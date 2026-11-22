# Runbook: rotate a secret (owner)

1. Create the new credential at the provider (keep the old one valid for now).
2. `make secret-set NAME=<name> ENV=<env>` and paste the value at the hidden prompt.
   This adds a new Secret Manager version; mounts reference `latest`.
3. Nothing to restart: the job reads `latest` at every execution. To check now, run a dry
   run (`gcloud run jobs execute trading-bot-prod --region europe-west2 --args=run,--dry-run --wait`).
4. Confirm it works (`make logs ENV=prod`), then revoke the old credential at the
   provider and disable the old version:
   `gcloud secrets versions disable <old-version> --secret=<name>`.
5. Suspected leak: revoke first (step 4) even if it causes downtime, then follow steps
   1–3, then write a postmortem.

Agents never run these commands (the guard hook blocks reading or writing secret values).

The risk-gate GitHub App key is a GitHub environment secret, not in Secret Manager: see
[risk-gate-app.md](risk-gate-app.md#rotating-the-key).
