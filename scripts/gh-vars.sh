#!/usr/bin/env bash
# Owner-only: copy infra/bootstrap outputs into GitHub Actions repository variables
# (not secrets: none of these values are sensitive). Usage: scripts/gh-vars.sh
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
tofu -chdir="$root/infra/bootstrap" init -input=false >/dev/null
out() { tofu -chdir="$root/infra/bootstrap" output -raw "$1"; }

# name=output pairs (bash 3.2 compatible for macOS)
for pair in \
  GCP_PROJECT=project_id GCP_REGION=region APP_NAME=app_name \
  AR_REPO=artifact_registry_repo TF_STATE_BUCKET=state_bucket WIF_PROVIDER=wif_provider \
  SA_PREVIEW=sa_preview SA_DEPLOYER=sa_deployer SA_INFRA_PLAN=sa_infra_plan SA_INFRA_APPLY=sa_infra_apply; do
  name="${pair%%=*}"
  gh variable set "$name" --body "$(out "${pair#*=}")"
  echo "set $name"
done
echo "Done. Deploys stay off until these exist; CI picks them up on the next run."
