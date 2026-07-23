#!/usr/bin/env bash
# setup.sh — bootstrap the vault stack from a fresh clone.
#
#   ./scripts/setup.sh --demo    local evaluation: https://localhost, open
#                                signups, no SMTP, no prompts
#   ./scripts/setup.sh           production: prompts for domain + SMTP
#   ./scripts/setup.sh --no-start   (either mode) prepare configs only
#
# Idempotent: existing config files are never overwritten.

set -euo pipefail
cd "$(dirname "$0")/.."

DEMO=false
START=true
for arg in "$@"; do
  case "$arg" in
    --demo) DEMO=true ;;
    --no-start) START=false ;;
    *) echo "unknown flag: $arg"; exit 1 ;;
  esac
done

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }

command -v docker >/dev/null || { echo "ERROR: docker is required"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "ERROR: docker compose v2 required"; exit 1; }

# ---------------------------------------------------------------- .env
say ".env (Vaultwarden + Caddy)"
if [[ -f .env ]]; then
  note "exists, leaving untouched"
else
  cp .env.example .env
  if $DEMO; then
    sed -i.bak -e 's|^DOMAIN=.*|DOMAIN=https://localhost|' \
               -e 's|^SIGNUPS_ALLOWED=false|SIGNUPS_ALLOWED=true|' .env && rm -f .env.bak
    note "demo config written: https://localhost, signups OPEN, SMTP off"
    note "(flip SIGNUPS_ALLOWED=false after creating your account)"
  else
    read -rp "Vault domain (e.g. https://vault.example.com): " domain
    read -rp "SMTP host: " smtp_host
    read -rp "SMTP from address: " smtp_from
    read -rp "SMTP username: " smtp_user
    read -rsp "SMTP password: " smtp_pass; echo
    sed -i.bak -e "s|^DOMAIN=.*|DOMAIN=${domain}|" \
               -e "s|^SMTP_HOST=.*|SMTP_HOST=${smtp_host}|" \
               -e "s|^SMTP_FROM=.*|SMTP_FROM=${smtp_from}|" \
               -e "s|^SMTP_USERNAME=.*|SMTP_USERNAME=${smtp_user}|" \
               -e "s|^SMTP_PASSWORD=.*|SMTP_PASSWORD=${smtp_pass}|" .env && rm -f .env.bak
    note "production .env written (signups closed, invitation-only)"
    note "ADMIN_TOKEN is empty (admin panel disabled). To enable it later:"
    note "  docker run --rm -it vaultwarden/server:1.36.0 /vaultwarden hash"
  fi
fi

# ------------------------------------------------- escrow key + backup.toml
say "Backup encryption (age escrow keypair)"
if [[ -f scripts/backup.toml ]]; then
  note "scripts/backup.toml exists, leaving untouched"
else
  if [[ -f escrow-key.txt ]]; then
    note "escrow-key.txt already present, reusing"
  elif command -v age-keygen >/dev/null; then
    age-keygen -o escrow-key.txt 2>/dev/null
  else
    note "age not on host; generating inside the backup image..."
    docker compose build backup >/dev/null
    docker compose run --rm --no-deps backup age-keygen -o escrow-key.txt 2>/dev/null
  fi
  PUB=$(grep 'public key:' escrow-key.txt | awk '{print $NF}')
  sed "s|age1REPLACE_WITH_ESCROW_PUBLIC_KEY|${PUB}|" scripts/backup.toml.example > scripts/backup.toml
  note "scripts/backup.toml written with recipient ${PUB}"
  printf '\n   \033[1;31mCRITICAL: escrow-key.txt is the ONLY way to read backups.\033[0m\n'
  note "Give a copy to each of two escrow officers (offline: safe/personal"
  note "password manager), then DELETE it from this machine:  rm escrow-key.txt"
  note "It must never live on this host or in the vault it protects (ADR-0001)."
fi

# ------------------------------------------------------------ litestream.yml
say "Litestream (continuous DB replication)"
if [[ -f litestream.yml ]]; then
  note "exists, leaving untouched"
else
  PUB=${PUB:-$(grep -o 'age1[a-z0-9]*' scripts/backup.toml | head -1)}
  sed "s|age1REPLACE_WITH_ESCROW_PUBLIC_KEY|${PUB}|" litestream.yml.example > litestream.yml
  note "litestream.yml written (local file replica; switch to the s3:// block for prod)"
fi

mkdir -p backups/litestream logs/caddy

# ------------------------------------------------------------------- start
if $START; then
  say "Starting the stack"
  docker compose up -d --build
  sleep 5
  if curl -fsSk "https://localhost/alive" >/dev/null 2>&1; then
    note "vault is alive"
  else
    note "no /alive on localhost yet (normal if DOMAIN is a remote host / DNS pending)"
  fi
fi

say "Done. Next steps"
$DEMO && note "1. Open https://localhost (accept the self-signed cert), create your account" \
       || note "1. Open your vault domain, log in via an invitation"
note "2. Create an org + collections; add a RESTORE-CANARY item (see runbooks)"
note "3. Offsite backups:  docker compose exec backup rclone config"
note "   then set rclone_remote in scripts/backup.toml"
note "4. Dead-man switch: healthchecks.io URLs into scripts/backup.toml"
note "5. Read docs/runbooks/backup-restore.md (key ceremony, drills)"
