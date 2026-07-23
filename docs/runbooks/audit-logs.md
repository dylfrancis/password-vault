# Runbook: Audit Logs

Tool: `scripts/vw_audit.py` (stdlib Python; reads the DB read-only + app log).
Closes the free-tier gap: Vaultwarden records org events and auth failures but has
no export API and only a bare web UI (Reporting tab).

## Where login/audit data actually lives

| Source | What it captures | Caveat |
|---|---|---|
| `event` table (DB) | Logins, failed logins, item create/edit/delete, sharing, membership, policy changes — for **org members** | Needs `ORG_EVENTS_ENABLED=true` (on since day 1). Personal-vault item events are never recorded (architectural) |
| `vaultwarden.log` | Failed logins incl. **unknown usernames and non-org accounts** with IP | Needs `EXTENDED_LOGGING` + `LOG_FILE` (both set) |
| Caddy access log (`logs/caddy/`) | Every API request: IP, URI, status, timing | JSON, 12-month roll; no usernames |

Client IPs are real (Caddy forwards `X-Real-IP`; without it everything logs the
proxy container's IP).

## Commands

```bash
# who logged in / failed to, last 7 days
python3 scripts/vw_audit.py logins
python3 scripts/vw_audit.py logins --failed --since 1

# every decoded event (membership changes, item edits, policy updates...)
python3 scripts/vw_audit.py events --since 30

# counts by type — access-review / evidence snapshot
python3 scripts/vw_audit.py summary --since 90

# incremental SIEM export (cron runs this every 15 min in the sidecar)
python3 scripts/vw_audit.py export
```

Run inside the sidecar on a server: `docker compose exec backup sh -c 'cd "$REPO_DIR" && python3 scripts/vw_audit.py logins'`

## SIEM pickup

`export` writes JSONL files to `backups/audit-spool/` and keeps a cursor in
`backups/audit-state.json` — each run emits only new records, duplicate-safe,
log-rotation-aware. Point any shipper (Promtail, Vector, rclone-to-bucket, or the
future SIEM's agent) at the spool directory. Records carry `source`
(`vaultwarden.event` / `vaultwarden.log`), ISO8601 `time`, `type`, `user`, `ip`, `client`.

Spool files are covered by the backup tier's bucket if you add the spool to an
rclone sync; retention follows the log-retention policy (12 months default).

## SOC 2 mapping

- CC7.2 (monitoring): `logins --failed` + export-to-SIEM cover authentication
  monitoring; Caddy access logs cover request-level trails.
- CC4.1 (evidence): quarterly `summary` + `events` snapshots into the evidence
  repo; spool JSONL is the machine-readable trail.
- Known limit (documented, accepted): per-item *read* auditing is impossible in
  any free tier — clients sync the full encrypted vault. Compensating controls:
  shared credentials live in org collections (where write/share events ARE
  logged), plus offboarding rotation (plan item 3).
