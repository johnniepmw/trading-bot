#!/usr/bin/env bash
# Owner-only, on your own machine or Cloud Shell: put the dashboard service behind IAP so only
# your Google account can open it (ADR-0020; modelled on the owner's own setup for another
# dashboard). CI never runs this and never holds IAP rights, the OAuth client secret or your
# address. Safe to re-run.
#
# Before: the OAuth consent screen (External, Testing, you as the only test user) and an OAuth
# client (Web application) with the redirect URI
#   https://iap.googleapis.com/v1/oauth/clientIds/<CLIENT_ID>:handleRedirect
# Usage (the two `read` lines don't echo or save the values):
#   read -rs IAP_CLIENT_ID && export IAP_CLIENT_ID
#   read -rs IAP_CLIENT_SECRET && export IAP_CLIENT_SECRET
#   bash scripts/dashboard-iap.sh staging        # then prod
set -euo pipefail
env="${1:?usage: dashboard-iap.sh <staging|prod>}"
case "$env" in staging|prod) ;; *) echo "dashboard-iap: unknown env $env" >&2; exit 2 ;; esac
[ -n "${IAP_CLIENT_ID:-}" ] && [ -n "${IAP_CLIENT_SECRET:-}" ] \
  || { echo "dashboard-iap: set IAP_CLIENT_ID and IAP_CLIENT_SECRET first (see the header)" >&2; exit 2; }
project="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
region="${GCP_REGION:-europe-west2}"
app="${APP_NAME:-trading-bot}"
service="${app}-dashboard-${env}"
account="$(gcloud config get-value account 2>/dev/null)"
[ -n "$project" ] && [ -n "$account" ] || { echo "dashboard-iap: gcloud has no project or account set" >&2; exit 2; }

gc() { gcloud "$@" --project "$project" --quiet; }

gc run services describe "$service" --region "$region" --format='value(metadata.name)' >/dev/null \
  || { echo "dashboard-iap: $service doesn't exist yet (its infra PR applies first)" >&2; exit 1; }

echo "==> IAP on $service"
gc run services update "$service" --region "$region" --iap

echo "==> the OAuth client (the secret goes to IAP only: never the repo, state or CI)"
settings="$(mktemp)"
chmod 600 "$settings"
trap 'rm -f "$settings"' EXIT
printf 'access_settings:\n  oauth_settings:\n    client_id: "%s"\n    client_secret: "%s"\n' \
  "$IAP_CLIENT_ID" "$IAP_CLIENT_SECRET" >"$settings"
gc iap settings set "$settings" --resource-type=cloud-run --service="$service" --region="$region" >/dev/null
rm -f "$settings"

echo "==> IAP's service agent is the only invoker"
number="$(gc projects describe "$project" --format='value(projectNumber)')"
gc beta services identity create --service=iap.googleapis.com >/dev/null
gc run services add-iam-policy-binding "$service" --region "$region" \
  --member="serviceAccount:service-${number}@gcp-sa-iap.iam.gserviceaccount.com" --role=roles/run.invoker >/dev/null
policy="$(gc run services get-iam-policy "$service" --region "$region" --format=json)"
for public in allUsers allAuthenticatedUsers; do
  if grep -q "\"$public\"" <<<"$policy"; then
    echo "    removing public access ($public)"
    gc run services remove-iam-policy-binding "$service" --region "$region" \
      --member="$public" --role=roles/run.invoker >/dev/null
  fi
done

echo "==> your account ($account) may sign in; check no one else is listed below"
gc iap web add-iam-policy-binding --resource-type=cloud-run --service="$service" --region="$region" \
  --member="user:${account}" --role=roles/iap.httpsResourceAccessor >/dev/null

gc iap web get-iam-policy --resource-type=cloud-run --service="$service" --region="$region" \
  --format='value(bindings.members)'
url="$(gc run services describe "$service" --region "$region" --format='value(status.url)')"
echo
echo "Done. Open $url on your phone and sign in. Then check another Google account (or a private"
echo "window without signing in) is refused."
