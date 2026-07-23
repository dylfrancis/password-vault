# 0002 — Two-tier backup: Litestream WAL streaming + 15-minute Backup Sets

Status: accepted (2026-07-23)

## Context

Stakeholder clarification: vault contents are day-to-day operational credentials;
losing even a short window of changes is costly. The original 1h RPO is too wide.

Alternatives considered:
- **Shorter cron only (15 min):** simplest, but still a 15-minute loss window.
- **Migrate to PostgreSQL for streaming replication:** rejected — solves a
  concurrency problem we don't have at 10–50 users, adds a heavier ops surface
  (WAL archiving, base backups, upgrade dances), and forces a rewrite of the
  tested SQLite-based backup/drill tooling. Revisit triggers: hundreds of users,
  HA requirement, or an org-managed Postgres with provider PITR.

## Decision

Two independent tiers:

1. **Litestream sidecar** (`vw-litestream`, pinned image) streams the SQLite WAL
   to a replica continuously — RPO seconds-to-minutes for all vault items.
   Replica is age-encrypted to the same escrow recipients (ADR-0001 holds);
   72h retention, since it only bridges gaps between Backup Sets.
2. **Full Backup Sets every 15 minutes** (was hourly) — unchanged mechanism,
   escrow-encrypted, drilled weekly, retention ladder 48h/30d/12m. This remains
   the authoritative tier: it alone covers attachments, sends, RSA keys, config.

## Consequences

- Item edits are recoverable to within seconds; a full-set restore loses ≤15 min.
- Two restore paths now exist and both must stay exercised: the weekly drill
  covers Backup Sets; the quarterly manual drill must also run
  `litestream restore` (documented in the runbook).
- Litestream covers ONLY the database — never use it as the sole recovery source;
  attachments and server keys come from the Backup Set tier.
- Gotcha (found in testing): Litestream's `age.identities` config takes the
  secret key VALUE inline, not a file path — restore configs are therefore
  themselves secrets and must be built ad hoc and destroyed after use.
- New dependency (BSL-licensed Litestream binary, pinned) — acceptable: it is
  additive; if it dies, the 15-min tier stands alone.
