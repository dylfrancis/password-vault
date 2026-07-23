#!/usr/bin/env python3
"""vw-access — access-review matrix and offboarding rotation report.

Implementation plan items 3 + 4 (SOC 2 CC6.2/CC6.3 offboarding, CC6.1-6.3
access reviews). Reads the SQLite DB read-only; stdlib only (Python 3.11+).

Commands:
  matrix    User x role x collection permission grid. Quarterly access-review
            evidence in one command.
  offboard  Exposure report for a departing user: everything they could have
            seen, as a rotation checklist with web-vault deep links.
            RUN THIS BEFORE REMOVING THE ACCOUNT — removal deletes the
            permission rows this report is built from.

E2E caveat: item and collection names are encrypted; the server cannot print
them. Reports use web-vault deep links (your session decrypts on click) and an
optional operator-maintained label file for collections:
  scripts/vault-labels.toml ->  [collections]
                                "<collection-uuid>" = "Engineering"
"""

import argparse
import json
import sqlite3
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

ROLES = {0: "owner", 1: "admin", 2: "user", 3: "manager"}
STATUS = {-1: "revoked", 0: "invited", 1: "accepted", 2: "confirmed"}


def connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        sys.exit(f"ERROR: database not found: {db_path}")
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def load_labels(path: Path) -> dict:
    if path.exists():
        with open(path, "rb") as f:
            return tomllib.load(f).get("collections", {})
    return {}


def domain_from_env(env_path: Path) -> str:
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("DOMAIN="):
                return line.split("=", 1)[1].strip()
    return "https://localhost"


def collection_label(uuid: str, labels: dict) -> str:
    return labels.get(uuid, f"collection {uuid[:8]}… (add label in vault-labels.toml)")


def org_graph(con: sqlite3.Connection) -> dict:
    """Permission graph: orgs -> collections -> cipher counts; memberships."""
    orgs = {r["uuid"]: r["name"] for r in con.execute("SELECT uuid, name FROM organizations")}
    collections = {}   # org_uuid -> [collection_uuid]
    for r in con.execute("SELECT uuid, org_uuid FROM collections"):
        collections.setdefault(r["org_uuid"], []).append(r["uuid"])
    ciphers = {}       # collection_uuid -> [cipher_uuid]
    for r in con.execute("SELECT cipher_uuid, collection_uuid FROM ciphers_collections"):
        ciphers.setdefault(r["collection_uuid"], []).append(r["cipher_uuid"])
    return {"orgs": orgs, "collections": collections, "ciphers": ciphers}


# ------------------------------------------------------------------ matrix

