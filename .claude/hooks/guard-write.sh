#!/usr/bin/env bash
# PreToolUse(Read|Edit|Write|NotebookEdit) guard. Blocks reading or writing secret and
# infrastructure-state files, and writing git internals or the kit's own files (those
# listed in .claude/kit.lock change upstream, then sync).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$HERE/lib.sh"

# shellcheck disable=SC2034  # read by json_field in lib.sh
HOOK_INPUT="$(cat)"
path="$(json_field tool_input.file_path)" || block "could not read the hook input (fails closed)."
[ -z "$path" ] && path="$(json_field tool_input.notebook_path)"
[ -z "$path" ] && exit 0
tool="$(json_field tool_name)"

cwd="$(json_field cwd)"
root="$(repo_root "${cwd:-.}")"
rel="$path"
[ -n "$root" ] && rel="${path#"$root"/}"
base="$(basename -- "$path")"

# Secrets and state: neither read nor written. Values never enter the session.
case "$base" in
  .env.example|.env.sample|.env.template) ;;  # documented placeholders are fine
  .env|.env.*|*.pem|*.key|*.p12|*.pfx|id_rsa*|id_ed25519*|*.tfstate|*.tfstate.*|credentials.json|service-account*.json)
    block "reading or writing a secrets or state file ($base). Secret values never enter the session; the owner sets them (make secret-set). State is managed by OpenTofu." ;;
esac
case "$rel" in
  secrets/*)
    block "reading or writing the repo's secrets/ directory ($rel)." ;;
esac

[ "$tool" = "Read" ] && exit 0

case "/$rel" in
  */.git/*)
    block "editing git internals ($rel)." ;;
esac
lock="$root/.claude/kit.lock"
if [ -n "$root" ] && [ -f "$lock" ]; then
  if [ "$rel" = ".claude/kit.lock" ] || awk -v p="$rel" '$1 !~ /^#/ && $2 == p { found = 1 } END { exit !found }' "$lock"; then
    block "editing an agentic kit file ($rel). Change it in the kit repo (johnniepmw/agentic-dev-kit), then run make kit-sync."
  fi
fi

exit 0
