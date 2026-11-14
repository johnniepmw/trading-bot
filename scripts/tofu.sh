#!/usr/bin/env bash
# Run OpenTofu for one environment with the right backend and variables.
# Usage: scripts/tofu.sh <staging|prod> <plan|apply|destroy> [extra tofu args]
# Needs: TF_STATE_BUCKET (or infra/bootstrap outputs), GCP credentials.
set -euo pipefail
env="${1:?usage: tofu.sh <staging|prod> <plan|apply|destroy>}"
action="${2:?usage: tofu.sh <staging|prod> <plan|apply|destroy>}"
shift 2
root="$(git rev-parse --show-toplevel)"
dir="$root/infra/envs/$env"
[ -d "$dir" ] || { echo "no such environment: $env" >&2; exit 2; }

bucket="${TF_STATE_BUCKET:-}"
if [ -z "$bucket" ] && [ -f "$root/infra/bootstrap/backend.tf" ]; then
  bucket="$(sed -n 's/.*bucket *= *"\([^"]*\)".*/\1/p' "$root/infra/bootstrap/backend.tf")"
fi
[ -n "$bucket" ] || { echo "set TF_STATE_BUCKET or run make infra-bootstrap first" >&2; exit 2; }

export TF_VAR_state_bucket="$bucket"
tofu -chdir="$dir" init -input=false -reconfigure \
  -backend-config="bucket=$bucket" -backend-config="prefix=envs/$env" >/dev/null

# Staging is disposable: apply/destroy without a prompt. Prod asks for confirmation
# unless the caller (CI) passes -auto-approve explicitly.
auto=()
[ "$env" = staging ] && auto=(-auto-approve)

case "$action" in
  plan)    tofu -chdir="$dir" plan -input=false -lock-timeout=120s "$@" ;;
  apply)   tofu -chdir="$dir" apply -lock-timeout=120s ${auto[@]+"${auto[@]}"} "$@" ;;
  destroy) tofu -chdir="$dir" destroy -lock-timeout=120s ${auto[@]+"${auto[@]}"} "$@" ;;
  *) echo "unknown action: $action" >&2; exit 2 ;;
esac
