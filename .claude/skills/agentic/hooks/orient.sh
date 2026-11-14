#!/usr/bin/env bash
# SessionStart hook. Prints a short, factual orientation that is added to Claude's
# context: branch state, active plans and recent history. Never fails, never uses the
# network.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$HERE/lib.sh"

# shellcheck disable=SC2034  # read by json_field in lib.sh
HOOK_INPUT="$(cat)"
cwd="$(json_field cwd)"
root="$(repo_root "${cwd:-${CLAUDE_PROJECT_DIR:-.}}")"
[ -z "$root" ] && exit 0
cd "$root" || exit 0

branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
dirty="$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
ahead="$(git rev-list --count origin/main..HEAD 2>/dev/null || echo '?')"
kit_version="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$HERE/../.claude-plugin/plugin.json" 2>/dev/null | head -n 1)"

echo "Repository orientation (generated at session start by the agentic kit ${kit_version:-}):"
echo "- Branch: ${branch}; ${ahead} commit(s) ahead of origin/main (as last fetched); ${dirty} uncommitted path(s)."

if compgen -G "docs/exec-plans/active/*.md" >/dev/null 2>&1; then
  echo "- Active exec plans:"
  for f in docs/exec-plans/active/*.md; do
    title="$(sed -n '1s/^# *//p' "$f")"
    status="$(sed -n 's/^- \*\*Status\*\*: *//p' "$f" | head -n 1)"
    echo "  - $f — ${title:-untitled} — status: ${status:-unknown}"
  done
else
  echo "- No active exec plans."
fi

echo "- Recent commits on this branch:"
git log --oneline -5 2>/dev/null | sed 's/^/  /'

if [ -f AGENTS.md ]; then
  echo "- AGENTS.md is the operating manual for this repo; the deliver skill runs the full loop."
fi
exit 0
