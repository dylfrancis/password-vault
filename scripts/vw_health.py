#!/usr/bin/env python3
"""vw-health — liveness + freshness checks for the vault stack (plan item 5).

Checks:
  1. Vaultwarden answers /alive
  2. Newest backup archive is fresh (default: < 35 min, i.e. 2x the 15-min cron
     + grace)
  3. Litestream replica has written something within 25h (its snapshot cadence;
     WAL segments only appear on DB writes, so a tight threshold would
     false-alarm on quiet days)
  4. Disk has headroom

Exit 0 all-OK / 1 any-FAIL. If [healthchecks].alive_url is set in backup.toml,
pings it on success and <url>/fail on failure (dead-man switch). Designed for
the sidecar cron (every 5 min) but runs identically from the host.
"""

import json
import ssl
import sys
import shutil
import time
import tomllib
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
BACKUP_MAX_AGE_MIN = 35
REPLICA_MAX_AGE_H = 25
MIN_FREE_GB = 1.0
ALIVE_URLS = ["http://vaultwarden:80/alive", "https://localhost/alive"]


def check_alive() -> tuple[bool, str]:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE          # self-signed local certs are fine here
    for url in ALIVE_URLS:
        try:
            with urllib.request.urlopen(url, timeout=5, context=ctx) as r:
                if r.status == 200:
                    return True, f"alive: OK ({url})"
        except OSError:
            continue
    return False, f"alive: FAIL — no /alive response from {ALIVE_URLS}"


def check_backup_fresh() -> tuple[bool, str]:
    archive_dir = BASE / "backups" / "archive"
    archives = sorted(archive_dir.glob("vw-backup-*.tar.gz.age"),
                      key=lambda p: p.stat().st_mtime)
    if not archives:
        return False, "backup: FAIL — no archives found"
    age_min = (time.time() - archives[-1].stat().st_mtime) / 60
    ok = age_min <= BACKUP_MAX_AGE_MIN
    return ok, (f"backup: {'OK' if ok else 'FAIL'} — newest archive "
                f"{archives[-1].name} is {age_min:.0f} min old "
                f"(limit {BACKUP_MAX_AGE_MIN})")


def check_replica_fresh() -> tuple[bool, str]:
    replica = BASE / "backups" / "litestream"
    if not replica.exists():
        return True, "litestream: SKIP — no local replica dir (S3 mode or disabled)"
    newest = max((p.stat().st_mtime for p in replica.rglob("*") if p.is_file()),
                 default=0)
    if not newest:
        return False, "litestream: FAIL — replica dir empty"
    age_h = (time.time() - newest) / 3600
    ok = age_h <= REPLICA_MAX_AGE_H
    return ok, (f"litestream: {'OK' if ok else 'FAIL'} — last replica write "
                f"{age_h:.1f}h ago (limit {REPLICA_MAX_AGE_H}h)")


def check_disk() -> tuple[bool, str]:
    free_gb = shutil.disk_usage(BASE).free / 1e9
    ok = free_gb >= MIN_FREE_GB
    return ok, f"disk: {'OK' if ok else 'FAIL'} — {free_gb:.1f} GB free (min {MIN_FREE_GB})"


def ping(ok: bool) -> None:
    cfg_path = BASE / "scripts" / "backup.toml"
    if not cfg_path.exists():
        return
    with open(cfg_path, "rb") as f:
        url = tomllib.load(f).get("healthchecks", {}).get("alive_url")
    if not url:
        return
    target = url if ok else url.rstrip("/") + "/fail"
    try:
        urllib.request.urlopen(target, timeout=10)
    except OSError as e:
        print(f"WARN: dead-man ping failed: {e}")


def main() -> None:
    results = [check_alive(), check_backup_fresh(), check_replica_fresh(), check_disk()]
    all_ok = all(ok for ok, _ in results)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for _, msg in results:
        print(f"[{stamp}] {msg}")
    print(f"[{stamp}] health: {'OK' if all_ok else 'FAIL'}")
    ping(all_ok)
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
