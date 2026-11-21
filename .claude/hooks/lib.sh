#!/usr/bin/env bash
# Shared helpers for agentic kit hooks. Sourced, not executed.

# json_field <dotted.path>  — reads hook JSON from $HOOK_INPUT and prints the field ("" if absent).
json_field() {
  local path="$1"
  if command -v jq >/dev/null 2>&1; then
    printf '%s' "$HOOK_INPUT" | jq -r --arg p "$path" 'getpath($p | split(".")) // "" | tostring' 2>/dev/null
  elif command -v python3 >/dev/null 2>&1; then
    HOOK_FIELD="$path" python3 -c '
import json, os, sys
data = json.loads(sys.stdin.read() or "{}")
for key in os.environ["HOOK_FIELD"].split("."):
    data = data.get(key, "") if isinstance(data, dict) else ""
print("" if data is None else data, end="")
' <<<"$HOOK_INPUT" 2>/dev/null
  else
    echo "agentic hooks need jq or python3" >&2
    return 1
  fi
}

# block <reason>  — refuse the tool call; stderr goes back to Claude.
block() {
  {
    echo "BLOCKED by agentic guard: $1"
    echo "Why: see AGENTS.md > Non-negotiables. If this is truly needed, use the escalate skill so the owner decides."
  } >&2
  exit 2
}

# repo_root <dir>  — prints the git top level for dir, or nothing.
repo_root() {
  git -C "${1:-.}" rev-parse --show-toplevel 2>/dev/null || true
}

# once  — true for the first of identical runs of a hook, false for a duplicate.
# A hook can be wired twice (a repo's .claude/settings.json and an installed agentic
# plugin); both copies then receive the same event at the same moment. The event JSON plus
# the transcript's size identifies the event, so a later event always runs. Only the slow
# or chatty hooks use this; the guards always run, because a duplicate guard is harmless.
once() {
  local dir t size=""
  dir="${TMPDIR:-/tmp}/agentic-hooks-$(id -u)"
  t="$(json_field transcript_path)"
  [ -n "$t" ] && size="$(wc -c <"$t" 2>/dev/null | tr -d ' ')"
  mkdir -p "$dir" 2>/dev/null || return 0
  find "$dir" -mindepth 1 -maxdepth 1 -mmin +1440 -exec rm -rf {} + 2>/dev/null
  # Bash's noclobber create (O_EXCL) is atomic; `mkdir` isn't on every coreutils build.
  ( set -C; : >"$dir/$(printf '%s %s' "$HOOK_INPUT" "$size" | cksum | tr ' ' '-')" ) 2>/dev/null
}
