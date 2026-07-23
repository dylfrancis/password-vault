# Context: Vaultwarden Password Vault

Domain language for this deployment. See `docs/vault-comparison.md` (selection) and `docs/implementation-plan.md` (scope).

## Glossary

**Backup Set** — the complete restorable unit: SQLite DB snapshot, `attachments/`, `sends/`, `rsa_key*`, `.env`, `Caddyfile`, `docker-compose.yml`. Every backup run produces a full Backup Set (no DB-only tiers) so the RPO holds for attachments, not just DB rows.

**RPO (this deployment)** — two-tier (ADR-0002): Litestream streams every DB write to an encrypted replica within seconds (items tier); full Backup Sets every 15 minutes via cron (authoritative tier — the only one carrying attachments, keys, and config). Interval configurable in `scripts/backup-crontab`.

**Retention Tiers** — every hourly Backup Set kept 48h; one promoted daily kept 30 days; one promoted monthly kept 12 months (matches the 365-day event-log retention). Script prunes; S3 lifecycle rule is a backstop, not the mechanism.

**Backup Destination** — local staging directory on the vault host plus an S3-compatible bucket reached via rclone. The rclone remote name is configuration; the org picks the provider.

**Escrow Officer** — one of two named people who each hold a copy of the age private key *outside* the company vault (personal safe / personal password manager). Two officers so no single person's unavailability blocks a restore. The company vault must never be the only home of this key — it decrypts the vault's own backups.

**Restore Drill** — automated weekly: decrypt the latest Backup Set, launch a scratch Vaultwarden container on a throwaway port, assert `/alive`, `PRAGMA integrity_check`, sane user/cipher row counts, `rsa_key*` present. Quarterly, a human additionally walks the full runbook including key retrieval from an Escrow Officer — verifying the people path, not just the script.

**Dead-man Switch** — backup and drill jobs ping healthchecks.io on success; alerts fire when pings *stop*. Chosen over failure-emails because a dead host or dead cron sends nothing. Three checks: hourly backup, weekly drill, `/alive` uptime probe.

## Invariants

- A Backup Set that has never passed a Restore Drill is not considered a backup.
- The age private key never lives inside the vault it restores.
- Losing `rsa_key*` + DB together with no backup = permanent, unrecoverable lockout for all users (E2E design). This is why backup is scope item #1.
