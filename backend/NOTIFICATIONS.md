# JAYN Vault email notifications

Backup notification preferences and recipients are managed from the Notification Center in JAYN Vault. SMTP credentials remain server-side and are never returned to the browser.

Add these values to the environment file used by `jayn-vault-api.service`:

```ini
JAYN_VAULT_SMTP_HOST=smtp.office365.com
JAYN_VAULT_SMTP_PORT=587
JAYN_VAULT_SMTP_USERNAME=vault@jaynconstruction.com
JAYN_VAULT_SMTP_PASSWORD=replace-with-an-app-password-or-smtp-credential
JAYN_VAULT_SMTP_FROM=vault@jaynconstruction.com
JAYN_VAULT_SMTP_STARTTLS=true
JAYN_VAULT_SMTP_SSL=false
```

Optional storage override:

```ini
JAYN_VAULT_NOTIFICATION_HISTORY=/var/lib/jayn-vault/notifications.json
```

After changing the service environment, run:

```bash
sudo systemctl daemon-reload
sudo systemctl restart jayn-vault-api
```

Use **Notification Center → Email Settings → Send Test Email** to verify delivery independently from a backup run. The history retains sent, failed, suppressed, and test events and links each backup event to its run ID.
