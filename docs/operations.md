# Operations

## Environments
| Env | What | Created by | Who changes it |
|---|---|---|---|
| local | `make dev`, local e2e | `make setup` | anyone |
| staging | `<app>-staging` Cloud Run job on fakes, its paused schedule, ledger, lock and flags buckets | `infra/envs/staging` | CI on merge; agent may apply infra to staging |
| prod | `<app>-prod` Cloud Run job on the Trading 212 demo, the same set | `infra/envs/prod` | CI on merge only; owner for manual actions |

Both jobs live in one GCP project per app (see ADR-0004). Region: `europe-west2`.

## Deploy pipeline (`.github/workflows/deploy.yml`)
On every push to `main` (ADR-0019):
1. Build the image once, push to Artifact Registry, record its digest.
2. Staging: set the job's image to the digest, run a `smoke` execution; on failure put
   the previous image back.
3. Production, once the owner sets `DEPLOY_PROD=true` (after Phase 2 steps 3–4): refuse
   while a prod execution runs or the next scheduled run is under 30 minutes away; set
   the image; run a `run --dry-run` execution (prod's ledger copy and broker, no lock, no
   sending); on failure put the previous image back and open an `incident` issue.
The pipeline reads execution status only, never logs (ADR-0014).

Deploys are serialised (one at a time). Re-running the workflow redeploys the same commit.

## Rollback and kill switches
- **Rollback** (seconds, no rebuild): `ops` workflow → `action=rollback, env=prod`.
  Puts the job back on the image it ran before the last deploy and runs a `smoke`
  execution.
- **Pause** the schedule: `ops` workflow → `action=pause`. No new runs; a running one
  finishes. Only the owner resumes it. Agents may trigger it during an
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
- The bot is a Cloud Run job with no HTTP endpoint. Its health check is the `smoke`
  command, run as an execution by every deploy and rollback.
- Alerts on log lines (`cycle stopped`, stale lock, …) arrive with Phase 2 step 7.
- `make logs ENV=prod` reads recent job logs from Cloud Logging (never from Actions).

## Incidents
Stabilise first (rollback or kill), then tell the owner, diagnose, fix forward with a
regression test, and write a postmortem in `docs/postmortems/`. The `incident` skill runs
this.
