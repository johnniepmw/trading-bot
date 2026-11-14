# Runbook: roll back production

**When**: production is broken and the last deploy is the likely cause. The deploy
pipeline already rolls back automatically if its own smoke checks fail; use this when
the problem shows up later.

**Who**: owner or agent (agents tell the owner immediately afterwards).

1. Trigger: `gh workflow run ops.yml -f action=rollback -f env=prod`
   (phone: GitHub app → Actions → ops → Run workflow).
2. Watch: `gh run watch $(gh run list --workflow ops.yml --limit 1 --json databaseId -q '.[0].databaseId')`.
   The job moves 100% of traffic to the previous ready revision and runs the smoke test.
3. Confirm: `make logs ENV=prod` and the smoke output show the old version serving.
4. Production now serves the previous revision. The next successful deploy from `main`
   replaces it, so fix forward with a PR (the `incident` skill) before merging anything
   else risky.
5. Write the postmortem in the fix PR.

To roll back further than one revision, pass `-f revision=<name>` (find names with
`gcloud run revisions list --service <app>-prod --region europe-west2`).
