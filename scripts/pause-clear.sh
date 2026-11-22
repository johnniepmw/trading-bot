#!/usr/bin/env bash
# Clear every pause request for one environment (Phase 2 step 7b; monitoring AC6): new
# orders resume at the next check. Owner only: the ops workflow sets OWNER from the actor.
# Usage: scripts/pause-clear.sh <staging|prod>
# Env: GCP_PROJECT; OWNER=true
set -euo pipefail
env="${1:?usage: pause-clear.sh <staging|prod>}"
project="${GCP_PROJECT:?}"
case "$env" in staging|prod) ;; *) echo "pause-clear: unknown env $env" >&2; exit 2 ;; esac
[ "${OWNER:-false}" = true ] || { echo "pause-clear: only the owner may clear a pause" >&2; exit 2; }

errors="$(mktemp)"
trap 'rm -f "$errors"' EXIT
if gcloud storage rm "gs://${project}-pause-${env}/**" --project "$project" --quiet 2>"$errors"; then
  echo "pause-clear: $env pause requests cleared; new orders resume at the next check"
elif grep -qiE 'matched no objects|No URLs matched' "$errors"; then
  echo "pause-clear: $env had no pause requests"
else
  cat "$errors" >&2
  echo "pause-clear: could not clear; $env is still paused" >&2
  exit 1
fi
