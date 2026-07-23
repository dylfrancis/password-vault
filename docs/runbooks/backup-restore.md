# Runbook: Backup & Restore

Tool: `scripts/vw_backup.py` (Python 3.11+ stdlib; needs `age`, `rclone`, `docker`).
Design: `docs/implementation-plan.md` item 1 · glossary `CONTEXT.md` · key custody `docs/adr/0001`.

## How it runs

The `backup` service in `docker-compose.yml` is a sidecar (python + age + rclone +
docker-cli + crond) that runs the schedule in `scripts/backup-crontab`. Deploying to
any Docker host — including a fresh demo server — is just:

```bash
git clone <repo> && cd password-vault
cp .env.example .env && cp scripts/backup.toml.example scripts/backup.toml  # fill both in
docker compose up -d        # vault + TLS + scheduled backups/drills, no host deps
docker compose exec backup rclone config       # one-time: create the S3 remote
docker logs vw-backup                          # job output lands here
```

Notes:
- The repo is mounted into the sidecar at the **same absolute path** as on the host;
  the drill's scratch container is started on the host engine (docker-out-of-docker),
  so identical paths are required. Don't change those volume lines.
- The sidecar mounts `/var/run/docker.sock`, which is root-equivalent on the host.
  Accepted because it runs only our pinned image and this repo's script; keep the
  repo write-protected in prod and don't add packages to the sidecar image.
- The script also runs fine directly on a host with `age`/`rclone`/python installed —
  same commands, nothing container-specific in it.

## One-time setup

### 1. Key ceremony (the two Escrow Officers, together)

```bash
age-keygen -o escrow-key.txt        # run on an officer's machine, NOT the server
```

- The `# public key: age1...` line goes into `scripts/backup.toml` → `age_recipients`.
- Each officer stores a full copy of `escrow-key.txt` **outside the company vault**
  (personal safe / personal password manager / sealed printed envelope).
- Delete the file from the machine that generated it once both copies are confirmed.
- The server never holds the private key (ADR-0001).

### 2. Offsite bucket

```bash
rclone config                        # create an S3-compatible remote, e.g. "s3backup"
```

Set `rclone_remote = "s3backup:company-vault-backups"` in `backup.toml`.
Bucket lifecycle rule (backstop only): expire objects older than 400 days.

### 3. Dead-man switch

Create three checks at healthchecks.io; put the ping URLs in `backup.toml`:

| Check | Schedule | Grace |
|---|---|---|
| vault-backup | every 15 min | 10 min |
| vault-drill | weekly | 1 day |
| vault-alive | every 5 min (cron curl `/alive`) | 10 min |

Litestream has no ping hook — watch it via `docker logs vw-litestream` (it logs every
snapshot/WAL write) and treat a stale `backups/litestream` mtime as an alert condition;
the quarterly drill's `litestream restore` is the hard verification.

### 4. Schedule

Handled by the sidecar — edit `scripts/backup-crontab` and `docker compose build backup`
to change cadence. Defaults: backup hourly at :17, drill Mondays 04:23 UTC, plus a
commented liveness-probe line to enable once the healthchecks.io UUID exists.

## Tier 1: continuous DB streaming (Litestream)

The `litestream` service streams every database change to an age-encrypted replica
within seconds (see ADR-0002). Demo default replicates to `./backups/litestream`;
production should switch `litestream.yml` to the S3 replica block (creds via
`LITESTREAM_ACCESS_KEY_ID`/`LITESTREAM_SECRET_ACCESS_KEY` in `.env`, enable
`env_file` on the service).

Restore the DB to the latest streamed state (needs the escrow key):

```bash
# Build a THROWAWAY restore config — litestream takes the age secret key INLINE,
# so this file is itself a secret. Delete it immediately after.
cat > /tmp/ls-restore.yml <<EOF
dbs:
  - path: /data/db.sqlite3
    replicas:
      - url: file:///replica          # or the s3:// URL
        age:
          recipients: [<age1... public key>]
          identities: [<AGE-SECRET-KEY-1... from the escrow file>]
EOF
docker run --rm -v "$PWD/backups/litestream:/replica:ro" \
  -v /tmp/ls-restore.yml:/etc/litestream.yml:ro -v "$PWD/ls-out:/out" \
  litestream/litestream:0.3.13 restore -o /out/db.sqlite3 /data/db.sqlite3
rm /tmp/ls-restore.yml
```

Use when: damage happened minutes ago and the 15-min Backup Set hasn't caught it.
The restored file replaces `data/db.sqlite3` from a Backup Set restore — Litestream
carries ONLY the DB; attachments/keys/config always come from a Backup Set.
Quarterly manual drill: run this restore alongside the archive restore.

## Tier 2: What a backup contains

Full Backup Set every run: `data/db.sqlite3` (online snapshot, integrity-checked),
`data/rsa_key*`, `data/attachments/`, `data/sends/`, `config/{.env,Caddyfile,docker-compose.yml}`,
`manifest.json` (timestamp + row counts). Encrypted to the escrow public key(s); named
`vw-backup-<UTC-stamp>.tar.gz.age`. Retention: 48 hourlies / 30 dailies / 12 monthlies.

## Weekly automated drill (what it proves)

