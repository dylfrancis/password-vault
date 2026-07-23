# Free, Enterprise-Ready Password Vault: Research, Build & Validation

Submission for the "Build (or Set Up) a Free, Enterprise-Ready Password Vault"
challenge. Everything in this repo is runnable: `docker compose up -d` gives you
the vault, TLS, continuous backups, scheduled restore drills, audit log export,
and health monitoring, with no host dependencies beyond Docker, no paid services.

**TL;DR:** I chose **Vaultwarden** (self-hosted Bitwarden-compatible server,
AGPL-3.0) after comparing 12+ options, then spent most of the effort where
free tiers and stock tooling fall short: backup/restore with an encryption
key-escrow model, audit log export for SOC 2 evidence, and an offboarding
tool that generates credential-rotation checklists, a capability even paid
Bitwarden doesn't ship. Every recovery path documented here was executed for
real, including a full dead-server rebuild measured at ~30 seconds.

---

## 0. Decision zero: don't roll our own

The brief says "build (or set up)." The first real decision was refusing the
*build* half. A custom vault means designing an E2E encryption protocol, key
management, and sharing semantics (the exact domain where subtle mistakes are
catastrophic and invisible until someone exploits them), plus a client ecosystem
(browser extensions, mobile apps, autofill) that takes years to make usable, all
of it unaudited on day one. Battle-tested open source gives every one of those
properties out of the box, with years of public CVE history and third-party
audits I could read instead of earn.

So the effort went where custom work actually pays: the operational gaps
*around* a proven vault: backup with a sane key-custody model, audit log
export, offboarding rotation. All of it small, auditable scripts at the edges
(§4), with zero lines of cryptography written by me. Rolling your own crypto isn't
ambition; it's how password managers end up as CVE case studies.

## 1. How I researched

I ran this AI-first. The entire project started with one prompt to Claude Code:

> *"Just received a memo to setup an enterprise password vault. The requirements
> are located in the docs folder. Please read and understand the requirements of
> the document, then do research on potential options that match the description.
> Off the top of my head, some good candidates would be Vaultwarden and Passbolt,
> leaning towards Passbolt as it hits the free, SOC2 compliance, and self-hosted
> parts. Please compare these to other potential solutions I may not be aware of
> and present a comparison against the requirements."*

Three deliberate things in how that's prompted:

1. **Ground in the source document, not my summary of it.** "Read and understand
   the requirements" forces the comparison to be scored against the actual brief.
2. **State my priors out loud.** Naming Passbolt *and my reasoning for it* gave
   the research something concrete to attack. It did: both assumptions
   (free, SOC 2) turned out wrong (§2). Hiding your lean gets you agreement;
   stating it gets you corrected.
3. **Ask for what I don't know.** "Solutions I may not be aware of" widens the
   sweep beyond my shortlist; that's where the sysPass/Padloc disqualifications
   and the Psono dark horse came from.

From that prompt, the research fanned out as three parallel deep-dive agents:
(a) Passbolt vs Vaultwarden feature/paywall/CVE analysis, (b) a survey of every
credible alternative, and (c) how SOC 2 actually applies to self-hosted
software. Each verified its claims against current official docs, release pages, and CVE
databases (July 2026), not vendor marketing. I used the same pattern for the
rest of the project: AI drafts and researches, I steer scope (cut an 8-day plan
to a 2-hour one), challenge designs (a grilling session produced the backup
ADRs), and validate everything against the running system (§5). Full comparison
with sources: [docs/vault-comparison.md](docs/vault-comparison.md).

Two more moments from the session that show the working relationship:

- **Cutting AI scope creep.** The first implementation plan came back as a
  plausible-looking 8-10 day enterprise rollout. My response: *"While I
  appreciate the estimate, this is a 1-2 hour setup we should do, let's focus
  on the highest value wins."* The default failure mode of agentic AI is
  gold-plating; the human's job is to reject the frame, not just edit the plan.
  The 8-day plan became five ranked extensions (§4) delivered the same day.
- **Rewarding pushback.** When the interviewer clarified that losing even a
  short window of changes was costly, I asked: *"should we rearchitect to
  postgres for their streaming of this easier and more hardened for
  concurrency?"* The AI argued no: Postgres solves a concurrency problem this
  scale doesn't have, and would force rewriting tested backup tooling for zero
  requirement. I took the pushback, and the rejection rationale with explicit
  revisit triggers is recorded in
  [ADR-0002](docs/adr/0002-two-tier-backup-litestream.md). Prompting until the
  AI agrees with you is the anti-pattern; stating a position and letting it be
  attacked is the point.

