---
paths:
  - "infra/**"
---
- Every change under infra/ is high risk: write the owner brief in plain English
  (what resources are created, changed or destroyed, cost, blast radius).
- Never put secret values in OpenTofu: add the name to `secrets` in `infra/envs/<env>`
  (the job module creates the container with no version); the owner adds the value with
  `make secret-set` before the job needs it. Cloud Run refuses to mount a secret with no
  version, so the owner adds the value before the PR that mounts it is applied (or the
  apply fails and is re-run afterwards, as in Phase 2 step 3). The live switch `prod-operator` is the
  bootstrap's, never the module's.
- Ship infra changes in their own PR, merged before the app change that needs them.
  OpenTofu template changes create a revision that takes traffic at the next deploy.
- Ownership split for the job: OpenTofu owns its shape (identity, timeout, retries, secret
  mounts, the wiring env vars); the deploy pipeline changes only the image, which stays in
  `lifecycle.ignore_changes`. `APP_VERSION` is baked into the image (Phase 2 step 6).
- Least privilege: grant roles on the specific resource (service, secret, bucket), not
  the project, unless impossible. No primitive roles (owner/editor/viewer) for runtime
  identities. No `allUsers` except the public web invoker where intended.
- Stateful resources (buckets with data, databases) get deletion protection or
  `prevent_destroy`.
- You may `make infra-plan` for any env and `make infra-apply ENV=staging`. Production
  applies happen only in CI after merge.
- Run `tofu fmt -recursive infra` and `tofu validate` before committing.
