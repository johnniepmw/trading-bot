#!/usr/bin/env bash
# Install the agentic kit (the kit repo's plugins/agentic/) into this repo's .claude/, so
# Claude Code loads it as ordinary project files in every surface, cloud sessions
# included (they don't load plugins from a repository). The mapping is one to one:
#   skills/ → .claude/skills/   agents/ → .claude/agents/
#   hooks/*.sh → .claude/hooks/  scripts/ → .claude/scripts/
# .claude/settings.json wires the hooks; .claude/kit.lock lists every kit file with its
# checksum. Kit files change in the kit repo, never here.
#
# Usage: scripts/kit-sync.sh [--check | --verify] [--ref <branch, tag or commit>]
#   (no flag)  install or update from the kit's main branch (or --ref)
#   --check    exit 10 when the kit has changes this repo doesn't have yet (network)
#   --verify   exit 1 when a kit file here differs from .claude/kit.lock (offline)
# Env: KIT_REPO (default johnniepmw/agentic-dev-kit), KIT_URL (default its GitHub URL).
# In a Claude Code cloud session the kit repo must be attached to the session.
set -euo pipefail

KIT_REPO="${KIT_REPO:-johnniepmw/agentic-dev-kit}"
KIT_URL="${KIT_URL:-https://github.com/$KIT_REPO.git}"
LOCK=".claude/kit.lock"
mode=sync
ref=""
while [ $# -gt 0 ]; do
  case "$1" in
    --check) mode=check ;;
    --verify) mode=verify ;;
    --ref) ref="$2"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

cd "$(git rev-parse --show-toplevel)"
sha256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }
installed() { if [ -f "$LOCK" ]; then awk '$1 !~ /^#/ { print $2 }' "$LOCK"; fi; }
nl=$'\n'
listed() { case "$nl$1$nl" in *"$nl$2$nl"*) return 0 ;; esac; return 1; }  # listed <list> <line>

if [ "$mode" = verify ]; then
  [ -f "$LOCK" ] || { echo "kit-check: $LOCK is missing; run make kit-sync" >&2; exit 1; }
  if grep -v '^#' "$LOCK" | sha256 -c --quiet; then
    echo "kit-check: kit files match $LOCK"
    exit 0
  fi
  echo "kit-check: kit files were changed in this repo. Change them in $KIT_REPO, then run make kit-sync." >&2
  exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
if [ -z "$ref" ]; then
  git clone --quiet --depth 1 "$KIT_URL" "$tmp/kit"
else
  git clone --quiet --depth 1 --branch "$ref" "$KIT_URL" "$tmp/kit" 2>/dev/null \
    || { git clone --quiet "$KIT_URL" "$tmp/kit" && git -C "$tmp/kit" checkout --quiet "$ref"; }
fi
commit="$(git -C "$tmp/kit" rev-parse HEAD)"
src="$tmp/kit/plugins/agentic"
version="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$src/.claude-plugin/plugin.json" | head -n 1)"
# hooks.json is the plugin's own wiring; here .claude/settings.json does that job.
wanted="$(cd "$src" && find skills agents hooks scripts -type f ! -path hooks/hooks.json | LC_ALL=C sort)"

have="$(installed)"
changed=0
[ -d .claude/skills/agentic ] && changed=1  # the old layout (kit v0.1), replaced below
for f in $wanted; do cmp -s "$src/$f" ".claude/$f" || changed=1; done
for f in $have; do listed "$wanted" "${f#.claude/}" || changed=1; done

if [ "$changed" -eq 0 ]; then
  echo "kit-sync: up to date with $KIT_REPO@${commit:0:12} (kit $version)"
  exit 0
fi
if [ "$mode" = check ]; then
  echo "kit-sync: update available from $KIT_REPO@${commit:0:12} (kit $version)"
  exit 10
fi

# Never overwrite a project's own file that happens to sit at a kit path.
for f in $wanted; do
  if [ -e ".claude/$f" ] && ! listed "$have" ".claude/$f"; then
    echo "kit-sync: .claude/$f exists and isn't a kit file; rename or remove it first." >&2
    exit 1
  fi
done

for f in $have; do rm -f "$f"; done
rm -rf .claude/skills/agentic
for f in $wanted; do
  mkdir -p ".claude/$(dirname "$f")"
  cp "$src/$f" ".claude/$f"
done
chmod +x .claude/hooks/*.sh .claude/scripts/*.sh
find .claude/skills .claude/agents .claude/hooks .claude/scripts -type d -empty -delete 2>/dev/null || true
{
  echo "# Agentic kit $version from $KIT_REPO@$commit, installed by scripts/kit-sync.sh."
  echo "# These files change in the kit repo, then make kit-sync; make kit-check verifies them."
  for f in $wanted; do sha256 ".claude/$f"; done
} >"$LOCK"
echo "kit-sync: installed $KIT_REPO@${commit:0:12} (kit $version) into .claude/"
if [ -f "$tmp/kit/CHANGELOG.md" ]; then
  echo "--- kit changelog (top) ---"
  sed -n '1,30p' "$tmp/kit/CHANGELOG.md"
fi