Default (no key on host): builds a fresh plaintext set, extracts it, checks
`PRAGMA integrity_check`, row counts vs manifest and live DB, `rsa_key*` presence,
boots a scratch container and polls `/alive`; separately confirms the newest
encrypted archive exists with a valid age header. Pings the drill check either way.

## Quarterly manual drill (the people path)

Why a human is required: vault item names/contents are end-to-end encrypted — scripts
can only verify row counts, never that data is *readable*. Logging in is the only
true content check.

1. Pick a random monthly archive from the bucket (not the newest).
2. Retrieve the private key from an Escrow Officer — time this; it is the real RTO driver.
3. Restore and boot an isolated copy (safe — never touches the live stack):

   ```bash
   rclone copyto s3backup:company-vault-backups/<archive> ./archive.tar.gz.age
   python3 scripts/vw_backup.py restore \
     --archive ./archive.tar.gz.age --identity <escrow-key-file> --output ./drill-out
   docker run --rm -d --name drill-check \
     -p 127.0.0.1:8088:80 -v $PWD/drill-out/data:/data vaultwarden/server:1.36.0
   ```

4. Open `http://localhost:8088`, log in with your own account, open a known item and
   confirm it decrypts. (Plain HTTP: if the web vault refuses, use the browser's
   proceed-anyway bypass — localhost-only, throwaway instance.)
5. Record date, archive tested, key-retrieval time, result — SOC 2 evidence (CC7.5/A1.3).
6. Tear down and destroy key material from the machine:

   ```bash
   docker rm -f drill-check && rm -rf drill-out archive.tar.gz.age <escrow-key-file>
   ```

Tip: keep one marker item (e.g. `RESTORE-CANARY`) in the org vault permanently —
step 4 is then always "can I open the canary," same check every quarter.

## Recovery scenario A — item permanently deleted, server is fine
*(tested 2026-07-23; zero downtime, live vault never modified)*

First: check the web vault's **Trash** — deletes go there before they're permanent.
Only continue if the item is truly gone.

1. Pick an archive from **before** the deletion (names are UTC timestamps —
   don't blindly take the newest, it may already post-date the mistake):

   ```bash
   ls -t backups/archive/
   ```

2. Get the escrow key from an Escrow Officer, then restore into a **side copy** —
   this touches nothing in production:

   ```bash
   python3 scripts/vw_backup.py restore \
     --archive backups/archive/<pre-delete-archive>.tar.gz.age \
     --identity <escrow-key-file> --output ./recover-out
   docker run --rm -d --name recover-check \
     -p 127.0.0.1:8088:80 -v $PWD/recover-out/data:/data vaultwarden/server:1.36.0
   ```

3. Open `http://localhost:8088` (accept the plain-HTTP browser warning — localhost-only,
   throwaway), log in with your normal account, open the lost item, copy its values out.
4. Re-create the item by hand in the real vault (`https://<your-domain>`).
5. Tear down:

   ```bash
   docker rm -f recover-check && rm -rf recover-out
   ```

Why not restore the whole backup over production for one item: the backup is a
full snapshot — overwriting would also roll back every change *everyone* made
since it was taken.

## Recovery scenario B — server dead, rebuild from latest backup
*(tested 2026-07-23; dead-to-alive measured at ~30 seconds once the key was in hand)*

You get whatever the schedule last wrote — worst case 15 minutes old. If those
minutes matter, additionally replay the Litestream tail (Tier 1 section above)
and use its `db.sqlite3` in place of the archive's copy.

1. On the replacement host: install Docker, clone this repo.
   (`.env`, `Caddyfile`, `docker-compose.yml` also live inside every archive under
   `config/` — a truly bare host can be rebuilt from the archive alone.)
2. Fetch the newest archive:

   ```bash
   rclone copyto s3backup:company-vault-backups/$(rclone lsf s3backup:company-vault-backups | sort | tail -1) .
   ```

3. Get the escrow key from either Escrow Officer.
4. Restore and install the data directory:

   ```bash
   python3 scripts/vw_backup.py restore \
     --archive <newest>.tar.gz.age --identity <escrow-key-file> --output ./dr-restore
   mv dr-restore/data vw-data
   docker compose up -d
   curl -fsSk https://localhost/alive
   ```

5. Log in and open a known item (the `RESTORE-CANARY`) — **login succeeding with an
   empty-looking vault is not proof of failure or success; opening a real item is.**
6. Point DNS at the new host; Caddy re-issues certificates automatically.
7. Afterwards: the escrow key touched this machine — if it wasn't clean/airgapped,
   run a new key ceremony and rotate (ADR-0001).

## Failure signals

| Alert | Meaning | First moves |
|---|---|---|
| vault-backup missed | cron dead, script crashed, disk full, DB locked | `logs/backup.log`; `df -h`; run `backup` manually |
| vault-backup /fail ping | script ran and hit an error | log shows the failing step (snapshot / age / rclone) |
| vault-drill missed or /fail | restore path broken — treat as a real incident, backups may be unusable | `logs/drill.log`; run `drill` manually; do NOT ignore until next week |
| vault-alive missed | service down | `docker ps`, `docker logs vaultwarden`, `docker logs vw-caddy` |
