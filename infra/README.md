# Infrastructure (OpenTofu, GCP)

```
bootstrap/                  once per app, owner-run: APIs, state bucket, Artifact Registry,
                            workload identity federation, CI identities, budget
modules/cloud-run-service/  one environment: runtime identity, Cloud Run service, secrets,
                            flags bucket, CI permissions, uptime check and alert
envs/staging, envs/prod     instances of the module; applied by CI on merge
```

| Task | Command | Who |
|---|---|---|
| First-time setup | `make infra-bootstrap && make gh-vars` | owner |
| Plan | `make infra-plan ENV=staging` | anyone |
| Apply staging | `make infra-apply ENV=staging` | agent or owner |
| Apply prod | merge a PR (infra workflow) | CI |
| Rebuild staging | `make env-down ENV=staging && make env-up ENV=staging` | agent or owner |
| Add a secret | add the name to `secrets` in `envs/<env>/main.tf` (PR) → `make secret-set NAME=… ENV=…` | agent, then owner |

Design notes are in ADR-0004 and ADR-0005. Rules for changing this folder:
`.claude/rules/infra.md`.
