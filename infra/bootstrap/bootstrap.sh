#!/usr/bin/env bash
# Owner-only, once per app: create the GCP foundation with local state, then move the
# state into the bucket it just created. Re-running is safe (plan/apply against the
# remote state). Needs: gcloud auth application-default login, infra/bootstrap/bootstrap.tfvars
set -euo pipefail
cd "$(dirname "$0")"
[ -f bootstrap.tfvars ] || { echo "copy bootstrap.tfvars.example to bootstrap.tfvars and fill it in" >&2; exit 2; }

if [ -f backend.tf ]; then
  bucket="$(sed -n 's/.*bucket *= *"\([^"]*\)".*/\1/p' backend.tf)"
  tofu init -input=false -backend-config="bucket=$bucket"
  tofu apply -input=false -var-file=bootstrap.tfvars
  exit 0
fi

tofu init -input=false
tofu apply -input=false -var-file=bootstrap.tfvars
bucket="$(tofu output -raw state_bucket)"

cat > backend.tf <<EOF
terraform {
  backend "gcs" {
    bucket = "$bucket"
    prefix = "bootstrap"
  }
}
EOF
tofu init -input=false -migrate-state -force-copy
rm -f terraform.tfstate terraform.tfstate.backup
echo
echo "Bootstrap complete. State is in gs://$bucket/bootstrap."
echo "Commit infra/bootstrap/backend.tf, then run: make gh-vars"
