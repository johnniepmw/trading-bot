# Infrastructure (OpenTofu, GCP)

```
bootstrap/                  once per app, owner-run: APIs, state bucket, Artifact Registry,
                            workload identity federation, CI identities, budget
modules/cloud-run-job/      one environment: the Cloud Run job (args `run`, no retries, 30 min
                            timeout), its daily schedule (created paused), ledger bucket
                            (versioned, soft delete, never destroyed), lock bucket, flags
                            bucket (seeded once), pause bucket (any object stops new orders),
                            an alert policy, IAM for the bootstrap's identities
envs/staging, envs/prod     instances of the module; applied by CI on merge
```

| Task | Command | Who |
|---|---|---|
| First-time setup | `make infra-bootstrap && make gh-vars` | owner |
| Plan | `make infra-plan ENV=staging` | anyone |
| Apply staging | `make infra-apply ENV=staging` | agent or owner |
| Apply prod | merge a PR (infra workflow) | CI |
| Rebuild staging | `make env-down ENV=staging && make env-up ENV=staging` (the ledger and lock buckets refuse to be destroyed: empty and delete them by hand first, then re-run the ledger setup) | owner |
| Add a secret | `make secret-set NAME=… ENV=…` creates it (owner), then a PR adds it to `secrets` in `envs/<env>/main.tf` (mount and access) | owner, then agent |

Design notes are in ADR-0004 and ADR-0005. Rules for changing this folder:
`.claude/rules/infra.md`.

Identities come from the bootstrap; environments never create service accounts. OpenTofu
never writes secret versions (CI's plan identity can't read secret values). The schedule is
created paused and applies never resume it: turning it on is an owner step.
