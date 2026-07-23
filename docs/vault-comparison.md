# Enterprise Password Vault — Options Comparison

Research date: 2026-07-23. All facts verified against current official docs/releases (sources linked inline).

## Requirements (from "Password Vault Challenge")

| # | Requirement |
|---|-------------|
| R1 | Completely free — no paid plans, expiring trials, or paywalled core features |
| R2 | Open-source, self-hosted preferred |
| R3 | Multi-user, suitable for 10–50 people |
| R4 | Secure sharing between users/teams |
| R5 | Access controls (roles, groups, granularity) |
| R6 | Logging / auditing basics |
| R7 | Strong encryption |
| R8 | Realistic, low-friction setup |
| R9 | SOC 2 compliance question answered |

## First: the SOC 2 framing (it changes the comparison)

SOC 2 is an attestation of an **organization's** controls, not a certification of a software product ([AICPA](https://www.aicpa-cima.com/topic/audit-assurance/audit-and-assurance-greater-than-soc-2), [Drata](https://drata.com/learn/soc-2/myths)). Passbolt SA holds a SOC 2 Type II report and Bitwarden holds SOC 2 Type II + ISO 27001 — but those cover *their* corporate/cloud environments. The moment you self-host, the vendor's report is only vendor-due-diligence evidence; every control around your deployment (access control, MFA, audit logging + retention, encryption, backup/DR, patching, offboarding) lands in **your** SOC 2 scope.

Practical consequence: no self-hosted option "is SOC 2 compliant" out of the box. The question becomes *which product gives you the features to satisfy the Trust Services Criteria for free* — and audit logging (CC7.2/CC4.1) is where the free tiers differ most.

## The field

Evaluated: Passbolt CE, Vaultwarden, Bitwarden (official), Psono CE, Nextcloud Passwords, TeamPass, sysPass, Padloc, KeePassXC shared-DB, Infisical, HashiCorp Vault, OpenBao, plus 2024–2026 newcomers (LibrePass, Passky).

### Quick disqualifications

