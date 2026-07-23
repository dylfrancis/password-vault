# Runbook: Offboarding & Access Reviews

Tool: `scripts/vw_access.py` (stdlib Python, reads DB read-only).
Why this exists: E2E encryption means a departing user's clients already
decrypted everything they could reach — removing the account revokes future
access, **not knowledge**. Offboarding therefore ends with credential rotation,
and this tool generates the exact rotation list. Neither Vaultwarden nor paid
Bitwarden produces this.

## Offboarding (SOC 2 CC6.2/CC6.3)

Order matters — the report reads permission rows that account removal deletes:

1. **Report first:**
   ```bash
   python3 scripts/vw_access.py offboard --email leaver@example.com \
     --output evidence/offboard-leaver-$(date +%Y%m%d).md
   ```
2. Revoke/remove the account (org member removal in the web UI; full user
   delete via admin panel if enabled).
3. Work the checklist: each line is a web-vault deep link — click, rotate the
   credential at the *actual service*, update the item, tick the box.
4. File the completed report as evidence.

The report automatically handles:
- **Owner/Admin/access_all** → whole-org rotation list.
- **Invited but never confirmed** → "nothing to rotate" (they never received the
  org key) — don't waste a rotation cycle on ghost invitations.
- `hide_passwords` collections → included anyway (client-side cosmetics, not a control).
- Emergency-access grants in either direction → flagged for removal.

## Quarterly access review (SOC 2 CC6.1-6.3)

```bash
python3 scripts/vw_access.py matrix > evidence/access-review-$(date +%Y%m%d).md
```

Markdown grid: user x org x role x status x reachable items x collections, with
sign-off line. Review meeting = walk the grid, question every `owner`/`access_all`
row and any `invited` older than 2 weeks, record changes, sign, file.

`--csv` for spreadsheet import.

## Collection labels

Collection names are E2E-encrypted — the server prints UUIDs unless you maintain
`scripts/vault-labels.toml` (copy the `.example`, fill labels from the web UI
once, update when collections change). Reports flag unlabeled collections.
