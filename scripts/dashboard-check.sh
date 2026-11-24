#!/usr/bin/env bash
# Read-only check of the dashboard service (ADR-0020): it shows account data and must never be
# public. Run by deploys and rollbacks (before and after moving its image), after every infra
# apply, and nightly. Prints a verdict; never changes anything.
# Usage: scripts/dashboard-check.sh <staging|prod>
# Env: GCP_PROJECT, GCP_REGION, APP_NAME
# Exit: 0 behind IAP, or no service yet; 1 PUBLIC (an allUsers/allAuthenticatedUsers invoker,
#       or invoker IAM disabled) or unreadable; 2 couldn't list services; 3 IAP is off (private,
#       but not behind the owner's sign-in: run scripts/dashboard-iap.sh)
set -euo pipefail
env="${1:?usage: dashboard-check.sh <staging|prod>}"
case "$env" in staging|prod) ;; *) echo "dashboard-check: unknown env $env" >&2; exit 2 ;; esac
project="${GCP_PROJECT:?}"; region="${GCP_REGION:?}"; app="${APP_NAME:?}"
service="${app}-dashboard-${env}"

gc() { gcloud "$@" --project "$project" --quiet; }

names="$(gc run services list --region "$region" --format='value(metadata.name)')" \
  || { echo "dashboard-check: couldn't list services in $region" >&2; exit 2; }
grep -qx "$service" <<<"$names" || { echo "dashboard-check: no $service yet"; exit 0; }

policy="$(gc run services get-iam-policy "$service" --region "$region" --format=json)" \
  || { echo "dashboard-check: DASHBOARD UNREADABLE: couldn't read $service's access policy" >&2; exit 1; }
described="$(gc run services describe "$service" --region "$region" --format=json)" \
  || { echo "dashboard-check: DASHBOARD UNREADABLE: couldn't read $service" >&2; exit 1; }
if grep -Eq '"(allUsers|allAuthenticatedUsers)"' <<<"$policy" \
   || grep -Eq '"(run\.googleapis\.com/invoker-iam-disabled|invokerIamDisabled)": *"?true' <<<"$described"; then
  echo "dashboard-check: DASHBOARD IS PUBLIC: $service lets anyone call it; remove that access now (dashboard-access.md)" >&2
  exit 1
fi
if ! grep -Eq '"(run\.googleapis\.com/iap-enabled|iapEnabled)": *"?true' <<<"$described"; then
  echo "dashboard-check: $service is private but IAP is off: the owner runs scripts/dashboard-iap.sh $env" >&2
  exit 3
fi
echo "dashboard-check: $service is behind IAP and not public"
