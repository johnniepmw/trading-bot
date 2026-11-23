#!/usr/bin/env bash
# Deploy an image to the bot's Cloud Run job (Phase 2 step 6; ADR-0019).
# Usage: scripts/cloudrun-job-deploy.sh <staging|prod> <image@sha256:...>
# Env: GCP_PROJECT, GCP_REGION, APP_NAME; CHECK_TIMEOUT (seconds, default 900)
#
# Changes only the job's image (OpenTofu owns everything else). Then runs a check
# execution and reads only its status, never its logs (ADR-0014): staging runs `smoke`,
# prod runs `run --dry-run --deploy-check` against prod's own ledger and broker (a business
# stop passes; a fault in the image fails). Any way out other than a
# passed check (failure, timeout, cancellation of this script) puts the previous image
# back. Prod first checks the deploy window: no execution running, and the scheduler
# either paused or its next run more than 30 minutes away.
set -euo pipefail
env="${1:?usage: cloudrun-job-deploy.sh <staging|prod> <image@sha256:...>}"
image="${2:?image}"
project="${GCP_PROJECT:?}"; region="${GCP_REGION:?}"; app="${APP_NAME:?}"
job="${app}-${env}"
case "$env" in staging|prod) ;; *) echo "deploy: unknown env $env" >&2; exit 2 ;; esac
# Our registry, derived from the project itself (never from a separate, mutable variable).
registry="${region}-docker.pkg.dev/${project}/${app}/${app}"
[[ "$image" =~ ^${registry//./\\.}@sha256:[0-9a-f]{64}$ ]] \
  || { echo "deploy: expected ${registry}@sha256:<64 hex>, got $image" >&2; exit 2; }
WINDOW_SECONDS="${WINDOW_SECONDS:-1800}"
CHECK_TIMEOUT="${CHECK_TIMEOUT:-900}"  # well inside the window and the workflow's timeout

gc() { gcloud "$@" --project "$project" --quiet; }

# Executions of the job that haven't finished (no completion time). Filtered here, not with
# gcloud's --filter: newer gcloud releases reject `status.completionTime:*` (2026-11-23).
unfinished() {
  gc run jobs executions list --job "$job" --region "$region" --limit 50 \
    --format='value(metadata.name,status.completionTime)' | awk -F'\t' 'NF && $2 == "" {print $1}'
}

check_window() {
  local running state next
  running="$(unfinished)"
  [ -z "$running" ] || { echo "deploy: refused: an execution of $job is running ($running); try again later" >&2; exit 75; }
  IFS=$'\t' read -r state next < <(gc scheduler jobs describe "${job}-daily" --location "$region" \
    --format='value(state,scheduleTime)') || true
  [ "$state" = "PAUSED" ] && return 0
  # Anything but plainly paused needs a readable next run time that is far enough away.
  if ! next_s="$(date -u -d "${next:-invalid}" +%s 2>/dev/null)" \
     || [ $(( next_s - $(date -u +%s) )) -lt "$WINDOW_SECONDS" ]; then
    echo "deploy: refused: the schedule is ${state:-unknown} and its next run (${next:-unknown}) is not clearly more than $((WINDOW_SECONDS / 60)) minutes away" >&2
    exit 75
  fi
}

[ "$env" = prod ] && check_window

previous="$(gc run jobs describe "$job" --region "$region" \
  --format='value(spec.template.spec.template.spec.containers[0].image)')"
[ -n "$previous" ] || { echo "deploy: could not read $job's current image" >&2; exit 1; }
echo "deploy: $job ${previous##*@} -> ${image##*@}"

checked=no
restore() {
  [ "$checked" = yes ] && return 0
  echo "deploy: no passed check; cancelling $job's unfinished executions and going back to ${previous##*@}" >&2
  for name in $(unfinished 2>/dev/null || true); do
    gc run jobs executions cancel "$name" --region "$region" || true
  done
  gc run jobs update "$job" --region "$region" --image "$previous" \
    || echo "deploy: RESTORE FAILED: $job may be on an unchecked image; roll back with ops" >&2
}
trap restore EXIT
trap 'exit 1' INT TERM
gc run jobs update "$job" --region "$region" --image "$image"

check_args="smoke"
[ "$env" = prod ] && check_args="run,--dry-run,--deploy-check"  # a business stop passes (ADR-0019)
if ! timeout "$CHECK_TIMEOUT" gcloud run jobs execute "$job" --region "$region" --args "$check_args" --wait \
    --project "$project" --quiet; then
  echo "deploy: the check execution failed or timed out" >&2
  exit 1
fi
checked=yes

# Remember the image we replaced, for `ops rollback` (only our own, and only a different one).
if [[ "$previous" == "${registry}@sha256:"* && "$previous" != "$image" ]]; then
  gc artifacts docker tags add "$previous" "${registry}:previous-${env}" \
    || echo "deploy: warning: could not tag the previous image for rollback; name its digest instead" >&2
fi
echo "deploy: $job now runs ${image##*@}"
