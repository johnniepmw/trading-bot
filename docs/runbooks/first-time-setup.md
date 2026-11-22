# Runbook: first-time setup for a new project (owner)

About 30 minutes, once per project. The agent's `bootstrap` skill does everything else.
No local terminal (e.g. a Chromebook)? Run the commands below in Google Cloud Shell
after its one-time install (paste those lines one at a time); see
[cloud-sessions.md](cloud-sessions.md#owner-only-steps-without-a-local-terminal).

## 1. GitHub
This repository is **public** (ADR-0014), which gives rulesets, auto-merge, secret
scanning and push protection on the free plan. No GitHub Pro needed.
1. Public-repo hardening (ADR-0014), in the repo's settings:
   - Pull requests and issues: **Collaborators only**.
   - Actions → General → fork pull request workflows: **require approval for all
     external contributors**.
   - Actions → General → workflow permissions: **read repository contents**, with
     "Allow GitHub Actions to create and approve pull requests" **off**.
   - Advanced Security: **secret scanning** and **push protection** on.
   - Your account's email settings: keep your commit email **private**.
2. `make github-setup` — repo settings (squash-only, auto-merge, delete merged branches),
   the `main` ruleset with required checks, labels, Dependabot alerts and security fixes.
   Then set up the risk-gate app and re-run it with `RISK_GATE_APP_ID`
   ([risk-gate-app.md](risk-gate-app.md)).
3. Agent-review credential: run `claude setup-token`, then
   `gh secret set CLAUDE_CODE_OAUTH_TOKEN` and paste at the prompt (or add it in the
   repo's Settings → Secrets and variables → Actions). Never paste it in chat.
4. Dependabot PRs don't receive Actions secrets; agent-review skips them by design and
   relies on CI.

## 2. Google Cloud
1. Create a project (for example `<app>-<random>`), link billing, and set a budget alert
   in the console if you don't pass `billing_account` to bootstrap.
2. `gcloud auth login && gcloud auth application-default login`
3. `cp infra/bootstrap/bootstrap.tfvars.example infra/bootstrap/bootstrap.tfvars`, fill in
   project ID, repo (`owner/name`), its numeric IDs (`github_repository_id`: `gh api
   repos/<owner>/<repo> --jq .id`; `github_owner_id`: `--jq .owner.id`), app name (18
   characters or fewer), alert email.
4. `make infra-bootstrap` — turns on the APIs it needs with `gcloud` first (a new project
   has them off; if the first run still fails with "API has not been used in project …
   before", wait two minutes and re-run), then creates the state bucket, Artifact Registry,
   Workload Identity Federation and the three CI identities, and migrates its own state
   into the bucket.
   Before every bootstrap run, check that no service account holds Owner, Editor or a
   Secret Manager role on the project (CI may act as the bot's runtime identities, so any
   other identity with those roles would be a way around the owner-only `prod-operator`
   secret, ADR-0009):
   `gcloud projects get-iam-policy PROJECT_ID --flatten=bindings --filter='bindings.members:serviceAccount AND (bindings.role:roles/owner OR bindings.role:roles/editor OR bindings.role:secretmanager)' --format='value(bindings.role, bindings.members)'`
   Expected, and fine to leave: `roles/editor` for `NUMBER@cloudservices.gserviceaccount.com`
   (Google's own API agent; removing it breaks services, and CI can't act as it), and, after
   the first Phase 2 run, `roles/secretmanager.admin` for `ci-infra-apply@…` (it has a
   condition excluding `prod-operator`). Remove anything else, especially a default account
   (`…-compute@developer.gserviceaccount.com` or `…@appspot.gserviceaccount.com`).
5. `make gh-vars` — writes the outputs into GitHub Actions variables. **This repo: don't
   run it until the Phase 2 job module lands.** Once the variables exist, the deploy
   workflow tries to deploy the bot as a service and fails on every merge to main
   (ADR-0011).
6. `make env-up ENV=staging && make env-up ENV=prod` (once; afterwards the infra workflow
   applies changes on merge). Then any merge to `main` deploys the app.
7. For each secret the app needs: `make secret-set NAME=<name> ENV=staging` and `ENV=prod`.
8. Working in Cloud Shell? Run `gh auth logout` when you're done.

## 3. Claude Code
1. Use Claude Code on the web after the one-time environment setup in
   [cloud-sessions.md](cloud-sessions.md), which also has a 30-second check that the
   kit loaded; or open the repo in the desktop app and accept the folder trust prompt.
   The kit is plain files in `.claude/`, so its skills, subagents and hooks load in both.
2. Claude Code Projects need nothing extra: threads pick up the repo's `.claude/`.
   Don't also install the `agentic` plugin; its skills would appear twice.
3. Optional: `/schedule weekly /garden` for automated upkeep.
4. Say what you want built.
