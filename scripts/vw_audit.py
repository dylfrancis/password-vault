#!/usr/bin/env python3
"""vw-audit — audit log views and SIEM export for Vaultwarden.

Closes the free-tier audit gap (implementation plan item 2, SOC 2 CC7.2/CC4.1):
Vaultwarden stores org events in the DB (ORG_EVENTS_ENABLED=true) and logs
auth failures to its log file, but ships no export API and only a bare web UI.

Reads the SQLite DB read-only and the app log; writes nothing to either.
Stdlib only (Python 3.11+).

Commands:
  logins    Login activity (successes + failures) from the event table,
            enriched with failed attempts parsed from vaultwarden.log
            (covers users outside any org too).
  events    Full decoded org event stream.
  summary   Counts by event type — quick access-review / memo view.
  export    Incremental JSONL export for SIEM pickup. Cursor state on disk;
            safe to run from cron; each run emits only new records.
"""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Bitwarden event type enum (subset Vaultwarden emits) — names match upstream.
EVENT_TYPES = {
    1000: "user_logged_in",
    1001: "user_changed_password",
    1002: "user_updated_2fa",
    1003: "user_disabled_2fa",
    1005: "user_failed_login",
    1006: "user_failed_login_2fa",
    1007: "user_exported_vault",
    1100: "cipher_created",
    1101: "cipher_updated",
    1102: "cipher_deleted",
    1103: "cipher_attachment_created",
    1104: "cipher_attachment_deleted",
    1105: "cipher_shared",
    1106: "cipher_updated_collections",
    1111: "cipher_client_copied_password",
    1114: "cipher_client_autofilled",
    1115: "cipher_soft_deleted",
    1116: "cipher_restored",
    1300: "collection_created",
    1301: "collection_updated",
    1302: "collection_deleted",
    1400: "group_created",
    1401: "group_updated",
    1402: "group_deleted",
    1500: "org_user_invited",
    1501: "org_user_confirmed",
    1502: "org_user_updated",
    1503: "org_user_removed",
    1508: "org_user_admin_reset_password",
    1511: "org_user_revoked",
    1512: "org_user_restored",
    1600: "org_updated",
    1601: "org_purged_vault",
    1700: "policy_updated",
}

DEVICE_TYPES = {
    0: "android", 1: "ios", 2: "chrome-ext", 3: "firefox-ext", 4: "opera-ext",
    5: "edge-ext", 6: "windows-desktop", 7: "macos-desktop", 8: "linux-desktop",
    9: "chrome", 10: "firefox", 11: "opera", 12: "edge", 14: "unknown-browser",
    17: "safari", 21: "sdk", 22: "server", 23: "windows-cli", 24: "macos-cli",
    25: "linux-cli",
}

LOGIN_TYPES = (1000, 1005, 1006)

# vaultwarden.log failed-auth line, e.g.:
# [2026-07-23 16:21:44.020][vaultwarden::api::identity][ERROR] Username or
#   password is incorrect. Try again. IP: 1.2.3.4. Username: user@example.com.
FAILED_LOG_RE = re.compile(
    r"\[(?P<ts>[\d\- :.]+)\]\[vaultwarden::api::identity\]\[ERROR\] "
    r"Username or password is incorrect. Try again. "
    r"IP: (?P<ip>[^.]+(?:\.[^. ]+)*)\. Username: (?P<user>\S+?)\.$"
)


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        sys.exit(f"ERROR: database not found: {db_path}")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def user_map(con: sqlite3.Connection) -> dict:
    return {r["uuid"]: r["email"] for r in con.execute("SELECT uuid, email FROM users")}


def fetch_events(con: sqlite3.Connection, since: datetime,
                 types: tuple | None = None) -> list[dict]:
    users = user_map(con)
    orgs = {r["uuid"]: r["name"] for r in con.execute("SELECT uuid, name FROM organizations")}
    q = "SELECT * FROM event WHERE event_date >= ?"
    args: list = [since.strftime("%Y-%m-%d %H:%M:%S")]
    if types:
        q += f" AND event_type IN ({','.join('?' * len(types))})"
        args += list(types)
    q += " ORDER BY event_date"
    out = []
    for r in con.execute(q, args):
        out.append({
            "time": r["event_date"][:19].replace(" ", "T") + "Z",
            "type": EVENT_TYPES.get(r["event_type"], f"unknown_{r['event_type']}"),
            "type_code": r["event_type"],
            "user": users.get(r["user_uuid"] or r["act_user_uuid"], ""),
            "acting_user": users.get(r["act_user_uuid"], ""),
            "org": orgs.get(r["org_uuid"], ""),
            "ip": r["ip_address"] or "",
            "client": DEVICE_TYPES.get(r["device_type"], str(r["device_type"] or "")),
            "cipher_uuid": r["cipher_uuid"] or "",
            "collection_uuid": r["collection_uuid"] or "",
            "event_uuid": r["uuid"],
        })
    return out


def parse_failed_log(log_path: Path, offset: int = 0) -> tuple[list[dict], int]:
    """Failed-auth lines from vaultwarden.log starting at byte offset.
    Returns (records, new_offset). Catches attempts against non-org accounts
    and unknown usernames — neither ever reaches the event table."""
    if not log_path.exists():
        return [], 0
    size = log_path.stat().st_size
    if size < offset:            # rotated/truncated — start over
        offset = 0
    records = []
    with open(log_path, errors="replace") as f:
        f.seek(offset)
        for line in f:
            m = FAILED_LOG_RE.search(line.strip())
            if m:
                records.append({
                    "time": m["ts"][:19].replace(" ", "T") + "Z",
                    "type": "user_failed_login",
                    "source": "vaultwarden.log",
                    "user": m["user"],
                    "ip": m["ip"],
                })
        new_offset = f.tell()
    return records, new_offset


