#!/usr/bin/env bash
# Vendor the agentic kit plugin into .claude/skills/agentic (a repository plugin that
# Claude Code loads as `agentic`). The kit repo is the source of truth; never edit the
# vendored copy here.
# Usage: scripts/kit-sync.sh [--check] [--ref <tag-or-branch>]
# Env: KIT_REPO (default johnniepmw/agentic-dev-kit)
set -euo pipefail

KIT_REPO="${KIT_REPO:-johnniepmw/agentic-dev-kit}"
DEST=".claude/skills/agentic"
ref=""
check=0
while [ $# -gt 0 ]; do
  case "$1" in
    --check) check=1 ;;
    --ref) ref="$2"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

root="$(git rev-parse --show-toplevel)"
cd "$root"
current="$(cat "$DEST/.kit-ref" 2>/dev/null || echo none)"

if [ -z "$ref" ]; then
  # Latest release tag, falling back to the default branch head.
  # REST only (gh's GraphQL calls are blocked in Claude Code cloud sessions).
  ref="$(gh api "repos/$KIT_REPO/releases/latest" --jq .tag_name 2>/dev/null || true)"
  [ -z "$ref" ] && ref="$(gh api "repos/$KIT_REPO/commits/HEAD" --jq .sha)"
fi

if [ "$check" -eq 1 ]; then
  if [ "$current" = "$ref" ]; then echo "kit-sync: up to date ($current)"; exit 0; fi
  echo "kit-sync: update available: $current -> $ref"; exit 10
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
url="https://github.com/$KIT_REPO.git"
git clone --quiet --depth 1 --branch "$ref" "$url" "$tmp/kit" 2>/dev/null \
  || { git clone --quiet "$url" "$tmp/kit" && git -C "$tmp/kit" checkout --quiet "$ref"; }

rm -rf "$DEST"
mkdir -p "$(dirname "$DEST")"
cp -R "$tmp/kit/plugins/agentic" "$DEST"
printf '%s\n' "$ref" > "$DEST/.kit-ref"
chmod +x "$DEST"/hooks/*.sh
echo "kit-sync: vendored $KIT_REPO@$ref into $DEST (was $current)"
if [ -f "$tmp/kit/CHANGELOG.md" ]; then
  echo "--- kit changelog (top) ---"
  sed -n '1,40p' "$tmp/kit/CHANGELOG.md"
fi
