#!/usr/bin/env bash
# Put the bot's Cloud Run job back on the image it ran before the last deploy, then run a
# `smoke` execution (status only, never logs; ADR-0014). If the smoke fails, the job goes
# back to the image it had. A named digest skips the deploy's checks, so only the owner may
# name one (the ops workflow sets NAMED_ROLLBACK_ALLOWED from the actor).
# Usage: scripts/cloudrun-job-rollback.sh <staging|prod> [image@sha256:...]
# Env: GCP_PROJECT, GCP_REGION, APP_NAME; NAMED_ROLLBACK_ALLOWED=true for a named digest
set -euo pipefail
env="${1:?usage: cloudrun-job-rollback.sh <staging|prod> [image@sha256:...]}"
target="${2:-}"
project="${GCP_PROJECT:?}"; region="${GCP_REGION:?}"; app="${APP_NAME:?}"
job="${app}-${env}"
case "$env" in staging|prod) ;; *) echo "rollback: unknown env $env" >&2; exit 2 ;; esac
registry="${region}-docker.pkg.dev/${project}/${app}/${app}"

gc() { gcloud "$@" --project "$project" --quiet; }

if [ -n "$target" ] && [ "${NAMED_ROLLBACK_ALLOWED:-false}" != true ]; then
  echo "rollback: only the owner may name an image; leave it empty to use the one before the last deploy" >&2
  exit 2
fi
if [ -z "$target" ]; then
  digest="$(gc artifacts docker images describe "${registry}:previous-${env}" --format='value(image_summary.digest)')"
  [ -n "$digest" ] || { echo "rollback: no previous-${env} image recorded" >&2; exit 1; }
  target="${registry}@${digest}"
fi
[[ "$target" =~ ^${registry//./\\.}@sha256:[0-9a-f]{64}$ ]] \
  || { echo "rollback: expected ${registry}@sha256:<64 hex>, got $target" >&2; exit 2; }

current="$(gc run jobs describe "$job" --region "$region" \
  --format='value(spec.template.spec.template.spec.containers[0].image)')"
echo "rollback: $job ${current##*@} -> ${target##*@}"
gc run jobs update "$job" --region "$region" --image "$target"
if ! timeout "${CHECK_TIMEOUT:-900}" gcloud run jobs execute "$job" --region "$region" --args smoke --wait \
    --project "$project" --quiet; then
  echo "rollback: the smoke execution failed; going back to ${current##*@}" >&2
  gc run jobs update "$job" --region "$region" --image "$current"
  exit 1
fi
echo "rollback: done; $job runs ${target##*@}"
