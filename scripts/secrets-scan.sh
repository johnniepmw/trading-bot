#!/usr/bin/env bash
# Secret scan of the commits under review (`make secrets-scan`), run from the repo root.
# SECRETS_SCAN_RANGE is one git revision or range: HEAD by default (this branch's history,
# not every fetched branch); CI sets base..head on pull requests. Fails closed in CI.
set -euo pipefail

range="${SECRETS_SCAN_RANGE:-HEAD}"

if ! command -v gitleaks >/dev/null 2>&1; then
  echo "secrets-scan: gitleaks not installed (make setup)"
  if [ -n "${CI:-}" ]; then exit 1; fi
  exit 0
fi

case "$range" in
  "" | -* | *[[:space:]]*)
    echo "secrets-scan: SECRETS_SCAN_RANGE must be one revision or range, got '$range'" >&2
    exit 1
    ;;
esac

# gitleaks prints git's error but exits 0 ("0 commits scanned") when the range doesn't
# resolve, so check it here or a bad range would pass silently.
if ! git rev-list --quiet "$range" -- >/dev/null; then
  echo "secrets-scan: cannot resolve '$range' (is the history fetched?)" >&2
  exit 1
fi

echo "secrets-scan: scanning $range"
# -m: also scan what merge commits change, so a secret added only in a merge is caught
exec gitleaks git --no-banner --redact --config .gitleaks.toml --log-opts="-m $range" .