def print_table(rows: list[dict], cols: list[str]) -> None:
    if not rows:
        print("(no records)")
        return
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    print("  ".join(c.upper().ljust(widths[c]) for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))


def since_arg(days: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


# ---------------------------------------------------------------- commands

def cmd_logins(con, args) -> None:
    events = fetch_events(con, since_arg(args.since), LOGIN_TYPES)
    # De-dup: Vaultwarden writes a user-level row and one per org membership
    # for the same login — keep one per (time, user, type).
    seen, rows = set(), []
    for e in events:
        key = (e["time"], e["user"], e["type_code"])
        if key not in seen:
            seen.add(key)
            rows.append(e)
    log_failures, _ = parse_failed_log(Path(args.log_file))
    cutoff = since_arg(args.since).strftime("%Y-%m-%dT%H:%M:%S")
    known = {(r["time"], r["user"]) for r in rows if r["type_code"] in (1005, 1006)}
    for lf in log_failures:
        if lf["time"] >= cutoff and (lf["time"], lf["user"]) not in known:
            rows.append({**lf, "client": "", "org": "(log)"})
    rows.sort(key=lambda r: r["time"])
    if args.failed:
        rows = [r for r in rows if "failed" in r["type"]]
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print_table(rows, ["time", "type", "user", "ip", "client"])


def cmd_events(con, args) -> None:
    rows = fetch_events(con, since_arg(args.since))
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print_table(rows, ["time", "type", "acting_user", "user", "org", "ip", "client"])


def cmd_summary(con, args) -> None:
    since = since_arg(args.since)
    rows = con.execute(
        "SELECT event_type, COUNT(*) n FROM event WHERE event_date >= ? "
        "GROUP BY event_type ORDER BY n DESC",
        [since.strftime("%Y-%m-%d %H:%M:%S")]).fetchall()
    total = sum(r["n"] for r in rows)
    print(f"events since {since.date()}: {total}")
    for r in rows:
        name = EVENT_TYPES.get(r["event_type"], f"unknown_{r['event_type']}")
        print(f"  {r['n']:>6}  {name}")


def cmd_export(con, args) -> None:
    """Incremental JSONL for SIEM pickup. State = last event date+uuid, log offset."""
    state_path = Path(args.state)
    spool_dir = Path(args.spool)
    spool_dir.mkdir(parents=True, exist_ok=True)
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        "last_event_date": "1970-01-01 00:00:00", "exported_uuids": [], "log_offset": 0}

    rows = con.execute(
        "SELECT * FROM event WHERE event_date >= ? ORDER BY event_date",
        [state["last_event_date"]]).fetchall()
    users = user_map(con)
    already = set(state["exported_uuids"])
    records = []
    for r in rows:
        if r["uuid"] in already:
            continue
        records.append({
            "source": "vaultwarden.event",
            "time": r["event_date"][:19].replace(" ", "T") + "Z",
            "type": EVENT_TYPES.get(r["event_type"], f"unknown_{r['event_type']}"),
            "type_code": r["event_type"],
            "user": users.get(r["user_uuid"] or r["act_user_uuid"], ""),
            "acting_user": users.get(r["act_user_uuid"], ""),
            "org_uuid": r["org_uuid"] or "",
            "ip": r["ip_address"] or "",
            "client": DEVICE_TYPES.get(r["device_type"], str(r["device_type"] or "")),
            "cipher_uuid": r["cipher_uuid"] or "",
            "event_uuid": r["uuid"],
        })

    log_records, new_offset = parse_failed_log(Path(args.log_file), state["log_offset"])

    if not records and not log_records:
        print("export: nothing new")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = spool_dir / f"vw-audit-{stamp}.jsonl"
    with open(out, "w") as f:
        for rec in records + log_records:
            f.write(json.dumps(rec) + "\n")

    # Cursor: keep uuids sharing the max date (sub-second ties), prune the rest.
    if rows:
        max_date = rows[-1]["event_date"]
        state["last_event_date"] = max_date
        state["exported_uuids"] = [
            r["uuid"] for r in rows if r["event_date"] == max_date]
    state["log_offset"] = new_offset
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state))
    print(f"export: {len(records)} events + {len(log_records)} log records -> {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="vw-data/db.sqlite3")
    ap.add_argument("--log-file", default="vw-data/vaultwarden.log")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("logins")
    p.add_argument("--since", type=float, default=7, help="days back (default 7)")
    p.add_argument("--failed", action="store_true", help="failures only")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("events")
    p.add_argument("--since", type=float, default=7)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("summary")
    p.add_argument("--since", type=float, default=30)

    p = sub.add_parser("export")
    p.add_argument("--spool", default="backups/audit-spool")
    p.add_argument("--state", default="backups/audit-state.json")

    args = ap.parse_args()
    con = connect(Path(args.db))
    {"logins": cmd_logins, "events": cmd_events,
     "summary": cmd_summary, "export": cmd_export}[args.command](con, args)


if __name__ == "__main__":
    main()
