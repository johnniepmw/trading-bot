# 0003. Trunk-based delivery with auto-merge, staged deploys and automatic rollback

- **Status**: accepted
- **Date**: 2026-11-14
- **Deciders**: owner
- **Enforcement**: branch ruleset (`make github-setup`); `.github/workflows/ci.yml` and `deploy.yml`

## Context
Agents produce many small changes. The pipeline must turn each into a safe production
release without a human in the loop for routine work, and recover by itself when a
release is bad. Personal GitHub plans have auto-merge and rulesets (with Pro) but no
merge queue.

## Options considered
1. **Trunk-based, squash-merged short-lived branches, auto-merge on required checks** —
   simple history, fast flow; without a merge queue, PRs aren't re-tested against the
   latest main before merging.
2. **Require branches to be up to date** — catches semantic conflicts, but every merge
   forces rebases of other PRs, and Actions can't trigger CI on bot-updated branches
   without extra tokens.
3. **Release branches / manual promotion** — more control, much more human work.

## Decision
Option 1, plus defences for its gap: main's CI runs on every merge, and the deploy is
staged: build once → staging (smoke + e2e) → production candidate revision with no
traffic (smoke) → 100% traffic → five-minute watch → automatic rollback on failure.
The same image digest moves through every stage.

## Consequences
- Good: routine changes reach production in minutes with no human involvement; bad
  releases roll back in seconds.
- Bad / costs: rare semantic conflicts between concurrently merged PRs surface on main
  (caught by main CI or staging), not before merge.
- Revisit when: merge frequency makes conflicts common, or a merge queue becomes available.
