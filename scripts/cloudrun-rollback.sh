#!/usr/bin/env bash
# Route 100% of traffic to the previous ready revision (or a named one), then smoke test.
# Usage: scripts/cloudrun-rollback.sh <staging|prod> [revision]
# Env: GCP_PROJECT, GCP_REGION, APP_NAME
set -euo pipefail
env="${1:?env}"; target="${2:-}"
project="${GCP_PROJECT:?}"; region="${GCP_REGION:?}"; service="${APP_NAME:?}-${env}"

current="$(gcloud run services describe "$service" --project "$project" --region "$region" --format=json \
  | jq -r '[.status.traffic[] | select(.percent == 100)][0].revisionName // empty')"

if [ -z "$target" ]; then
  # Newest ready revision created before the current one.
  target="$(gcloud run revisions list --service "$service" --project "$project" --region "$region" \
      --format=json --sort-by='~metadata.creationTimestamp' \
    | jq -r --arg cur "$current" '
        [ .[] | select(.status.conditions[]? | select(.type == "Ready" and .status == "True")) | .metadata.name ]
        | (index($cur)) as $i | if $i == null then .[0] else .[$i + 1] end // empty')"
fi
[ -n "$target" ] || { echo "rollback: no earlier ready revision found" >&2; exit 1; }

echo "rollback: $service $current -> $target"
gcloud run services update-traffic "$service" --project "$project" --region "$region" \
  --to-revisions "${target}=100" --quiet
url="$(gcloud run services describe "$service" --project "$project" --region "$region" --format='value(status.url)')"
bash scripts/smoke.sh "$url"
echo "rollback: done; $service serving $target"
