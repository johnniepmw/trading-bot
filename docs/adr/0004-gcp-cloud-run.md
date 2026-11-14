# 0004. Run on Google Cloud Run, one project per app, infrastructure in OpenTofu

- **Status**: accepted
- **Date**: 2026-11-14
- **Deciders**: owner
- **Enforcement**: `infra/` is high risk in `.github/risk-paths.txt`; `infra.yml` plans on PRs, applies on merge, checks drift nightly

## Context
The owner chose GCP. Apps are containers. The environment must be recreatable from code
in one command, deploys must be keyless, and a solo owner needs low operational load
and cost.

## Options considered
1. **Cloud Run services (and Cloud Run jobs for batch)** — managed, scales to zero,
   revision-based traffic control (instant rollback, tagged previews); container-based.
2. **GKE** — far more control and far more to operate.
3. **Compute Engine VM** — simple mental model; patching, deploys and rollbacks are ours.

Project layout: (a) one project per environment — strongest isolation, more IAM and
bootstrap work; (b) one project per app with separate services, runtime identities and
secrets per environment — less isolation, much simpler.

## Decision
Cloud Run, option (b): one GCP project per app, region `europe-west2`, with
`<app>-staging` and `<app>-prod` services. OpenTofu with state in GCS. GitHub Actions
authenticates through Workload Identity Federation: a preview identity (any branch,
staging service only), a deployer and an infra-apply identity (main branch only), and a
read-only infra-plan identity. Ownership split: OpenTofu owns the shape of each service
(identity, scaling, secret mounts, ingress); the deploy pipeline owns image, traffic and
environment variables (OpenTofu ignores those fields).

## Consequences
- Good: no servers to patch, one-command environments, keyless CI, rollbacks in seconds.
- Bad / costs: staging and prod share a project boundary (mitigated by per-env service
  accounts and secret IAM); bootstrap identities are soft-deleted for 30 days if destroyed.
- Revisit when: compliance or a team needs project-level isolation, or workloads need
  long-running stateful processes (consider GKE or a VM with an ADR).
