#!/usr/bin/env bash
# PreToolUse(Edit|Write|NotebookEdit) guard. Blocks writing secret files, git internals,
# infrastructure state, and the vendored kit (which is changed upstream, then synced).
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$HERE/lib.sh"

# shellcheck disable=SC2034  # read by json_field in lib.sh
HOOK_INPUT="$(cat)"
path="$(json_field tool_input.file_path)"
[ -z "$path" ] && path="$(json_field tool_input.notebook_path)"
[ -z "$path" ] && exit 0

base="$(basename -- "$path")"

case "$base" in
  .env.example|.env.sample|.env.template) ;;  # documented placeholders are fine
  .env|.env.*|*.pem|*.key|*.p12|*.pfx|id_rsa*|id_ed25519*|*.tfstate|*.tfstate.*|credentials.json|service-account*.json)
    block "writing a secrets or state file ($base). Secrets are set by the owner (make secret-set); state is managed by OpenTofu." ;;
esac

cwd="$(json_field cwd)"
root="$(repo_root "${cwd:-.}")"
rel="$path"
[ -n "$root" ] && rel="${path#"$root"/}"

case "/$rel" in
  */.git/*)
    block "editing git internals ($rel)." ;;
  */.claude/skills/agentic/*)
    block "editing the vendored agentic kit ($rel). Change it in the kit repo (johnniepmw/agentic-dev-kit) and run scripts/kit-sync.sh." ;;
esac
case "$rel" in
  secrets/*)
    block "writing into the repo's secrets/ directory ($rel)." ;;
esac

exit 0
