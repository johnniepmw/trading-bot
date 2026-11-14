#!/usr/bin/env bash
# Deploy an image digest to one environment's Cloud Run service with a safety net:
#   new revision (no traffic, tagged) → smoke the tag URL → 100% traffic → watch →
#   automatic rollback to the previous revision if anything fails after the switch.
# Usage: scripts/cloudrun-deploy.sh <staging|prod> <image@sha256:...> <version>
# Env: GCP_PROJECT, GCP_REGION, APP_NAME, WATCH_SECONDS (default 60 staging / 300 prod)
set -euo pipefail

env="${1:?env}"; image="${2:?image}"; version="${3:?version}"
project="${GCP_PROJECT:?}"; region="${GCP_REGION:?}"; app="${APP_NAME:?}"
service="${app}-${env}"
tag="c-${version}"
watch="${WATCH_SECONDS:-$([ "$env" = prod ] && echo 300 || echo 60)}"
run() { gcloud run "$@" --project "$project" --region "$region" --quiet; }
describe() { gcloud run services describe "$service" --project "$project" --region "$region" --format=json; }

prev="$(describe | jq -r '[.status.traffic[] | select(.percent == 100)][0].revisionName // empty')"
echo "deploy: $service currently serving: ${prev:-none}"

run deploy "$service" --image "$image" --no-traffic --tag "$tag" \
  --update-env-vars "APP_VERSION=${version},APP_ENV=${env},FLAGS_BUCKET=${project}-flags-${env}"
tag_url="$(describe | jq -r --arg t "$tag" '.status.traffic[] | select(.tag == $t) | .url')"
echo "deploy: candidate at $tag_url"

if ! bash scripts/smoke.sh "$tag_url" "$version"; then
  echo "deploy: candidate failed smoke; production traffic untouched" >&2
  exit 1
fi

run services update-traffic "$service" --to-tags "${tag}=100"
service_url="$(describe | jq -r '.status.url')"

rollback() {
  echo "deploy: FAILED after traffic switch; rolling back to ${prev:-<none>}" >&2
  if [ -n "$prev" ]; then run services update-traffic "$service" --to-revisions "${prev}=100"; fi
  exit 1
}

deadline=$(( $(date +%s) + watch ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  SMOKE_ATTEMPTS=3 bash scripts/smoke.sh "$service_url" "$version" || rollback
  sleep 20
done

# Keep the current and two previous candidate tags; drop older ones.
old_tags="$(describe | jq -r --arg t "$tag" '[.status.traffic[] | select(.tag != null and (.tag | startswith("c-")) and .tag != $t) | .tag] | .[:-2] | join(",")')"
if [ -n "$old_tags" ]; then run services update-traffic "$service" --remove-tags "$old_tags" || true; fi

echo "deploy: $service now serving $version ($service_url)"
