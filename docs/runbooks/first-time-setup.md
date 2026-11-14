# Runbook: first-time setup for a new project (owner)

About 30 minutes, once per project. The agent's `bootstrap` skill does everything else.
No local terminal (e.g. a Chromebook)? Run the commands below in Google Cloud Shell; see
[cloud-sessions.md](cloud-sessions.md).

## 1. GitHub
1. Create the repo from the template ("Use this template" on `johnniepmw/agentic-dev-template`),
   private.
2. Needs GitHub Pro on your account (rulesets and auto-merge on private repos).
3. `make github-setup` — repo settings (squash-only, auto-merge, delete merged branches),
   the `main` ruleset with required checks, labels, Dependabot alerts and security fixes.
4. Agent-review credential: run `claude setup-token`, then
   `gh secret set CLAUDE_CODE_OAUTH_TOKEN` and paste at the prompt. Never paste it in chat.
5. Dependabot PRs don't receive Actions secrets; agent-review skips them by design and
   relies on CI.

## 2. Google Cloud
1. Create a project (for example `<app>-<random>`), link billing, and set a budget alert
   in the console if you don't pass `billing_account` to bootstrap.
2. `gcloud auth login && gcloud auth application-default login`
3. `cp infra/bootstrap/bootstrap.tfvars.example infra/bootstrap/bootstrap.tfvars`, fill in
   project ID, repo (`owner/name`), app name, alert email.
4. `make infra-bootstrap` — enables APIs, creates the state bucket, Artifact Registry,
   Workload Identity Federation and the four CI identities, and migrates its own state
   into the bucket.
5. `make gh-vars` — writes the outputs into GitHub Actions variables.
6. `make env-up ENV=staging && make env-up ENV=prod` (once; afterwards the infra workflow
   applies changes on merge). Then any merge to `main` deploys the app.
7. For each secret the app needs: `make secret-set NAME=<name> ENV=staging` and `ENV=prod`.

## 3. Claude Code
1. Open the repo in the Claude Code desktop app and accept the folder trust prompt (this
   loads the vendored kit and its hooks), or use Claude Code on the web after the
   one-time environment setup in [cloud-sessions.md](cloud-sessions.md).
2. For Claude Code Projects (cloud threads), add the `agentic` plugin in Project settings.
3. Optional: `/schedule weekly /agentic:garden` for automated upkeep.
4. Say what you want built.
