# Runbook: working from Claude Code on the web (cloud sessions)

Cloud sessions run on Anthropic VMs, so a Chromebook (or a phone) is enough to direct
all agent work. Owner-only steps need a terminal with your own credentials; see the end.

## One-time: configure the cloud environment (claude.ai/code → environment settings)
1. **Network access**: **Limited**, the default (the docs call it Trusted). It already
   allows the package registries, GitHub and `nodejs.org`, where mise gets Node 24. The
   typescript-web pack's Playwright browsers come from a host outside that list: if
   `make e2e-setup` fails on a download, add the host its error names to the
   environment's allowed domains.
2. **Setup script** (runs before Claude starts; the result is cached for about a week):
   ```bash
   #!/bin/bash
   npm install -g mise || true
   ```
   Sessions then run `make setup`, which runs `mise install` for the repo's pinned tools;
   the Makefile puts mise's shims on `PATH`. If the environment's npm is version 12 or
   later, use the `--allow-scripts` form shown under Cloud Shell below.
3. Install the Claude GitHub App on the repo (needed for cloud sessions to push, and for
   Auto-fix of pull requests).

## First session in a new repo: check the harness loaded
The kit lives in `.claude/` as plain project files, which cloud sessions load. (They
don't load plugins from a repository, so it is deliberately not a plugin here.)
1. The session opens with a "Repository orientation" message: the session-start hook ran.
2. Ask: "List the skills and subagents you have loaded." Expect `/deliver`, `/review`,
   `/escalate` and the rest, and the subagents `reviewer`, `security-reviewer`,
   `architect`, `qa` and `Explore`.
3. Ask: "Run `git push origin main`." Expect it to be refused with
   `BLOCKED by agentic guard`.
If any of these fail, AGENTS.md and `docs/workflow.md` still drive the same loop and CI
still gates every merge; tell Claude what you saw.

## Model and effort: the session picker wins
**Checked 21 Nov 2026** (sessions started from the Android app). Cloud sessions ignore
`"model"` and `"effortLevel"` in `.claude/settings.json`: the session's own picker sets
both. With the picker on Sonnet the session ran Sonnet; effort was medium in both runs,
although the repo says high. `CLAUDE_CODE_EFFORT_LEVEL` was unset.
- **When you start a cloud session, pick Opus and High** (xhigh for high-risk work). The
  repo values still apply on the desktop app and the CLI.
- The kit's subagent and skill pins (reviewers on Opus, qa and `/garden` on Sonnet,
  `Explore` on Haiku) apply whatever the picker says.
- `/status` isn't available in cloud sessions. To check, ask Claude for its exact model
  ID and to run
  `echo "effort=$CLAUDE_EFFORT override=${CLAUDE_CODE_EFFORT_LEVEL:-unset}"`. If
  `override` isn't `unset`, remove that variable from the environment settings: it
  overrides the subagent pins.

## Differences from the desktop app
- **GitHub CLI**: the cloud GitHub proxy blocks GraphQL, so `gh pr` and `gh issue` fail.
  The kit's PR helper `.claude/scripts/gh-pr.sh` (create, checks, merge, view, issue)
  uses REST instead; `gh run`, `gh workflow` and `gh api` work as normal.
- **No auto-merge**: a cloud session can't turn on GitHub's auto-merge. Claude merges
  through the REST API once all required checks are green. For a high-risk PR, after you
  comment `/approve-risk <sha>`, tap **Squash and merge** on GitHub (or ask Claude to
  merge).
- **No cloud credentials in the session**: Claude can't read GCP logs or run gcloud; it
  works through CI, the deploy logs and the `ops` workflow. That's by design.
- **Parallel work**: start one session per goal; each gets its own VM and branch. For a
  stream of related tasks, use a Claude Code Project on the repo. Threads pick up the
  repo's skills and, in a single-repo project, its hooks; no plugin is needed.
- **Kit updates**: `make kit-sync` clones `johnniepmw/agentic-dev-kit`, which the session
  can reach only once that repo is attached to it. Claude can attach it.
- **Unattended**: `/goal <done condition>` works in cloud sessions; sessions keep running
  when you close the tab.

## Owner-only steps without a local terminal
**Google Cloud Shell** (shell.cloud.google.com, in the browser) is already signed in to
Google Cloud and has `gcloud`, `git` and `docker`. Install the rest once. Its npm (12)
blocks install scripts unless allowed, so paste these lines **one at a time**:
```bash
npm install -g --allow-scripts=@anthropic-ai/claude-code,mise @anthropic-ai/claude-code mise
mise use -g gh
echo 'eval "$(mise activate bash)"' >> ~/.bashrc
exec bash
gh auth login
```
Then clone the repo, `cd` into it, and run `make setup` (installs the repo's pinned
tools, OpenTofu included) before the owner commands in
[first-time-setup.md](first-time-setup.md). When setup is finished, run
`gh auth logout` so your GitHub token doesn't stay in Cloud Shell.

- **ChromeOS Linux** (Settings → About ChromeOS → Developers → Linux development
  environment) works too, if your Chromebook allows it.
- **Secret values**: Google Cloud console → Secret Manager → the secret → New version
  (paste there), then redeploy. GitHub Actions secrets: repo Settings → Secrets and
  variables → Actions. Never paste a secret into a Claude chat.
- **Agent-review token**: run `claude setup-token` in Cloud Shell (installed above),
  then paste the token into the GitHub secret `CLAUDE_CODE_OAUTH_TOKEN` in the web UI.
