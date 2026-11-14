#!/usr/bin/env bash
# Owner-only: add a new version of a secret without the value touching shell history,
# the repo or the agent. Usage: scripts/secret-set.sh <name> <staging|prod>
set -euo pipefail
name="${1:?usage: secret-set.sh <name> <staging|prod>}"
env="${2:?usage: secret-set.sh <name> <staging|prod>}"
project="${GCP_PROJECT:-$(gcloud config get-value project 2>/dev/null)}"
secret="${env}-${name}"
[ -t 0 ] || { echo "run this in an interactive terminal" >&2; exit 2; }

gcloud secrets describe "$secret" --project "$project" >/dev/null \
  || { echo "secret $secret does not exist; add '$name' to the secrets list in infra/envs/$env and apply first" >&2; exit 1; }
read -r -s -p "Value for $secret (input hidden): " value; echo
[ -n "$value" ] || { echo "empty value, aborting" >&2; exit 1; }
printf '%s' "$value" | gcloud secrets versions add "$secret" --project "$project" --data-file=-
unset value
echo "Added a new version of $secret. Redeploy (gh workflow run deploy.yml) so instances read it."
