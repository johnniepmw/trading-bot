#!/usr/bin/env bash
# Put the bot's Cloud Run job back on the image it ran before the last deploy (or a named
# digest), then run a `smoke` execution (status only, never logs; ADR-0014).
# Usage: scripts/cloudrun-job-rollback.sh <staging|prod> [image@sha256:...]
# Env: GCP_PROJECT, GCP_REGION, APP_NAME, AR_REPO
set -euo pipefail
env="${1:?usage: cloudrun-job-rollback.sh <staging|prod> [image@sha256:...]}"
target="${2:-}"
project="${GCP_PROJECT:?}"; region="${GCP_REGION:?}"; app="${APP_NAME:?}"; repo="${AR_REPO:?}"
job="${app}-${env}"
case "$env" in staging|prod) ;; *) echo "rollback: unknown env $env" >&2; exit 2 ;; esac

gc() { gcloud "$@" --project "$project" --quiet; }

if [ -z "$target" ]; then
  digest="$(gc artifacts docker images describe "${repo}/${app}:previous-${env}" --format='value(image_summary.digest)')"
  [ -n "$digest" ] || { echo "rollback: no previous-${env} image recorded" >&2; exit 1; }
  target="${repo}/${app}@${digest}"
fi
[[ "$target" == "${repo}/${app}@sha256:"* ]] || { echo "rollback: expected ${repo}/${app}@sha256:..., got $target" >&2; exit 2; }

echo "rollback: $job -> ${target##*@}"
gc run jobs update "$job" --region "$region" --image "$target"
gc run jobs execute "$job" --region "$region" --args smoke --wait
echo "rollback: done; $job runs ${target##*@}"
