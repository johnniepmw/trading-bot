#!/usr/bin/env bash
# Change a remote flag override for one environment (used by the ops workflow).
# Usage: scripts/flag-override.sh <staging|prod> <kill|set|restore> <flag> [true|false]
# Writes gs://<project>-flags-<env>/flags.json: {"overrides": {...}, "updated": ..., "by": ...}
#
# Reads the current object by generation and writes with a generation match, so a
# concurrent change is never overwritten. Only a missing object starts from no overrides;
# any other read error aborts, so a transient failure can't wipe an existing kill override.
set -euo pipefail
env="${1:?env}"; action="${2:?action}"; flag="${3:?flag}"; value="${4:-}"
project="${GCP_PROJECT:?}"
object="gs://${project}-flags-${env}/flags.json"

jq -e --arg f "$flag" '.flags | has($f)' flags.json >/dev/null \
  || { echo "unknown flag '$flag' (not in flags.json)" >&2; exit 2; }

errors="$(mktemp)"
trap 'rm -f "$errors"' EXIT
if generation="$(gcloud storage objects describe "$object" --format='value(generation)' 2>"$errors")"; then
  current="$(gcloud storage cat "${object}#${generation}")"
  jq -e '.overrides | type == "object"' <<<"$current" >/dev/null \
    || { echo "flags ($env): the current object is malformed; fix it by hand" >&2; exit 1; }
elif grep -qiE 'not found|404|No URLs matched' "$errors"; then
  generation=0
  current='{"overrides":{}}'
else
  echo "flags ($env): could not read $object; nothing changed:" >&2
  cat "$errors" >&2
  exit 1
fi

case "$action" in
  kill)    new="$(jq --arg f "$flag" '.overrides[$f] = false' <<<"$current")" ;;
  set)     [[ "$value" =~ ^(true|false)$ ]] || { echo "set needs value true|false" >&2; exit 2; }
           new="$(jq --arg f "$flag" --argjson v "$value" '.overrides[$f] = $v' <<<"$current")" ;;
  restore) new="$(jq --arg f "$flag" 'del(.overrides[$f])' <<<"$current")" ;;
  *) echo "unknown action $action" >&2; exit 2 ;;
esac
new="$(jq --arg by "${GITHUB_ACTOR:-$USER}" --arg at "$(date -u +%FT%TZ)" '.updated = $at | .by = $by' <<<"$new")"

printf '%s\n' "$new" | gcloud storage cp - "$object" --content-type=application/json \
  --if-generation-match="$generation"
echo "flags ($env) before: $(jq -c .overrides <<<"$current")"
echo "flags ($env) after:  $(jq -c .overrides <<<"$new")"
