# 0005. Secrets in Secret Manager as files; flags with remote kill switches

- **Status**: accepted
- **Date**: 2026-11-14
- **Deciders**: owner
- **Enforcement**: guard hooks (no secret reads, no secret files); `make flags-check`; gitleaks in hooks and CI

## Context
Agents write all code but must never handle secret values. Risky features need an off
switch that works in seconds without a deploy, even after a rollback.

## Options considered
Secrets: (1) Secret Manager mounted as files; (2) Secret Manager as env vars;
(3) SOPS-encrypted files in the repo. Flags: (a) env vars on the service (each change is
a new revision, which interacts badly with pinned rollbacks); (b) a flag SaaS (cost,
another dependency); (c) a JSON override object per environment in Cloud Storage read
with a short cache, defaults in a repo registry.

## Decision
Secrets: option 1. OpenTofu creates empty secrets and grants each environment's runtime
identity access to its own; the owner adds values with `make secret-set`; Cloud Run
mounts them at `/secrets/<name>/value`. Flags: option (c), with `flags.json` as the
registry and environment variables as a local and test override.

## Consequences
- Good: secret values never touch the repo, state, logs or the agent; kill switches work
  independently of deploys and rollbacks.
- Bad / costs: a new secret needs an owner step before first deploy; the app makes a cheap
  storage read every 30 seconds per instance.
- Revisit when: many flags with targeting rules are needed (consider OpenFeature with a
  managed provider).