| Option | Why out |
|--------|---------|
| **Bitwarden (official server)** | Free org capped at **2 users / 2 collections**; groups, event logs, SSO all paid even self-hosted. Not free at 10–50 seats — this gap is literally why Vaultwarden exists. [Plans](https://bitwarden.com/help/password-manager-plans/) |
| **sysPass** | Abandoned — last commit July 2022, unpatched CVEs since. Do not deploy. |
| **Padloc** | No commits since Dec 2023; commercial self-hosting requires contacting sales — fails "completely free". |
| **TeamPass** | Actively maintained and everything is free (RBAC, LDAP, audit), but **server-side crypto** (plaintext transits server memory) + long CVE history. A security tier below the E2E options. |
| **KeePass/KeePassXC shared .kdbx** | One master credential, all-or-nothing access, no per-user accounts/RBAC/audit, sync-conflict prone. Fine for 2–5 admins' break-glass vault, not a 10–50 user system. |
| **HashiCorp Vault** | BUSL license since 2023 — not open source. Also a machine-secrets manager, not a human password manager (no autofill, no personal vaults). |
| **Infisical / OpenBao** | Healthy projects, wrong category (machine secrets/CI). Complementary, not a substitute. |
| **Nextcloud Passwords** | Only viable if Nextcloud already runs. E2E is opt-in (server-side by default), sharing is per-item with no collections/RBAC. |
| **LibrePass, Passky** | Dead/stagnant since late 2024. |

**Real finalists: Passbolt CE, Vaultwarden, Psono CE.**

## Finalists vs requirements

| Requirement | Passbolt CE | Vaultwarden | Psono CE |
|---|---|---|---|
| R1 Free, no gates | ⚠️ Unlimited users free, but **audit log UI, SSO, LDAP sync, account recovery are all Pro** (~$4.90/user/mo) | ✅ Everything free — unlocks Bitwarden's paid features (orgs, collections, event logs, WebAuthn MFA, emergency access, admin password reset, OIDC SSO) | ⚠️ Unlimited users free; **SSO/LDAP and server-side audit log are EE** (EE free ≤10 users, then ~$3/user/mo) |
| R2 OSS / self-hosted | ✅ AGPL-3.0, commercial vendor behind it | ✅ AGPL-3.0, community project (unofficial Rust reimplementation of Bitwarden server) | ✅ Apache-2.0 |
| R3 Multi-user 10–50 | ✅ | ✅ | ✅ |
| R4 Secure sharing | ✅ Per-user OpenPGP re-encryption, signed ops | ✅ Org vault, O(1) sharing via org key | ✅ Shares/groups, E2E |
| R5 Access controls | ✅ Groups, folders, read/update/owner per resource (fine-grained roles are Pro) | ✅ Collections + Owner/Admin/User roles + org policies; groups behind `ORG_GROUPS_ENABLED` (beta) | ✅ Groups, datastores, folders |
| R6 Audit logging (free) | ❌ Activity-log UI is Pro-only. Data *is* written to the `action_logs` DB table in CE — query/export it yourself | ✅ Org event logs free: `ORG_EVENTS_ENABLED=true`, retention via `EVENTS_DAYS_RETAIN`. Gap: personal-vault events not logged | ❌ EE-only |
| R7 Encryption | ✅ E2E, per-user OpenPGP; encrypted metadata since v5.1; strongest revocation/attribution story | ✅ E2E Bitwarden protocol (Argon2id/PBKDF2 + AES-256); org key not rotated on member removal | ✅ E2E libsodium (Curve25519/XSalsa20-Poly1305) |
| R8 Setup friction | ⚠️ PHP + MariaDB + **mandatory SMTP** + NTP; 2 CPU/2 GB min. Official Docker compose | ✅ Single Rust binary, SQLite default, ~tens of MB RAM; official Docker image | ⚠️ Django + PostgreSQL, 3 containers (server, webclient, admin) |
| R9 SOC 2 support | Vendor has SOC 2 Type II + Cure53 audits ×N (best assurance pedigree). But CE's missing audit-log UI is the biggest gap for *your* audit | No attestations, no formal server audit (record as accepted risk under CC9.2). But free event logs directly feed CC7.2 evidence | Vendor ISO 27001 (2025) + Cure53 audits 2025 & 2026. Audit log paywalled |
| Clients | Extensions (Chrome/Firefox/Edge/Safari since 5.10), iOS/Android, CLI. Extension required — no pure-web access | **Official Bitwarden clients** — best-in-class extensions/mobile/CLI/autofill | Web client, extensions, iOS/Android, CLI |
| Security track record | Cure53 audits 2021–2026, SOC 2, CSPN pre-audit; CVEs low/medium, fast fixes | No formal audit of server code; CVE-2025-24364 (admin-panel RCE) + CVE-2025-24365 (priv-esc), fixed 1.33.0. Active: 1.36.0 May 2026 | Cure53 white-box 2025 + 2026, nothing above Low in 2026 |
| Key risks | Feature-gating drift (audit/SSO already paid); no account recovery in CE (lost user key = lost personal vault); heavier ops | Volunteer-maintained; depends on Bitwarden Inc.'s continued client compatibility + push relay (mobile push routes through Bitwarden cloud); harden/disable admin panel | Smaller community; audit log paywalled; EE free tier caps at 10 users |

## Where this lands

**Vaultwarden is the best fit for the requirements as literally written.** It is the only option where every "enterprise-ready" checklist item — unlimited users, collections, roles, event logging, WebAuthn MFA, admin account recovery, even OIDC SSO — is free. Ops footprint is minimal, and clients are the official Bitwarden apps (the best UX in the category, with client-side crypto that *is* audited). Trade-off you accept and document: the server is a community reimplementation with no formal audit and two serious 2025 CVEs (patched) — mitigate with prompt updates, admin panel off the internet, reverse-proxy log shipping.

**Passbolt CE is the pick if vendor assurance outweighs free features.** Deepest audit pedigree (Cure53 ×N, SOC 2 Type II vendor, CSPN pre-audit), commercial backing, and the strongest crypto model for attribution (per-user OpenPGP, signed operations). But for a compliance-minded deployment, CE withholds exactly the things auditors ask about — activity log UI, SSO, account recovery — and the practical answer to those gaps is the Pro tier, i.e. no longer free. (Partial workaround: CE still writes `action_logs` to the DB; cron an export to your SIEM.)

**Psono CE is the dark horse** — Apache-2.0, best recent audit cadence (Cure53 2025 and 2026), solid E2E. Same shape of catch as Passbolt: audit logs and SSO sit in EE. Worth noting: **EE is free for up to 10 users**, so a team at the bottom of the 10–50 range could run full EE (audit logs + SSO included) at $0.

### On the original Passbolt lean

Two corrections to the initial intuition:
1. **SOC 2**: Passbolt's SOC 2 report covers Passbolt SA, not your self-hosted instance. It's good vendor-diligence evidence but doesn't make your deployment compliant — and CE's paywalled audit log is actually the *hardest* free-tier gap to explain to your own auditor. Vaultwarden, despite zero attestations, ships the audit-logging feature free.
2. **Free**: Passbolt CE is genuinely free and unlimited, but the "enterprise" features the memo implies (auditing, SSO, recovery) are the Pro upsell.

### SOC 2 compensating controls (either choice)

- TLS-terminating reverse proxy (nginx/Caddy/Traefik) logging every authenticated API call; ship to SIEM; ≥12-month retention.
- Vaultwarden: `ORG_EVENTS_ENABLED=true`, `EVENTS_DAYS_RETAIN=365`, `EXTENDED_LOGGING=true` + syslog; keep shared credentials in org collections (where logging exists); Fail2Ban on failed logins.
- Passbolt CE: scheduled export of `action_logs`/`actions` tables to SIEM + retention purge job.
- Both: documented onboarding/offboarding procedure, quarterly access reviews, tested encrypted backups (DB + server keys — Passbolt's GPG server key and Vaultwarden's `rsa_key*` are unrecoverable if lost), patch SLA, MFA enforced via policy.
- Honest limit: per-secret *read* auditing isn't achievable in either free tier (Vaultwarden clients sync the whole encrypted vault; item-level read logging is architecturally impossible for personal vaults). If a customer audit demands it, that's the trigger to pay for Passbolt Pro or Bitwarden Enterprise.

## Recommendation

For this challenge: **Vaultwarden** as primary (meets every requirement at $0, lightest ops, best clients), with **Passbolt CE** as the documented runner-up if the org weighs vendor audits/commercial backing over free audit logging, and **Psono EE-free** flagged as the sleeper option for teams ≤10.

## Primary sources

- Passbolt: [pricing](https://www.passbolt.com/pricing/pro) · [security](https://www.passbolt.com/security) · [Docker install](https://www.passbolt.com/docs/hosting/install/ce/docker/) · [community threads on CE logs](https://community.passbolt.com/t/logs-in-passbolt/5850)
- Vaultwarden: [repo](https://github.com/dani-garcia/vaultwarden) · [wiki](https://github.com/dani-garcia/vaultwarden/wiki) · [1.36.0 release](https://github.com/dani-garcia/vaultwarden/releases/tag/1.36.0) · [BI.ZONE CVE writeup](https://bi-zone.medium.com/exploring-cve-2025-24364-and-cve-2025-24365-in-vaultwarden-562ee308270f) · [push notifications](https://github.com/dani-garcia/vaultwarden/wiki/Enabling-Mobile-Client-push-notification)
- Psono: [feature matrix](https://doc.psono.com/admin/overview/supported-features.html) · [Cure53 2026 audit](https://psono.com/blog/security-audit-2026) · [ISO 27001](https://psono.com/blog/iso27001-certification-2025)
- Bitwarden: [plans](https://bitwarden.com/help/password-manager-plans/) · [compliance](https://bitwarden.com/compliance/) · [Bitwarden Lite](https://bitwarden.com/blog/lightweight-and-flexible-bitwarden-lite-self-host-deployment/)
- SOC 2: [AICPA SOC overview](https://www.aicpa-cima.com/topic/audit-assurance/audit-and-assurance-greater-than-soc-2) · [shared responsibility](https://www.konfirmity.com/blog/soc-2-shared-responsibility-model) · [audit log requirements](https://auditkit.dev/blog/soc-2-audit-log-requirements)
