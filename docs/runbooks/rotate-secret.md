# Runbook: rotate a secret (owner)

1. Create the new credential at the provider (keep the old one valid for now).
2. `make secret-set NAME=<name> ENV=<env>` and paste the value at the hidden prompt.
   This adds a new Secret Manager version; mounts reference `latest`.
3. Restart instances so they read it: `gh workflow run deploy.yml` (redeploys main), or
   for an urgent change the ops workflow's rollback/redeploy.
4. Confirm the app works (smoke test, logs), then revoke the old credential at the
   provider and disable the old version:
   `gcloud secrets versions disable <old-version> --secret=<name>`.
5. Suspected leak: revoke first (step 4) even if it causes downtime, then follow steps
   1–3, then write a postmortem.

Agents never run these commands (the guard hook blocks reading or writing secret values).
