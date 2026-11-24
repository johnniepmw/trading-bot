# Runbook: the dashboard's sign-in (owner, once per environment)

**What**: the dashboard (ADR-0020) is a Cloud Run service behind IAP. Only your Google account
gets in. CI creates the service; you put it behind IAP with your own script, so no CI identity
ever holds IAP rights, the OAuth client's secret or your address.

## Once per project: the OAuth client
1. Cloud console, project `trading-bot-515509` → **Google Auth Platform → Branding**: create
   the consent screen. Audience **External**, publishing status **Testing**, and your own
   address as the only **test user**.
2. **Clients → Create client → Web application**, named "trading-bot IAP". Create it, then
   edit it and add the authorised redirect URI
   `https://iap.googleapis.com/v1/oauth/clientIds/<CLIENT_ID>:handleRedirect` (your client ID
   in place of `<CLIENT_ID>`).
3. Keep the client ID and secret for the next step. Never paste them into chat, an issue or a
   file in the repo.

## Once per environment (staging first): put the service behind IAP
The service must exist (its infra PR has applied). In Cloud Shell, with the project set:
```
cd ~/trading-bot && git pull
read -rs IAP_CLIENT_ID && export IAP_CLIENT_ID
read -rs IAP_CLIENT_SECRET && export IAP_CLIENT_SECRET
bash scripts/dashboard-iap.sh staging
```
It switches IAP on, gives IAP your OAuth client, makes IAP's service agent the only invoker,
removes any public access and lets only your account (gcloud's current account) sign in. It
prints the URL. Afterwards: `unset IAP_CLIENT_ID IAP_CLIENT_SECRET`.

## Check it (each environment)
1. Open the URL on your phone and sign in: the glance shows.
2. Open it in a private window without signing in, or with another Google account: refused.
3. Ask the agent to check that a direct call with a Google ID token is refused too.
4. Staging only: press **Pause new orders…**, confirm, and check you get the `pause_requested`
   alert email; then clear it with `ops clear-pause` (env `staging`).

## Removing access
Remove your accessor binding (or switch IAP off) in the console: the page goes dark at once. The
bot is unaffected; it never depends on the dashboard.
