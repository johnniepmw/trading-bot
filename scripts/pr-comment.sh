#!/usr/bin/env bash
# Create or update a single PR comment identified by a hidden marker, so each workflow
# keeps one up-to-date comment instead of piling up new ones.
# Usage: scripts/pr-comment.sh <pr-number> <marker> <body-file>
# Env: GH_TOKEN, GITHUB_REPOSITORY
set -euo pipefail
pr="$1"; marker="$2"; body_file="$3"
repo="${GITHUB_REPOSITORY:?}"
tag="<!-- ${marker} -->"
body="$(printf '%s\n%s' "$tag" "$(cat "$body_file")")"

id="$(gh api --paginate "repos/$repo/issues/$pr/comments" \
  --jq ".[] | select(.user.type == \"Bot\" and (.body | contains(\"$tag\"))) | .id" | tail -n 1)"
if [ -n "$id" ]; then
  gh api -X PATCH "repos/$repo/issues/comments/$id" -f body="$body" >/dev/null
else
  gh api -X POST "repos/$repo/issues/$pr/comments" -f body="$body" >/dev/null
fi
