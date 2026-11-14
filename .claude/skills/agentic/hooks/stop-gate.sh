#!/usr/bin/env bash
# Stop hook. If the working tree has uncommitted changes and the repo defines
# `make check-fast`, the turn may only end when it passes. Exit 2 sends Claude back to
# work with the failure output. Escape hatch for deliberate escalation: create
# .agentic/allow-stop (single use).
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

if [ -f .agentic/allow-stop ]; then
  rm -f .agentic/allow-stop
  exit 0
fi

# Nothing uncommitted (ignoring scratch space) → nothing to gate.
changes="$(git status --porcelain --untracked-files=normal 2>/dev/null | grep -v ' \.agentic/' || true)"
[ -z "$changes" ] && exit 0

# Only gate repos that define the target.
[ -f Makefile ] || exit 0
make -n check-fast >/dev/null 2>&1 || exit 0

if ! out="$(make check-fast 2>&1)"; then
  {
    echo "Stop gate: \`make check-fast\` fails and there are uncommitted changes."
    echo "Fix the failures below before finishing. If you are deliberately stopping to"
    echo "escalate to the owner, create .agentic/allow-stop first (single use)."
    echo "----- last 60 lines -----"
    printf '%s\n' "$out" | tail -n 60
  } >&2
  exit 2
fi
exit 0
