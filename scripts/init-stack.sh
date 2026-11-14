#!/usr/bin/env bash
# Apply a stack pack to this repo (run once, by the bootstrap skill).
# Usage: scripts/init-stack.sh <typescript-web|python>
# Copies stacks/<pack>/files into the repo root, adds the pack's tools to mise.toml,
# installs them, runs the pack's init (current dev dependencies + lockfile), adds its
# Dependabot entry, and removes stacks/. Needs network access.
set -euo pipefail

pack="${1:?usage: init-stack.sh <typescript-web|python>}"
root="$(git rev-parse --show-toplevel)"
cd "$root"
src="stacks/$pack"
[ -d "$src" ] || { echo "unknown pack: $pack (have: $(ls stacks))" >&2; exit 2; }
[ -f stack.mk ] && { echo "stack.mk exists: a stack is already applied" >&2; exit 2; }

# 1. Files (everything except the pack's control files).
( cd "$src/files" && find . -type f ) | while read -r f; do
  dest="${f#./}"
  if [ -e "$dest" ]; then echo "skip (exists): $dest"; continue; fi
  mkdir -p "$(dirname "$dest")"
  cp "$src/files/$f" "$dest"
done
cp "$src/stack.mk" stack.mk

# 2. Toolchain.
cat "$src/mise.tools" >> mise.toml
command -v mise >/dev/null && mise install

# 3. Dependencies at current versions, lockfile written.
bash "$src/init.sh"

# 4. Dependabot.
cat "$src/dependabot.yml.part" >> .github/dependabot.yml

# 5. Record the choice and clean up.
echo "$pack" > .stack
rm -rf stacks
echo "Applied stack pack '$pack'. Next: make setup && make check && make e2e"
