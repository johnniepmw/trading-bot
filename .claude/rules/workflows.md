---
paths:
  - ".github/**"
---
- Changes here are high risk (they are the gates). Explain them to the owner in the brief.
- Workflow-level `permissions: contents: read`; grant more per job, only what it uses.
- Never interpolate `${{ github.event.* }}`, PR titles, bodies, branch names or comment
  text directly into `run:`. Pass them through `env:` and quote them.
- `pull_request_target` / `issue_comment` workflows must not check out or run PR code.
- Keep third-party actions to a minimum; prefer `gh`, `gcloud` and `make` in `run:` steps.
  New actions are pinned by commit SHA.
- Job names that are required checks (`ci-required`, `agent-review`) and the
  `risk-gate` status context must not be renamed without updating the ruleset
  (`scripts/github-setup.sh`).
- Run `actionlint` (part of `make repo-lint`) before committing.
