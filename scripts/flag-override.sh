#!/usr/bin/env bash
# Change a remote flag override for one environment (used by the ops workflow).
# Usage: scripts/flag-override.sh <staging|prod> <kill|set|restore> <flag> [true|false]
# Writes gs://<project>-flags-<env>/flags.json: {"overrides": {...}, "updated": ..., "by": ...}
set -euo pipefail
env="${1:?env}"; action="${2:?action}"; flag="${3:?flag}"; value="${4:-}"
project="${GCP_PROJECT:?}"
object="gs://${project}-flags-${env}/flags.json"

jq -e --arg f "$flag" '.flags | has($f)' flags.json >/dev/null \
  || { echo "unknown flag '$flag' (not in flags.json)" >&2; exit 2; }

current="$(gcloud storage cat "$object" 2>/dev/null || echo '{"overrides":{}}')"
case "$action" in
  kill)    new="$(jq --arg f "$flag" '.overrides[$f] = false' <<<"$current")" ;;
  set)     [[ "$value" =~ ^(true|false)$ ]] || { echo "set needs value true|false" >&2; exit 2; }
           new="$(jq --arg f "$flag" --argjson v "$value" '.overrides[$f] = $v' <<<"$current")" ;;
  restore) new="$(jq --arg f "$flag" 'del(.overrides[$f])' <<<"$current")" ;;
  *) echo "unknown action $action" >&2; exit 2 ;;
esac
new="$(jq --arg by "${GITHUB_ACTOR:-$USER}" --arg at "$(date -u +%FT%TZ)" '.updated = $at | .by = $by' <<<"$new")"

printf '%s\n' "$new" | gcloud storage cp - "$object" --content-type=application/json
echo "flags ($env) before: $(jq -c .overrides <<<"$current")"
echo "flags ($env) after:  $(jq -c .overrides <<<"$new")"