def cmd_matrix(con, args) -> None:
    labels = load_labels(Path(args.labels))
    g = org_graph(con)
    rows = []
    for m in con.execute(
            "SELECT uo.*, u.email FROM users_organizations uo "
            "JOIN users u ON u.uuid = uo.user_uuid ORDER BY u.email"):
        org_cols = g["collections"].get(m["org_uuid"], [])
        if m["access_all"] or m["atype"] in (0, 1):
            scope = [(c, "full") for c in org_cols]
            scope_desc = f"ALL {len(org_cols)} collections (role/access_all)"
        else:
            assigned = list(con.execute(
                "SELECT * FROM users_collections WHERE user_uuid = ?", [m["user_uuid"]]))
            scope = [(a["collection_uuid"],
                      "read-only" if a["read_only"] else "read-write") for a in assigned]
            scope_desc = ", ".join(
                f"{collection_label(c, labels)} [{mode}]" for c, mode in scope) or "(none)"
        items = sum(len(g["ciphers"].get(c, [])) for c, _ in scope)
        rows.append({
            "user": m["email"],
            "org": g["orgs"].get(m["org_uuid"], m["org_uuid"][:8]),
            "role": ROLES.get(m["atype"], str(m["atype"])),
            "status": STATUS.get(m["status"], str(m["status"])),
            "items_reachable": items,
            "collections": scope_desc,
        })
    if args.csv:
        import csv
        w = csv.DictWriter(sys.stdout, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        print(f"# Access review — {stamp}\n")
        print("| user | org | role | status | items reachable | collections |")
        print("|---|---|---|---|---|---|")
        for r in rows:
            print(f"| {r['user']} | {r['org']} | {r['role']} | {r['status']} "
                  f"| {r['items_reachable']} | {r['collections']} |")
        print("\nReviewed by: ____________  Date: ____________  "
              "Changes required: ____________")


# ---------------------------------------------------------------- offboard

def cmd_offboard(con, args) -> None:
    labels = load_labels(Path(args.labels))
    domain = domain_from_env(Path(args.env_file))
    g = org_graph(con)
    user = con.execute("SELECT * FROM users WHERE email = ?", [args.email]).fetchone()
    if not user:
        sys.exit(f"ERROR: no user with email {args.email}")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [f"# Offboarding rotation report — {args.email}",
           f"Generated: {stamp}  |  Operator: ____________",
           "",
           "Order of operations: 1) this report  2) revoke/remove the account "
           "3) rotate everything below  4) file this document as evidence.",
           ""]

    memberships = list(con.execute(
        "SELECT * FROM users_organizations WHERE user_uuid = ?", [user["uuid"]]))
    if not memberships:
        out.append("User belongs to no organizations — no shared credentials exposed.")

    total_items = 0
    for m in memberships:
        org_name = g["orgs"].get(m["org_uuid"], m["org_uuid"][:8])
        role = ROLES.get(m["atype"], str(m["atype"]))
        status = STATUS.get(m["status"], str(m["status"]))
        out.append(f"## Organization: {org_name} (role: {role}, status: {status})")
        out.append("")

        if m["status"] < 2:
            out.append("Status is **" + status + "** — the user never received the "
                       "org key, so they could not decrypt any shared item. "
                       "**Nothing to rotate.** Just remove the invitation.")
            out.append("")
            continue

        if m["access_all"] or m["atype"] in (0, 1):
            scope = g["collections"].get(m["org_uuid"], [])
            out.append(f"Access was **organization-wide** ({role}"
                       f"{', access_all' if m['access_all'] else ''}) — every item "
                       "in every collection must be treated as exposed.")
        else:
            scope = [r["collection_uuid"] for r in con.execute(
                "SELECT collection_uuid FROM users_collections WHERE user_uuid = ?",
                [user["uuid"]])]
            out.append(f"Access was limited to {len(scope)} assigned collection(s). "
                       "(`hide_passwords` offers no protection — client-side only — "
                       "those items are included.)")
        out.append("")

        for c in scope:
            items = g["ciphers"].get(c, [])
            total_items += len(items)
            out.append(f"### {collection_label(c, labels)} — {len(items)} item(s)")
            for cipher in items:
                out.append(f"- [ ] rotate: {domain}/#/vault?itemId={cipher}")
            if not items:
                out.append("- (empty collection)")
            out.append("")

    ea = list(con.execute(
        "SELECT * FROM emergency_access WHERE grantor_uuid = ? OR grantee_uuid = ? "
        "OR email = ?", [user["uuid"], user["uuid"], args.email]))
    if ea:
        out.append("## Emergency access involving this user")
        for r in ea:
            direction = "GRANTOR (others can take over their vault)" \
                if r["grantor_uuid"] == user["uuid"] else "GRANTEE (they can take over someone's vault)"
            out.append(f"- [ ] review/remove grant: {direction}, "
                       f"type={'takeover' if r['atype'] else 'view'}, created {r['created_at'][:10]}")
        out.append("")

    out.append("## Also")
    out.append("- [ ] Personal vault leaves with the user (their data, their key) — no org action.")
    out.append("- [ ] Deauthorize sessions / delete user in the admin panel (or via invite removal).")
    out.append(f"- [ ] Confirm rotation complete: {total_items} shared item(s) total.")

    report = "\n".join(out)
    if args.output:
        Path(args.output).write_text(report)
        print(f"written: {args.output} ({total_items} items to rotate)")
    else:
        print(report)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="vw-data/db.sqlite3")
    ap.add_argument("--labels", default="scripts/vault-labels.toml")
    ap.add_argument("--env-file", default=".env")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("matrix")
    p.add_argument("--csv", action="store_true")

    p = sub.add_parser("offboard")
    p.add_argument("--email", required=True)
    p.add_argument("--output", help="write markdown here instead of stdout")

    args = ap.parse_args()
    con = connect(Path(args.db))
    {"matrix": cmd_matrix, "offboard": cmd_offboard}[args.command](con, args)


if __name__ == "__main__":
    main()