Requirements (from the brief): completely free (no expiring trials, no paywalled
core features) · open-source · self-hosted · 10–50 users · secure sharing ·
access controls · audit logging · strong encryption · realistic setup · a real
answer on SOC 2.

## 2. What I compared and what I found

| Option | Verdict |
|---|---|
| **Vaultwarden** | **Chosen.** Only option where every "enterprise" feature is free: unlimited users, collections, roles, event logs, WebAuthn MFA, admin account recovery, OIDC SSO. Official Bitwarden clients (best UX in category, audited client-side crypto). Single Rust binary. |
| **Passbolt CE** | Runner-up. Best vendor assurance record (Cure53 audits ×N, SOC 2 Type II vendor). But CE paywalls exactly what auditors ask about: audit log UI, SSO, LDAP, account recovery (~$4.90/user/mo). |
| **Psono CE** | Dark horse. Apache-2.0, Cure53 audits 2025 *and* 2026. Same catch: audit logs + SSO are EE. (EE free for up to 10 users; worth knowing, but a cap under our 10–50 requirement.) |
| Bitwarden official | Free org = 2 users / 2 collections. Not free at team scale; that gap is why Vaultwarden exists. |
| sysPass | Abandoned 2022, unpatched CVEs. Disqualified. |
| TeamPass | Server-side crypto (server sees plaintext). A security tier below E2E options. |
| KeePassXC shared DB | One master key, no per-user access control, no audit trail. Not a team system. |
| HashiCorp Vault / OpenBao / Infisical | Machine-secrets managers, wrong category (no autofill, no personal vaults). OpenBao noted for the future machine-secrets story. |
| Padloc, LibrePass, Passky | Dead or commercially ambiguous. |

**The instructive mistake:** I initially leaned Passbolt because "it hits free +
SOC 2 + self-hosted." Research corrected both assumptions: (1) their SOC 2
report covers *their company*, not your self-hosted deployment; SOC 2 attests
organizations, never software products; (2) Passbolt CE isn't free where it
counts: the audit log is the upsell. Vaultwarden, with zero attestations,
ships the actual audit-logging *feature* free. That reframing drove the
final decision and the whole SOC 2 section below.

**What "enterprise-ready" means here:** multiple users with real RBAC; sharing
that doesn't leak plaintext to the server; an audit trail you can hand an
auditor; MFA; recoverability (backups *proven* by restore, not assumed); an
offboarding story that accounts for E2E's blind spot; deploy/upgrade/monitor
without a dedicated ops team.

## 3. Architecture

```
                                users (Bitwarden apps / browser ext / web)
                                      │ HTTPS
                                      ▼
      ┌─────────────────────────── Docker host ────────────────────────────┐
      │  vw-caddy ───────── TLS (auto-ACME), security headers, JSON        │
      │     │               access logs, /admin IP-blocked, X-Real-IP      │
      │     ▼                                                              │
      │  vaultwarden ────── vault API + web UI (E2E: server stores         │
      │     │               only ciphertext)                               │
      │     ▼                                                              │
      │  ./vw-data ──────── SQLite DB, RSA keys, attachments               │
      │     │      │                                                       │
      │     │      └──► vw-litestream ── streams every DB write to an      │
      │     │                            age-encrypted replica (RPO: sec)  │
      │     └────────► vw-backup ─────── cron sidecar: full encrypted      │
      │                  │               Backup Set every 15 min, weekly   │
      │                  │               automated restore drill, audit    │
      │                  │               export, health checks             │
      │                  ▼                                                 │
      │               ./backups ──► rclone ──► S3-compatible (offsite)     │
      └────────────────────────────────────────────────────────────────────┘
         age private key: NEVER on this host, held by 2 escrow officers
```

