#!/usr/bin/env python3
"""vw-backup — backup, prune, restore-drill, and restore for the Vaultwarden stack.

Design: docs/implementation-plan.md item 1, CONTEXT.md glossary, docs/adr/0001.
Stdlib only (Python 3.11+). External binaries: age (required), rclone (remote ship,
optional), docker (drill only).

Commands:
  backup   Build a full Backup Set, encrypt with age, ship via rclone, prune, ping.
  prune    Apply retention tiers (local + remote) without taking a backup.
  drill    Weekly automated Restore Drill. Default mode restores the plaintext
           staging tar built fresh this run (no private key on host, per ADR-0001)
           and verifies the latest encrypted archive exists locally/remotely.
           With [drill].identity_file set, decrypts the latest archive instead.
  restore  Decrypt + extract an archive for a real restore (needs escrow key).
"""

import argparse
import json
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ARCHIVE_PREFIX = "vw-backup-"
ARCHIVE_SUFFIX = ".tar.gz.age"
TIME_FMT = "%Y%m%dT%H%M%SZ"
AGE_HEADER = b"age-encryption.org/v1"


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')}] {msg}", flush=True)


def fail(msg: str) -> "NoReturn":  # noqa: F821
    log(f"ERROR: {msg}")
    sys.exit(1)


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def load_config(path: Path) -> dict:
    if not path.exists():
        fail(f"config not found: {path} (copy scripts/backup.toml.example)")
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    base = path.resolve().parent.parent  # repo root (config lives in scripts/)
    cfg["_base"] = base
    if not cfg.get("encryption", {}).get("age_recipients"):
        fail("encryption.age_recipients is empty — set at least the escrow public key")
    return cfg


