# Operations

## Environments
| Env | What | Created by | Who changes it |
|---|---|---|---|
| local | `make dev`, local e2e | `make setup` | anyone |
| staging | `<app>-staging` Cloud Run service, own runtime identity and secrets | `infra/envs/staging` | CI on merge; agent may apply infra to staging |
| prod | `<app>-prod` Cloud Run service, own runtime identity and secrets | `infra/envs/prod` | CI on merge only; owner for manual actions |

Both services live in one GCP project per app (see ADR-0004). Region: `europe-west2`.

## Deploy pipeline (`.github/workflows/deploy.yml`)
On every push to `main`:
1. Build the image once, push to Artifact Registry, record its digest.
2. Staging: deploy the digest, smoke test, run e2e against the staging URL.
3. Production: deploy the same digest as a no-traffic revision tagged `c-<sha>`, smoke
   test that tag's URL, then move 100% of traffic to it.
4. Watch for five minutes (repeated smoke checks). Any failure moves traffic back to the
   previous revision, opens an issue labelled `incident`, and fails the run.

Deploys are serialised (one at a time). Re-running the workflow redeploys the same commit.

## Rollback and kill switches
- **Rollback** (seconds, no rebuild): `ops` workflow → `action=rollback, env=prod`.
  Routes 100% of traffic to the previous ready revision. Agents may trigger it during an
  incident; they tell the owner straight away. From a phone: GitHub app → Actions → ops →
  Run workflow.
- **Kill switch** (seconds, no deploy): `ops` workflow → `action=kill, flag=<name>`.
  Writes a remote override that the app re-reads every 30 seconds. Agents may kill;
  turning a feature back on (`restore`/`set`) in production is the owner's call.
- **Roll forward** is the default for everything else: fix, PR, normal pipeline.
- Runbooks: `docs/runbooks/rollback.md`, `kill-switch.md`.

## Rebuild from scratch
Infrastructure is code. `docs/runbooks/rebuild-environment.md` recreates staging with
`make env-down ENV=staging && make env-up ENV=staging` and documents the production
path. A rebuild drill runs quarterly; anything manual discovered in the drill becomes code.

## Observability
- Structured JSON logs to stdout with `severity`, `message`, `version`, `request_id`;
  never log secrets or personal data.
- `GET /healthz` returns `{"status":"ok","version":"<git sha>"}`; smoke tests and uptime
  checks use it. Deep dependency checks go in `/readyz` if needed. This repo's bot is a
  Cloud Run job with no HTTP endpoint: its check is `python -m tradingbot.app smoke`
  until the Phase 2 job deploy replaces this section (ADR-0011).
- Uptime check and alert policy per environment (OpenTofu), emailing the owner.
- `make logs ENV=prod` reads recent logs.

## Incidents
Stabilise first (rollback or kill), then tell the owner, diagnose, fix forward with a
regression test, and write a postmortem in `docs/postmortems/`. The `incident` skill runs
this.