Three encryption layers with different jobs: **E2E** (Bitwarden protocol,
vault items encrypted client-side), **TLS** (Caddy), **age** (backups, covering
what E2E doesn't: emails, password hashes, server keys, config).

Design records live with the code: [CONTEXT.md](CONTEXT.md) (domain glossary +
invariants), [ADR-0001](docs/adr/0001-backup-encryption-age-two-officer-escrow.md)
(key escrow), [ADR-0002](docs/adr/0002-two-tier-backup-litestream.md) (two-tier
RPO; includes why I rejected a Postgres migration).

## 4. What I built beyond the stock deployment

Stock Vaultwarden + a hardened proxy is a good vault. It is not yet an
*operable* one. Five extensions close the gap: all small, stdlib-Python,
reading the DB/API from outside (no fork, clean upgrade path), running on
schedule inside the backup sidecar:

| Tool | Gap it closes |
|---|---|
| [`vw_backup.py`](scripts/vw_backup.py) | Stock ships no backup story, and E2E data loss is unrecoverable *by design*. Full encrypted Backup Set every 15 min + Litestream WAL streaming (seconds RPO) + retention ladder (48h/30d/12m) + **weekly automated restore drill** that boots the backup in a scratch container and verifies data made it. Key custody: age public key on host, private key with two humans (ADR-0001). |
| [`vw_audit.py`](scripts/vw_audit.py) | Vaultwarden records events but has no export API and a bare UI. Login/event views, evidence summaries, incremental JSONL export for SIEM pickup. Found + fixed along the way: audit rows logged the proxy's IP until Caddy forwarded `X-Real-IP`. |
| [`vw_access.py offboard`](scripts/vw_access.py) | **The differentiator.** E2E means a leaver already decrypted everything they could reach; removing the account revokes access, not knowledge. Generates the rotation checklist (deep links per exposed item), scoped by actual role/collections, incl. the "invited-but-never-confirmed → nothing to rotate" shortcut. Neither Vaultwarden nor paid Bitwarden produces this. |
| [`vw_access.py matrix`](scripts/vw_access.py) | Quarterly access review (user × role × collections × reachable items) as sign-off-ready markdown/CSV. |
| [`vw_health.py`](scripts/vw_health.py) | Liveness + backup/replica freshness + disk, every 5 min, optional dead-man ping. |

E2E blindness handled honestly: the server cannot decrypt item/collection names,
so reports use web-vault deep links (your session decrypts on click) plus an
optional operator-maintained label map, rather than smuggling owner
credentials into scripts.

### Technology choices for the extension layer

| Choice | Why (and over what) |
|---|---|
| **Python, stdlib only** (no pip, no venv) | The tooling guards the credential store, so its own supply chain should be empty. `sqlite3` gives the online-backup API natively, `tomllib` reads config; zero third-party packages to audit or update. |
| **age** (backup encryption) | Public-key mode means the host holds only the encrypt key; a host compromise can't read historical backups. Single static binary, no agent/keyring machinery. Chosen over gpg (symmetric passphrase would have to live in cron's env) and bucket SSE (provider or anyone with bucket creds could read emails, hashes, server keys). |
| **rclone** (offsite shipping) | One tool speaks every S3-compatible backend, so the bucket provider stays a config value, not an architecture decision. |
| **Litestream** (continuous DB replication) | Seconds-level RPO on SQLite with a ~20 MB sidecar and zero changes to the vault. Chosen over migrating to Postgres replication; full trade-off in [ADR-0002](docs/adr/0002-two-tier-backup-litestream.md). |
| **SQLite** (kept, not replaced) | Vaultwarden's default and most battle-tested path; write load at 10-50 users is trivial; single-file DB makes backup, drill, and restore radically simpler. Postgres revisit triggers documented in ADR-0002. |
| **Caddy** (TLS/proxy) | Automatic ACME certificates and a readable 30-line config where nginx + certbot is two tools and a renewal cron. Also the enforcement point for security headers, admin-panel IP blocking, and the X-Real-IP forwarding the audit trail depends on. |
| **Docker sidecar + crond** (scheduling) | Jobs ship with the stack: `git clone` + `docker compose up` on any Docker host brings up vault *and* its backup/audit/health automation. No host cron, no config-management dependency; the scheduler is versioned in the repo. |
| **Markdown + JSONL outputs** | Reports (offboarding, access review) are markdown because their consumers are humans and auditors; exports are JSONL because their consumer is whatever SIEM shows up later. No bespoke formats. |

Several of these were not tools I knew going in; they won on merits against the
ones I did (the Postgres exchange in §1 is exactly that pattern playing out).
My rule for adopting an unfamiliar tool into a system this sensitive: have it
explained until I can explain it back in plain language, then break it on
purpose before trusting it. The key-escrow model went through exactly that
loop. After the design session settled on it, I stopped before building:

> *"explain the 2-officer escrow part to me a bit more so i can understand"*

and later, mid-testing, when an answer got too dense:

> *"wait did i actually restore the backup or just the create a copy environment? please help me ground my understanding a bit better as there's a lot of jargon being thrown around
> too much jargon right now"*

