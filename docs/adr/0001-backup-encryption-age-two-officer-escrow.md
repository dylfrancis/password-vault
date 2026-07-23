# 0001 — Backup encryption with age, private key held by two escrow officers outside the vault

Status: accepted (2026-07-23)

## Context

Backup Sets contain data the E2E model does not protect: user email addresses, password hashes, org structure, the JWT-signing RSA keypair, and `.env` (SMTP credentials, admin token hash). Backups therefore must be encrypted before leaving the host.

The decryption key cannot live only inside the company vault: the scenario the backup exists for (vault destroyed) is exactly the scenario where the vault is unavailable.

Alternatives considered:
- **gpg symmetric passphrase** — requires the secret on-host in cron's environment to encrypt; shared-passphrase sprawl.
- **S3 SSE only** — bucket-credential holders and the provider can read emails/hashes/RSA key.

## Decision

Encrypt every Backup Set with **age** (public-key mode). The public key sits in script config on the host — encryption requires no secret material on the host. The private key is held by **two named Escrow Officers**, each keeping a copy outside the company vault (personal safe or personal password manager).

## Consequences

- Host compromise does not expose historical backups (no decryption secret on host).
- Restore requires reaching one of two humans — accepted cost; the quarterly manual Restore Drill exercises this path so it cannot silently rot.
- **Hard to reverse:** every existing archive stays encrypted to this keypair. Key rotation means re-encrypting or aging-out the retention window (12 months).
- Officer offboarding = immediate key rotation + new escrow assignment.
