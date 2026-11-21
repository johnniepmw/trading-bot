# Runbook: first-time setup for a new project (owner)

About 30 minutes, once per project. The agent's `bootstrap` skill does everything else.
No local terminal (e.g. a Chromebook)? Run the commands below in Google Cloud Shell; see
[cloud-sessions.md](cloud-sessions.md).

## 1. GitHub
This repository is **public** (ADR-0014), which gives rulesets, auto-merge, secret
scanning and push protection on the free plan. No GitHub Pro needed.
1. `make github-setup`: repo settings (squash-only, auto-merge, delete merged branches),
   the `main` ruleset with required checks, labels, Dependabot alerts and security fixes.
2. Public-repo settings (repo **Settings** in the web UI, ADR-0014):
   - **Moderation options → Interaction limits**: "Limit to repository collaborators",
     so only collaborators can open issues and pull requests or comment. The limit
     lasts at most six months; renew it when GitHub says it is expiring.
   - **Actions → General → Fork pull request workflows from outside collaborators**:
     "Require approval for all external contributors".
   - **Code security**: Secret scanning and Push protection on.
   - **Your account → Settings → Emails**: "Keep my email addresses private" and "Block
     command line pushes that expose my email" on.
3. Agent-review credential: run `claude setup-token`, then
   `gh secret set CLAUDE_CODE_OAUTH_TOKEN` and paste at the prompt. Never paste it in chat.
4. Dependabot PRs don't receive Actions secrets; agent-review skips them by design and
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
5. `make gh-vars` — writes the outputs into GitHub Actions variables. **This repo: don't
   run it until the Phase 2 job module lands.** Once the variables exist, CI's preview
   job and the deploy workflow try to deploy the bot as a service and fail (ADR-0011).
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