def resolve(cfg: dict, p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else cfg["_base"] / path


def ping(url: str | None, ok: bool = True) -> None:
    if not url:
        return
    target = url if ok else url.rstrip("/") + "/fail"
    try:
        urllib.request.urlopen(target, timeout=10)
    except OSError as e:
        log(f"WARN: healthcheck ping failed ({target}): {e}")


# ---------------------------------------------------------------- backup set

def snapshot_db(db_path: Path, dest: Path) -> None:
    """Online-consistent SQLite snapshot via the backup API — no downtime."""
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(dest)
        with dst:
            src.backup(dst)
        dst.close()
    finally:
        src.close()


def db_counts(db_path: Path) -> dict:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        counts = {}
        for table in ("users", "ciphers", "organizations", "attachments"):
            counts[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return counts
    finally:
        con.close()


def build_backup_set(cfg: dict, workdir: Path, stamp: str) -> Path:
    """Full Backup Set (CONTEXT.md): DB snapshot + attachments + sends + rsa_key*
    + config files + manifest. Returns path to plaintext tar.gz."""
    data_dir = resolve(cfg, cfg["paths"]["data_dir"])
    db_path = data_dir / "db.sqlite3"
    if not db_path.exists():
        fail(f"database not found: {db_path}")

    db_snap = workdir / "db.sqlite3"
    snapshot_db(db_path, db_snap)
    check = sqlite3.connect(db_snap).execute("PRAGMA integrity_check").fetchone()[0]
    if check != "ok":
        fail(f"snapshot failed integrity_check: {check}")

    manifest = {
        "created_at": stamp,
        "counts": db_counts(db_snap),
        "members": [],
    }

    tar_path = workdir / f"{ARCHIVE_PREFIX}{stamp}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        def add(src: Path, arcname: str, required: bool = False):
            if src.exists():
                tar.add(src, arcname=arcname)
                manifest["members"].append(arcname)
            elif required:
                fail(f"required backup member missing: {src}")
            else:
                log(f"skip (absent): {src}")

        add(db_snap, "data/db.sqlite3", required=True)
        for key_file in sorted(data_dir.glob("rsa_key*")):
            add(key_file, f"data/{key_file.name}", required=True)
        if not any(m.startswith("data/rsa_key") for m in manifest["members"]):
            fail("no rsa_key* files found — refusing to produce a set that cannot restore logins")
        add(data_dir / "attachments", "data/attachments")
        add(data_dir / "sends", "data/sends")
        for cf in cfg["paths"].get("config_files", []):
            add(resolve(cfg, cf), f"config/{Path(cf).name}")

        mf = workdir / "manifest.json"
        mf.write_text(json.dumps(manifest, indent=2))
        tar.add(mf, arcname="manifest.json")

    log(f"backup set built: {tar_path.name} "
        f"({tar_path.stat().st_size // 1024} KiB, counts={manifest['counts']})")
    return tar_path


def encrypt(cfg: dict, tar_path: Path, out_path: Path) -> None:
    age = cfg.get("encryption", {}).get("age_binary", "age")
    cmd = [age, "-e"]
    for r in cfg["encryption"]["age_recipients"]:
        cmd += ["-r", r]
    cmd += ["-o", str(out_path), str(tar_path)]
    run(cmd)
    with open(out_path, "rb") as f:
        if not f.read(len(AGE_HEADER)).startswith(AGE_HEADER):
            fail("encrypted archive missing age header")


# ------------------------------------------------------------------- remote

def rclone_remote(cfg: dict) -> str | None:
    return cfg.get("remote", {}).get("rclone_remote") or None


def remote_list(remote: str) -> dict[str, int]:
    out = run(["rclone", "lsjson", remote]).stdout
    return {e["Name"]: e["Size"] for e in json.loads(out)
            if e["Name"].startswith(ARCHIVE_PREFIX)}


# ---------------------------------------------------------------- retention

def parse_stamp(name: str) -> datetime | None:
    if not (name.startswith(ARCHIVE_PREFIX) and name.endswith(ARCHIVE_SUFFIX)):
        return None
    raw = name[len(ARCHIVE_PREFIX):-len(ARCHIVE_SUFFIX)]
    try:
        return datetime.strptime(raw, TIME_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def compute_keep(names: list[str], cfg: dict, now: datetime) -> set[str]:
    """Retention Tiers (CONTEXT.md): every hourly for hourly_hours; earliest per
    UTC day up to daily_days; earliest per month up to monthly_months."""
    ret = cfg.get("retention", {})
    hourly = timedelta(hours=ret.get("hourly_hours", 48))
    daily = timedelta(days=ret.get("daily_days", 30))
    monthly = timedelta(days=30.5 * ret.get("monthly_months", 12))

    stamped = sorted((parse_stamp(n), n) for n in names if parse_stamp(n))
    keep: set[str] = set()
    seen_day: set[str] = set()
    seen_month: set[str] = set()
    for dt, name in stamped:
        age = now - dt
        if age <= hourly:
            keep.add(name)
        elif age <= daily:
            day = dt.strftime("%Y%m%d")
            if day not in seen_day:
                seen_day.add(day)
                keep.add(name)
        elif age <= monthly:
            month = dt.strftime("%Y%m")
            if month not in seen_month:
                seen_month.add(month)
                keep.add(name)
    return keep


def prune(cfg: dict) -> None:
    now = datetime.now(timezone.utc)
    archive_dir = resolve(cfg, cfg["paths"]["archive_dir"])
    local = [p.name for p in archive_dir.glob(f"{ARCHIVE_PREFIX}*{ARCHIVE_SUFFIX}")]
    keep = compute_keep(local, cfg, now)
    for name in sorted(set(local) - keep):
        (archive_dir / name).unlink()
        log(f"pruned local: {name}")

    remote = rclone_remote(cfg)
    if remote:
        rnames = list(remote_list(remote))
        rkeep = compute_keep(rnames, cfg, now)
        for name in sorted(set(rnames) - rkeep):
            run(["rclone", "deletefile", f"{remote}/{name}"])
            log(f"pruned remote: {name}")


# ------------------------------------------------------------------ backup

def cmd_backup(cfg: dict) -> None:
    hc = cfg.get("healthchecks", {}).get("backup_url")
    try:
        stamp = datetime.now(timezone.utc).strftime(TIME_FMT)
        archive_dir = resolve(cfg, cfg["paths"]["archive_dir"])
        archive_dir.mkdir(parents=True, exist_ok=True)
        staging = resolve(cfg, cfg["paths"]["staging_dir"])
        staging.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(dir=staging) as tmp:
            workdir = Path(tmp)
            tar_path = build_backup_set(cfg, workdir, stamp)
            out = archive_dir / f"{ARCHIVE_PREFIX}{stamp}{ARCHIVE_SUFFIX}"
            encrypt(cfg, tar_path, out)
        log(f"encrypted archive: {out.name} ({out.stat().st_size // 1024} KiB)")

        remote = rclone_remote(cfg)
        if remote:
            run(["rclone", "copyto", str(out), f"{remote}/{out.name}"])
            log(f"shipped to {remote}/{out.name}")
        else:
            log("WARN: no rclone_remote configured — archive is on this host only")

        prune(cfg)
        ping(hc)
        log("backup OK")
    except Exception:
        ping(hc, ok=False)
        raise


# ------------------------------------------------------------------- drill

def wait_alive(container: str, timeout: int = 60) -> bool:
    """Probe /alive via docker exec — works whether we run on the host or inside
    a sidecar container (no dependence on published ports/network topology)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        probe = subprocess.run(
            ["docker", "exec", container, "curl", "-fsS", "-m", "3",
             "http://localhost:80/alive"],
            capture_output=True)
        if probe.returncode == 0:
            return True
        time.sleep(2)
    return False


def drill_restore_checks(cfg: dict, tar_path: Path, workdir: Path) -> None:
    """Extract a Backup Set tar and prove it boots + data is sane."""
    extract = workdir / "extract"
    with tarfile.open(tar_path) as tar:
        tar.extractall(extract, filter="data")

    restored_db = extract / "data" / "db.sqlite3"
    if not restored_db.exists():
        fail("restored set has no data/db.sqlite3")
    if not list((extract / "data").glob("rsa_key*")):
        fail("restored set has no rsa_key* files")

    con = sqlite3.connect(f"file:{restored_db}?mode=ro", uri=True)
    integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    con.close()
    if integrity != "ok":
        fail(f"restored DB integrity_check: {integrity}")

    restored = db_counts(restored_db)
    if restored["users"] == 0:
        fail("restored DB has zero users")
    manifest = json.loads((extract / "manifest.json").read_text())
    if manifest["counts"] != restored:
        fail(f"manifest/restored count mismatch: {manifest['counts']} vs {restored}")

    live_db = resolve(cfg, cfg["paths"]["data_dir"]) / "db.sqlite3"
    if live_db.exists():
        live = db_counts(live_db)
        warn_pct = cfg.get("drill", {}).get("row_delta_warn_pct", 10)
        for table, live_n in live.items():
            delta = abs(live_n - restored[table])
            if live_n and delta * 100 / live_n > warn_pct:
                log(f"WARN: {table} delta {delta} vs live {live_n} exceeds {warn_pct}%")

    image = cfg.get("drill", {}).get("image", "vaultwarden/server:1.36.0")
    name = "vw-drill"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    # NOTE: when running inside the backup sidecar, this volume path must be
    # valid on the HOST too (docker-out-of-docker) — the compose file mounts
    # the repo at an identical path in the sidecar to guarantee that.
    run(["docker", "run", "-d", "--rm", "--name", name,
         "-v", f"{extract / 'data'}:/data",
         "-e", "SIGNUPS_ALLOWED=false", "-e", "INVITATIONS_ALLOWED=false",
         image])
    try:
        if not wait_alive(name):
            logs = subprocess.run(["docker", "logs", name],
                                  capture_output=True, text=True).stdout[-2000:]
            fail(f"scratch container never became healthy\n{logs}")
        # /alive alone would pass on an EMPTY /data (Vaultwarden creates a fresh
        # DB) — prove the mount actually delivered the restored set. Guards
        # against a broken host-path mapping in docker-out-of-docker mode.
        seen = subprocess.run(
            ["docker", "exec", name, "sh", "-c",
             "wc -c < /data/db.sqlite3"], capture_output=True, text=True)
        restored_size = restored_db.stat().st_size
        if seen.returncode != 0 or int(seen.stdout.strip() or 0) != restored_size:
            fail("scratch container does not see the restored db.sqlite3 "
                 f"(expected {restored_size} bytes, saw {seen.stdout.strip() or 'none'}) "
                 "— volume path mapping broken?")
        log(f"scratch container healthy with restored data, counts={restored}")
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def latest_archive(cfg: dict) -> tuple[str, Path | None]:
    """Newest archive name, preferring remote listing when configured."""
    remote = rclone_remote(cfg)
    if remote:
        names = sorted(remote_list(remote))
        if not names:
            fail(f"no archives found on remote {remote}")
        return names[-1], None
    archive_dir = resolve(cfg, cfg["paths"]["archive_dir"])
    files = sorted(archive_dir.glob(f"{ARCHIVE_PREFIX}*{ARCHIVE_SUFFIX}"))
    if not files:
        fail(f"no archives found in {archive_dir}")
    return files[-1].name, files[-1]


def cmd_drill(cfg: dict) -> None:
    hc = cfg.get("healthchecks", {}).get("drill_url")
    try:
        staging = resolve(cfg, cfg["paths"]["staging_dir"])
        staging.mkdir(parents=True, exist_ok=True)
        identity = cfg.get("drill", {}).get("identity_file")

        with tempfile.TemporaryDirectory(dir=staging) as tmp:
            workdir = Path(tmp)
            if identity:
                # Full path: fetch newest archive, decrypt with host identity.
                # Trade-off vs ADR-0001 documented in backup.toml.example.
                name, local = latest_archive(cfg)
                enc = local or workdir / name
                if not local:
                    run(["rclone", "copyto", f"{rclone_remote(cfg)}/{name}", str(enc)])
                tar_path = workdir / "restored.tar.gz"
                run([cfg.get("encryption", {}).get("age_binary", "age"),
                     "-d", "-i", str(resolve(cfg, identity)), "-o", str(tar_path), str(enc)])
                log(f"decrypted {name}")
            else:
                # Escrow-preserving path: restore a fresh plaintext set; verify the
                # newest encrypted archive exists and has the age header/size.
                stamp = datetime.now(timezone.utc).strftime(TIME_FMT)
                tar_path = build_backup_set(cfg, workdir, stamp)
                name, local = latest_archive(cfg)
                if local:
                    with open(local, "rb") as f:
                        if not f.read(len(AGE_HEADER)).startswith(AGE_HEADER):
                            fail(f"latest archive {name} missing age header")
                log(f"latest encrypted archive present: {name}")

            drill_restore_checks(cfg, tar_path, workdir)

        ping(hc)
        log("drill OK")
    except Exception:
        ping(hc, ok=False)
        raise


# ----------------------------------------------------------------- restore

def cmd_restore(cfg: dict, archive: str, identity: str, output: str) -> None:
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    tar_path = out / "backup-set.tar.gz"
    run([cfg.get("encryption", {}).get("age_binary", "age"),
         "-d", "-i", identity, "-o", str(tar_path), archive])
    with tarfile.open(tar_path) as tar:
        tar.extractall(out, filter="data")
    tar_path.unlink()
    counts = db_counts(out / "data" / "db.sqlite3")
    log(f"extracted to {out} (counts={counts})")
    print(
        "\nNext steps (see docs/runbooks/backup-restore.md):\n"
        f"  1. Review {out}/manifest.json\n"
        f"  2. Stop the stack:        docker compose down\n"
        f"  3. Replace data dir:      mv vw-data vw-data.broken && mv {out}/data vw-data\n"
        f"  4. Restore config files from {out}/config/ as needed\n"
        "  5. Start + verify:        docker compose up -d && curl -k https://localhost/alive\n"
    )


# -------------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", default=None,
                    help="path to backup.toml (default: alongside this script)")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("backup")
    sub.add_parser("prune")
    sub.add_parser("drill")
    rp = sub.add_parser("restore")
    rp.add_argument("--archive", required=True, help="path to .tar.gz.age file")
    rp.add_argument("--identity", required=True, help="age identity (escrow private key) file")
    rp.add_argument("--output", default="./restore-out")
    args = ap.parse_args()

    cfg_path = Path(args.config) if args.config else Path(__file__).parent / "backup.toml"
    cfg = load_config(cfg_path)

    if args.command == "backup":
        cmd_backup(cfg)
    elif args.command == "prune":
        prune(cfg)
    elif args.command == "drill":
        cmd_drill(cfg)
    elif args.command == "restore":
        cmd_restore(cfg, args.archive, args.identity, args.output)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        fail(f"command failed: {' '.join(e.cmd)}\n{e.stderr}")
