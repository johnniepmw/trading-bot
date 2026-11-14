#!/usr/bin/env bash
# Smoke test a deployment: GET <url>/healthz must return 200 with status "ok" and, if
# given, the expected version. Retries for up to ~60s while a new revision warms up.
# Usage: scripts/smoke.sh <base-url> [expected-version]
# Env: SMOKE_TOKEN (optional bearer token for private services), SMOKE_ATTEMPTS (default 12)
set -euo pipefail

base="${1:?usage: smoke.sh <base-url> [expected-version]}"
want_version="${2:-}"
attempts="${SMOKE_ATTEMPTS:-12}"
url="${base%/}/healthz"
auth=()
[ -n "${SMOKE_TOKEN:-}" ] && auth=(-H "Authorization: Bearer ${SMOKE_TOKEN}")

for i in $(seq 1 "$attempts"); do
  body="$(curl -fsS --max-time 10 ${auth[@]+"${auth[@]}"} "$url" 2>/dev/null || true)"
  if [ -n "$body" ]; then
    status="$(printf '%s' "$body" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))' 2>/dev/null || true)"
    version="$(printf '%s' "$body" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("version",""))' 2>/dev/null || true)"
    if [ "$status" = "ok" ] && { [ -z "$want_version" ] || [ "$version" = "$want_version" ]; }; then
      echo "smoke: OK ${url} version=${version} (attempt ${i})"
      exit 0
    fi
    echo "smoke: attempt ${i}: status='${status}' version='${version}' (want '${want_version:-any}')"
  else
    echo "smoke: attempt ${i}: no healthy response from ${url}"
  fi
  sleep 5
done
echo "smoke: FAILED ${url}" >&2
exit 1
