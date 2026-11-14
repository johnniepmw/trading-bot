# Runbook: rebuild an environment from code

## Staging (agent or owner)
```bash
make env-down ENV=staging   # destroys the staging service, its identity, secrets containers, flags bucket
make env-up ENV=staging     # recreates them from infra/envs/staging
make secret-set NAME=<each secret> ENV=staging   # owner: secret values are never in code
gh workflow run deploy.yml  # redeploys main to staging and prod
```
Expected time: about 10 minutes. Anything that needed a manual step is a bug: fix it in
code and note it in the drill log below.

## Production (owner only)
Production has deletion protection. To rebuild: remove `deletion_protection` in a PR
(high risk), merge, `make env-down ENV=prod && make env-up ENV=prod` locally with owner
credentials, re-add secret values, redeploy, then restore deletion protection.
Consider whether rollback, a kill switch or an in-place fix serves better first.

## Bootstrap layer
`infra/bootstrap` (state bucket, registry, CI identities) is created once and protected
(`prevent_destroy`). Workload identity pools are soft-deleted for 30 days after
deletion and their IDs can't be reused in that window; recreate with new IDs if needed.

## Drill log
| Date | Env | Duration | Manual steps found → fixed in |
|---|---|---|---|
| | | | |
