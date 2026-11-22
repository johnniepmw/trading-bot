# Runbook: roll back production

**When**: the job is broken and the last deploy is the likely cause. The deploy pipeline
already puts the previous image back if its own check execution fails; use this when the
problem shows up later (ADR-0019).

**Who**: owner or agent (agents tell the owner immediately afterwards). Naming a specific
older image is the owner's call only.

1. Trigger: `gh workflow run ops.yml -f action=rollback -f env=prod`
   (phone: GitHub app → Actions → ops → Run workflow).
2. Watch: `gh run watch $(gh run list --workflow ops.yml --limit 1 --json databaseId -q '.[0].databaseId')`.
   The job goes back to the image tagged `previous-prod` (the one before the last deploy)
   and runs a `smoke` execution; if that fails, it returns to the image it had.
3. Confirm: `make logs ENV=prod` (Cloud Logging, from your own machine) shows the smoke run.
4. The next successful deploy from `main` replaces it, so fix forward with a PR (the
   `incident` skill) before merging anything else risky.
5. Write the postmortem in the fix PR.

To go back further (owner only): pass `-f image=<region>-docker.pkg.dev/<project>/<app>/<app>@sha256:<digest>`
(digests: `gcloud artifacts docker images list <region>-docker.pkg.dev/<project>/<app>/<app> --include-tags`).
A named image skips the deploy's checks except `smoke`; prefer fixing forward.
