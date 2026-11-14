# Runbook: working from Claude Code on the web (cloud sessions)

Cloud sessions run on Anthropic VMs, so a Chromebook (or a phone) is enough to direct
all agent work. Owner-only steps need a terminal with your own credentials; see the end.

## One-time: configure the cloud environment (claude.ai/code → environment settings)
1. **Network access**: Custom, tick "Also include default list of common package
   managers", and add `nodejs.org` (mise installs Node 24 from there). For the
   typescript-web pack also add Playwright's browser download host; if `make e2e-setup`
   fails, its error names the host to add.
2. **Setup script** (runs before Claude starts, cached between sessions):
   ```bash
   #!/bin/bash
   npm install -g mise || true
   ```
   Sessions then run `make setup`, which runs `mise install` for the repo's pinned
   tools; the Makefile puts mise's shims on `PATH`.
3. Install the Claude GitHub App on the repo (needed for cloud sessions to push, and for
   Auto-fix of pull requests).

## First session in a new repo: check the harness loaded
Ask: "List the skills, subagents and hooks you have loaded." Expect `agentic:deliver`
and friends, the four reviewer agents, and the guard and stop-gate hooks. If the kit
didn't load, AGENTS.md and `docs/workflow.md` still drive the same loop and CI still
gates every merge; ask Claude to add the kit's hooks to `.claude/settings.json` (a
high-risk PR) so they run regardless.

## Differences from the desktop app
- **GitHub CLI**: the cloud GitHub proxy blocks GraphQL, so `gh pr` and `gh issue` fail.
  The kit's `scripts/gh-pr.sh` (create, checks, merge, view, issue) uses REST instead;
  `gh run`, `gh workflow` and `gh api` work as normal.
- **No auto-merge from the session**: Claude merges through the REST API once all
  required checks are green. For high-risk PRs, after you comment `/approve-risk <sha>`,
  tap **Squash and merge** on GitHub (or ask Claude to merge).
- **No cloud credentials in the session**: Claude can't read GCP logs or run gcloud; it
  works through CI, the deploy logs and the `ops` workflow. That's by design.
- **Parallel work**: start one session per goal; each gets its own VM and branch. For a
  stream of related tasks, use a Claude Code Project (enable the `agentic` plugin in
  Project settings).
- **Unattended**: `/goal <done condition>` works in cloud sessions; sessions keep running
  when you close the tab.

## Owner-only steps without a local terminal
- **Google Cloud Shell** (shell.cloud.google.com, in the browser): already signed in to
  Google Cloud with `gcloud`, `git` and `docker`. Install the rest once:
  `npm install -g mise && mise use -g opentofu gh` (or follow each tool's install page),
  then `gh auth login`, clone the repo and run `make infra-bootstrap`, `make gh-vars`,
  `make github-setup`.
- **ChromeOS Linux** (Settings → About ChromeOS → Developers → Linux development
  environment) works too, if your Chromebook allows it.
- **Secret values**: Google Cloud console → Secret Manager → the secret → New version
  (paste there), then redeploy. GitHub Actions secrets: repo Settings → Secrets and
  variables → Actions. Never paste a secret into a Claude chat.
- **Agent-review token**: `claude setup-token` needs the Claude Code CLI; run
  `npm install -g @anthropic-ai/claude-code && claude setup-token` in Cloud Shell, then
  paste the token into the GitHub secret `CLAUDE_CODE_OAUTH_TOKEN` in the web UI.
