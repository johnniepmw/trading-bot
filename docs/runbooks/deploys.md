# Runbook: deploys (ADR-0019)

**Normal**: every merge to `main` that changes code builds one image and deploys it to
staging. Staging's job gets the new image and a `smoke` execution; if that fails, hangs or
is cancelled, the job goes back to the previous image. Nothing to do.

**Prod** deploys only once the owner sets the repository variable `DEPLOY_PROD=true`
(GitHub → Settings → Secrets and variables → Actions → Variables), after Phase 2 step 4.
Then each deploy:
- waits for the window: it refuses while a prod execution is running, or when the
  schedule is on and the next run is under 30 minutes away. A refusal fails the run with
  "refused"; re-run it later (Actions → deploy → the run → Re-run jobs);
- updates the image and runs a `run --dry-run` against prod (no lock, no orders);
- on failure goes back to the previous image and opens an `incident` issue.

**Check a deploy**: Actions → deploy → the latest run: `build`, `staging` (and
`production` when on) green. Execution logs are in Cloud Logging only (`make logs`).

**Roll back**: rollback.md (`ops` → `rollback`).

**Drill** (after changes to the deploy scripts, or quarterly): Actions → deploy → Run
workflow → `drill: true`. It deploys a deliberately broken copy to staging and passes only
if staging ends up back on the good image. Never touches prod.

**Before `DEPLOY_PROD=true`** (Phase 2 plan, carried items): decide whether a prod dry run
that stops for a business reason (drawdown stop, stale data) should fail a deploy, and
split the window check into its own step so a refusal doesn't open an incident issue.