That second question caught a real misconception: I thought the side-copy
restore had modified the live vault (it hadn't), and the correction is why the
runbooks now spell out which recovery path touches production and which never
does. Refusing to operate machinery I can't explain back is the cheapest
safety control in this whole repo. The restore drills in §5 were as much me
learning the failure modes of age and Litestream as they were validating the
backups. Reaching only for familiar tools would have meant a worse design;
adopting unfamiliar ones without that loop would have meant a design I
couldn't operate at 3am.

## 5. Validation: scenarios actually executed

| Scenario | Result |
|---|---|
| Backup pipeline (snapshot → encrypt → archive) | Pass. archives verified as age ciphertext, integrity-checked snapshots |
| Automated restore drill (host + in-container) | Pass. incl. a guard I added after spotting a false-pass risk: `/alive` succeeds on an *empty* data dir, so the drill byte-compares the restored DB inside the scratch container |
| Item permanently deleted → point-in-time side-copy recovery | Pass. recovered value from pre-delete archive; live service untouched ([runbook scenario A](docs/runbooks/backup-restore.md)) |
| Full dead-server rebuild (planned, fresh backup) | Pass. with a lesson: an empty-but-healthy vault *looks* like failure; hence the permanent `RESTORE-CANARY` practice now in the runbook |
| Full dead-server rebuild (unplanned, newest cron archive only) | Pass. **~30 seconds dead-to-alive**, worst-case data loss 15 min (seconds with Litestream replay) |
| Litestream encrypted stream restore with escrow key | Pass. integrity ok, counts match live |
| Failed-login capture (incl. unknown usernames via app log) | Pass. real client IPs after the X-Real-IP fix |
| Audit export idempotency | Pass. run 1: 14 records; run 2: "nothing new" |
| Offboard report, both cases (org-wide owner / unconfirmed invite) | Pass. correct scope each way |
| Unattended cron firing | Pass. archives + exports appearing without manual trigger |

Testing was half the work; the other half was making sure recovery doesn't
depend on me being in the room. Every path above is written down as a runbook
with copy-paste commands, marked with the date it was actually executed:
[backup & restore](docs/runbooks/backup-restore.md) (both recovery scenarios,
key ceremony, failure-signal table for 3am debugging),
[audit logs](docs/runbooks/audit-logs.md), and
[offboarding & access reviews](docs/runbooks/offboarding-access-review.md).
The quarterly drill even exercises the human path (retrieving the escrow key
from an officer), so procedure rot gets caught, not discovered mid-outage.
Whoever inherits this system inherits the instructions, not just the scripts.

## 6. Does this meet SOC 2?

The precise answer: **SOC 2 attests an organization's controls, not a software
product.** No password manager "is SOC 2 compliant", and a vendor's SOC 2
report (Passbolt's, Bitwarden's) covers *their* environment, not your
self-hosted instance. Self-hosting moves the entire control surface into your
own audit scope.

The right question is whether this deployment gives a 10–50 person org the
controls its SOC 2 audit needs. It maps like this:

| Trust Services Criteria | Covered by |
|---|---|
| CC6.1–6.3 access control | Org roles + collections, MFA policy, quarterly `matrix` review, offboarding runbook + rotation report |
| CC6.7 encryption | E2E vault crypto, TLS, age-encrypted backups |
| CC7.2 / CC4.1 monitoring & evidence | Event logs (on since day 1), `vw_audit` export → SIEM spool, Caddy access logs (12-mo roll), health checks |
| CC7.5 / A1.2–1.3 recovery | Two-tier backups, weekly automated + quarterly human drills, evidence-generating runbooks |
| CC9.2 vendor risk | Documented accepted risk: community-maintained server, no vendor SLA, mitigated by pinned versions + patch cadence |

Honest limits, documented rather than hidden: personal-vault events are never
logged (mitigation: policy that work credentials live in org collections, where
logging exists); per-item *read* auditing is architecturally impossible in any
free tier (clients sync the full encrypted vault).

If a customer audit ever makes per-item read auditing a hard requirement, there
are two escalation paths, and picking between them is a build-vs-buy decision
with real numbers on both sides:

- **Pay for it.** Passbolt Pro (~$4.90/user/mo) or Bitwarden Enterprise gets
  native item-level audit trails with a vendor SLA behind them. At 50 seats
  that's roughly $3-5k/year, which is cheaper than one engineer-month.
- **Fork and extend.** Everything here is AGPL: server and official clients
  both. A fork is genuinely on the table, but the honest scope assessment is
  that per-item read auditing is a *protocol* change, not a server patch. The
  clients sync the whole encrypted vault, so the server never learns which
  item was opened; capturing that means modifying the clients to emit
  access events, then owning builds and store distribution of those clients,
  plus tracking upstream security releases on both halves forever. Viable for
  an org with platform-engineering appetite that wants to stay free (or
  upstream the feature), but for a 10-50 seat team the yearly license is the
  rational choice. Smaller fork-free wins (export APIs, extra server-side
  event types) don't require any of that, which is exactly the space the §4
  scripts already occupy from the outside.

## 7. Future iterations (trigger → action)

- **Org picks an IdP** → wire Vaultwarden's built-in OIDC SSO (config, not code).
- **Real deployment** → S3 bucket for rclone + Litestream, healthchecks.io
  UUIDs into `backup.toml`, key ceremony with two real escrow officers
  ([runbook](docs/runbooks/backup-restore.md#one-time-setup)), Fail2Ban jail on
  the app log, VPN-only ingress.
- **Team outgrows CLI log views** → Loki/Grafana on the existing JSONL spool
  (deliberately skipped now: wrong cost/benefit at ≤50 users).
- **Hundreds of users / HA requirement / org-managed Postgres with PITR
  appears** → revisit Postgres per ADR-0002's documented triggers.
- **Dual-control requirement on backups** → Shamir-split the escrow key
  (rejected for now: two-officer availability model fits the org size).
- **Machine secrets (CI, API keys) creep into the vault** → stand up OpenBao or
  Infisical beside it; human and machine secrets want different tools.

## 8. Repo map & quickstart

| Path | Contents |
|---|---|
| `docker-compose.yml`, `Caddyfile`, `.env.example` | The stack: vault, TLS proxy, replication, ops sidecar |
| `litestream.yml.example` | Continuous DB replication config |
| `scripts/setup.sh` | One-command bootstrap from a fresh clone |
| `scripts/vw_backup.py` | Backup, prune, restore drill, restore |
| `scripts/vw_audit.py` | Audit log views + incremental SIEM export |
| `scripts/vw_access.py` | Offboarding rotation report + access-review matrix |
| `scripts/vw_health.py` | Liveness, backup/replica freshness, disk checks |
| `scripts/Dockerfile.backup`, `scripts/backup-crontab` | Ops sidecar image + its schedule |
| `docs/vault-comparison.md` | Full 12-option comparison with sources |
| `docs/implementation-plan.md` | Scope, status, decision log |
| `docs/adr/` | 0001 key escrow · 0002 two-tier backup (incl. Postgres rejection) |
| `docs/runbooks/` | backup-restore · audit-logs · offboarding-access-review |
| `CONTEXT.md` | Domain glossary + invariants |

One command from clone to running stack:

```bash
git clone <repo> && cd password-vault
./scripts/setup.sh --demo     # local evaluation: https://localhost, no prompts
./scripts/setup.sh            # production: prompts for domain + SMTP
```

The script copies the three config templates, generates the age escrow keypair
(host `age-keygen` if present, otherwise inside the backup image), wires the
public key into both configs, starts the stack, and prints next steps. It is
idempotent and never overwrites existing config. **It also prints the one
non-negotiable manual step:** hand `escrow-key.txt` to two escrow officers and
delete it from the host (ADR-0001).

Configuration files, if you prefer doing it by hand (each has a commented
`.example` template):

| File | From | Fill in |
|---|---|---|
| `.env` | `.env.example` | `DOMAIN`; SMTP host/from/user/password (invites and emergency access need mail); `ADMIN_TOKEN` as an Argon2 hash if you want the admin panel (`docker run --rm -it vaultwarden/server:1.36.0 /vaultwarden hash`); keep `SIGNUPS_ALLOWED=false` in production |
| `scripts/backup.toml` | `scripts/backup.toml.example` | `age_recipients` (escrow public key); `rclone_remote` once a bucket exists (`docker compose exec backup rclone config`); healthchecks.io ping URLs when created |
| `litestream.yml` | `litestream.yml.example` | age recipient; swap the local `file:///replica` block for the `s3://` block in production (S3 creds go in `.env` as `LITESTREAM_ACCESS_KEY_ID`/`LITESTREAM_SECRET_ACCESS_KEY`) |

Four containers come up: the vault, TLS proxy, continuous replication, and the
ops sidecar (backups every 15 min, weekly restore drill, audit export, health
checks). First-run details and the key ceremony:
[docs/runbooks/backup-restore.md](docs/runbooks/backup-restore.md).

---

*Built AI-first with Claude Code; the prompting approach and division of labor
are described in §1. Every factual claim was source-verified and every recovery
path in §5 was executed against the running stack, not assumed.*
