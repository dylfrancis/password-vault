# Vaultwarden Implementation Plan (agreed scope)

Chosen solution: Vaultwarden — see `vault-comparison.md` for selection rationale.
Target: 10–50 users, self-hosted, $0 licensing, SOC 2-supportable.
Total effort: **1–2 hour core setup** plus a small extension toolkit (~2 hours), not a multi-week project.

## Status

| Step | State |
|---|---|
| Core stack (Docker Compose: Vaultwarden 1.36.0 + Caddy) | ✅ Done, validated locally at `https://localhost` |
| Hardened config (`.env.example` prod template, Caddyfile headers/admin-block/access logs, `.gitignore`) | ✅ Done |
| Extension toolkit (items 1–5 below) | ⏳ Next |
| Production cutover | ⏳ After toolkit |

## Agreed scope: the five extensions

Ranked by value per effort. 1–4 ship as one small toolkit (stdlib Python + bash, ~150 lines total); 5 is a cron one-liner. No Vaultwarden fork — everything reads the DB/API from outside, keeping the upgrade path clean.

### 1. Backup + restore verification (~30 min)
Highest value. E2E vault means data loss is unrecoverable by design — lose the DB or `rsa_key*` and every user is locked out permanently. Stock Vaultwarden ships nothing.
Build: script doing SQLite online backup (`.backup` API, consistent while running) + `rsa_key*` + attachments + config, encrypted, shipped offsite, pruned per retention, plus a restore test into a scratch container.

### 2. Audit log CLI — views + export (~30–40 min)
Vaultwarden writes org events to the DB (`ORG_EVENTS_ENABLED=true`, already on) but offers only a basic web UI and **no export API**.
Build: CLI with readable event views (logins, failed logins, membership/collection changes) and incremental JSONL export for future SIEM ingestion. Closes the SOC 2 CC7.2 evidence gap; this is the "log views" capability.

### 3. Offboarding rotation report (~20 min)
The differentiator. E2E model means a removed user already *saw* every secret they had access to — removing access revokes nothing. Neither Vaultwarden nor paid Bitwarden addresses this.
Build: given a departing user, emit a checklist of collections/items whose credentials need rotation.

### 4. Access-review matrix (~15 min)
Quarterly SOC 2 access-review evidence in one command.
Build: dump user × org role × collection permissions to markdown/CSV.

### 5. Liveness monitoring (~10 min)
`/alive` endpoint exists; nothing watches it.
Build: cron curl + healthchecks.io ping (same channel also catches backup-job failures). Explicitly NOT building Grafana/Loki — low marginal value at this scale.

## Explicitly out of scope

- **Grafana/Loki/Prometheus stack** — overkill at ≤50 users; item 2's CLI covers log review, item 5 covers uptime.
- **SSO wiring** — built-in OIDC config when an IdP decision lands; configuration, not custom work.
- **User-provisioning CLI** — Bitwarden Directory Connector exists free; manual invites fine at this scale.
- **Any Vaultwarden fork/patch** — kills the upgrade path.

## Accepted limitations (documented, not fought)

- Personal-vault events are never logged (architectural). Policy: work credentials live in org collections.
- Per-secret read auditing impossible in any free tier (clients sync the whole encrypted vault). If a customer audit ever demands it, that triggers the paid-tier conversation.
- Mobile push sync relays through Bitwarden's cloud (metadata only); optional registration at bitwarden.com/host, else poll-based sync.

## Remaining steps

1. Finish local validation (account, org, collection, invite flow, event log visible in Reporting tab).
2. Build toolkit items 1–5.
3. Production cutover: real `DOMAIN`, `SIGNUPS_ALLOWED=false`, Argon2 `ADMIN_TOKEN`, SMTP creds, DNS + ACME (automatic via Caddy), restore-drill once against prod backup.
4. Org policies: require MFA, master-password policy, admin password reset enabled.
5. Pilot with 2–3 users, then team rollout + 30-min onboarding.

## Architecture

```
 users ──► Caddy (TLS, JSON access log, /admin IP-blocked) ──► Vaultwarden (SQLite, event table, app log)
                                                                    │
             toolkit (cron/admin workstation): backup ── audit CLI ── offboard report ── access matrix
                                                                    │
             /alive ◄── cron probe ──► healthchecks.io (also pinged by backup job)
```
