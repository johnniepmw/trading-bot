#!/usr/bin/env bash
# Deploy an image to the bot's Cloud Run job (Phase 2 step 6; ADR-0019).
# Usage: scripts/cloudrun-job-deploy.sh <staging|prod> <image@sha256:...>
# Env: GCP_PROJECT, GCP_REGION, APP_NAME, AR_REPO
#
# Changes only the job's image (OpenTofu owns everything else). Then runs a check
# execution and reads only its status, never its logs (ADR-0014): staging runs `smoke`,
# prod runs `run --dry-run` against prod's own ledger and broker. On failure the job goes
# back to the previous image. Prod first checks the deploy window: no execution running
# and the next scheduled run more than 30 minutes away (a job update never touches a
# running execution, but the next run would start on the new image unchecked).
set -euo pipefail
env="${1:?usage: cloudrun-job-deploy.sh <staging|prod> <image@sha256:...>}"
image="${2:?image}"
project="${GCP_PROJECT:?}"; region="${GCP_REGION:?}"; app="${APP_NAME:?}"; repo="${AR_REPO:?}"
job="${app}-${env}"
case "$env" in staging|prod) ;; *) echo "deploy: unknown env $env" >&2; exit 2 ;; esac
[[ "$image" == "${repo}/${app}@sha256:"* ]] || { echo "deploy: expected ${repo}/${app}@sha256:..., got $image" >&2; exit 2; }
WINDOW_SECONDS="${WINDOW_SECONDS:-1800}"

gc() { gcloud "$@" --project "$project" --quiet; }

check_window() {
  local running state next now
  running="$(gc run jobs executions list --job "$job" --region "$region" \
    --filter='NOT status.completionTime:*' --format='value(metadata.name)')"
  [ -z "$running" ] || { echo "deploy: refused: an execution of $job is running ($running); try again later" >&2; exit 75; }
  read -r state next < <(gc scheduler jobs describe "${job}-daily" --location "$region" --format='value(state,scheduleTime)')
  if [ "$state" = "ENABLED" ]; then
    now="$(date -u +%s)"
    if [ $(( $(date -u -d "$next" +%s) - now )) -lt "$WINDOW_SECONDS" ]; then
      echo "deploy: refused: the next scheduled run ($next) is within $((WINDOW_SECONDS / 60)) minutes" >&2
      exit 75
    fi
  fi
}

[ "$env" = prod ] && check_window

previous="$(gc run jobs describe "$job" --region "$region" \
  --format='value(spec.template.spec.template.spec.containers[0].image)')"
[ -n "$previous" ] || { echo "deploy: could not read $job's current image" >&2; exit 1; }
echo "deploy: $job ${previous##*@} -> ${image##*@}"
gc run jobs update "$job" --region "$region" --image "$image"

check_args="smoke"
[ "$env" = prod ] && check_args="run,--dry-run"
if ! gc run jobs execute "$job" --region "$region" --args "$check_args" --wait; then
  echo "deploy: the check execution failed; rolling $job back to ${previous##*@}" >&2
  gc run jobs update "$job" --region "$region" --image "$previous"
  exit 1
fi

# Remember the image we replaced, for `ops rollback` (only images from our registry).
if [[ "$previous" == "${repo}/${app}@sha256:"* ]]; then
  gc artifacts docker tags add "$previous" "${repo}/${app}:previous-${env}"
fi
echo "deploy: $job now runs ${image##*@}"
