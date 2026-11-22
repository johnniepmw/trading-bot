# Runbook: rebuild an environment from code

## Staging (owner)
Staging's ledger and lock buckets refuse to be destroyed (`prevent_destroy`), and setup
refuses where a ledger existed (staging-ledger-reset.md). So a rebuild keeps them:
```bash
make infra-plan ENV=staging   # check: no deletes of the ledger or lock buckets
make env-up ENV=staging       # recreates anything missing from infra/envs/staging
gh workflow run deploy.yml    # redeploys main to staging
```
A missing job, schedule (created paused), flags or pause bucket comes back from code. If
the ledger is gone too, see staging-ledger-reset.md. Anything that needed a manual step
is a bug: fix it in code and note it in the drill log below.

## Production (owner only)
Prod's job has deletion protection and its ledger and lock buckets `prevent_destroy`.
Recreate missing pieces with a PR (CI applies on merge) rather than destroying anything.
Secret values are re-added with `make secret-set` before a mount is applied
(`.claude/rules/infra.md`). If the ledger itself is damaged: ledger-restore.md.

## Bootstrap layer
`infra/bootstrap` (state bucket, registry, CI identities) is created once and protected
(`prevent_destroy`). Workload identity pools are soft-deleted for 30 days after
deletion and their IDs can't be reused in that window; recreate with new IDs if needed.

## Drill log
| Date | Env | Duration | Manual steps found → fixed in |
|---|---|---|---|
| | | | |
