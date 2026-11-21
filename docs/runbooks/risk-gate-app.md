# Runbook: the risk-gate GitHub App (owner)

The required `risk-gate` status is posted by a dedicated GitHub App, and the `main`
ruleset accepts that status only from that app. Without this, any workflow on any branch
with `statuses: write` could set `risk-gate` to success on a PR and skip your approval.
The app's private key is an environment secret that only the default branch can read.

Do the steps in order. Until step 3 is done, the new risk-gate workflow fails and PRs
wait (fail closed). Between step 3 and step 5 nothing breaks; step 5 is what closes the
gap.

## 1. Create the app
GitHub → your avatar → Settings → Developer settings → GitHub Apps → **New GitHub App**.
- **Name**: something unique, e.g. `<your-login>-risk-gate`.
- **Homepage URL**: this repository's URL.
- **Webhook**: untick **Active**.
- **Repository permissions**: **Commit statuses: Read and write**. Nothing else
  (Metadata: read-only is added automatically).
- **Where can this app be installed**: **Only on this account**.
- **Create GitHub App**. On the app's page, note the **App ID** (a number) and the
  **Client ID** (starts `Iv`). Neither is secret.
- **Generate a private key**. A `.pem` file downloads. This one is secret.

## 2. Install it on this repository only
App page → **Install App** → your account → **Only select repositories** →
`trading-bot` → **Install**.

## 3. Create the `risk-gate` environment
Repository → Settings → Environments → **New environment** → name `risk-gate`.
- **Deployment branches and tags**: **Selected branches and tags** → add `main`. Nothing
  else: this is what stops other branches reading the key.
- No required reviewers and no wait timer (risk-gate must run unattended).
- **Environment secrets** → add `RISK_GATE_APP_KEY`, paste the whole `.pem` file content.
- **Environment variables** → add `RISK_GATE_APP_CLIENT_ID` = the Client ID.
- Delete the downloaded `.pem` (or keep it in your password manager). Never paste it in
  chat. If it leaks: on the app page revoke the key, generate a new one, repeat this step.

## 4. Check it works
On the first PR after the change that uses the app is merged, the `risk-gate` status
shows the app's name and avatar instead of GitHub Actions. The agent checks this with a
read-only API call (the status's `creator.login` is `<app-name>[bot]`).

## 5. Pin the ruleset to the app
From a machine with your own `gh` login (admin), run the repo-settings target with the
numeric App ID:

```bash
RISK_GATE_APP_ID=<the numeric App ID> make github-setup
```

This re-applies the `main` ruleset with `risk-gate` required from that app. Pass the same
variable whenever you re-run that target; without it the script warns and falls back to
matching by name.

## Rollback
Re-run the repo-settings target without `RISK_GATE_APP_ID` (name-only matching), then
revert the workflow change. Don't delete the app while the ruleset is pinned to it: every
PR would wait on a status nothing can post.
