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
- **Identifying and authorizing users**: leave **Request user authorization (OAuth)**
  and **Enable Device Flow** off, and don't generate a client secret (a user token of this
  app would count as the app).
- **Where can this app be installed**: **Only on this account**.
- **Create GitHub App**. On the app's page, note the **App ID** (a number) and the
  **Client ID** (starts `Iv`). Neither is secret.
- **Generate a private key**. A `.pem` file downloads. This one is secret.

## 2. Install it on this repository only
App page → **Install App** → your account → **Only select repositories** →
`trading-bot` → **Install**.

## 3. Create the `risk-gate` environment
Repository → Settings → Environments → **New environment** → name `risk-gate` (if it
already exists, open it: GitHub creates an environment, unrestricted, the first time a
workflow names it). Set the branch rule **first**, and add the key only after it's saved:
- **Deployment branches and tags**: **Selected branches and tags** → add rule, ref type
  **Branch**, name `main`. Nothing else, ever (no tags, no `refs/pull/*`, no other
  branches): this is what stops other branches reading the key. The repo-settings target
  in step 5 checks this and stops if anything else is there.
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

This checks the environment's branch policy (exactly `branch:main`, or stops), then
re-applies the `main` ruleset with `risk-gate` required from that app. Later re-runs keep
the pin without the variable; only `RISK_GATE_UNPIN=1` removes it.

Open PRs whose `risk-gate` status was posted before the pin need a fresh run: push to
them, or comment the approval again.

## Rotating the key
App page → Private keys → generate a new key → replace `RISK_GATE_APP_KEY` in the
environment → delete the old key on the app page. Do it at once if the key may have
leaked.

## Rollback
If the app can't post (key revoked, environment deleted), every PR waits, including the
revert, because risk-gate runs main's workflow. Then: re-run the repo-settings target
with `RISK_GATE_UNPIN=1`, temporarily remove `risk-gate` from the ruleset's required
checks (Settings → Rules → main-protection), merge the fix or revert, then re-run the
target with `RISK_GATE_APP_ID=<App ID>` to restore the pinned check (a plain re-run after
an unpin leaves it matched by name only). Don't delete the app while the ruleset is pinned to it.
