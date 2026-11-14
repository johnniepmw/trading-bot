---
paths:
  - "infra/**"
---
- Every change under infra/ is high risk: write the owner brief in plain English
  (what resources are created, changed or destroyed, cost, blast radius).
- Never put secret values in OpenTofu: add the name to `secrets` in `infra/envs/<env>`
  (the module creates the container with an `__unset__` placeholder); the owner adds
  the value with `make secret-set`.
- Ship infra changes in their own PR, merged before the app change that needs them.
  OpenTofu template changes create a revision that takes traffic at the next deploy.
- Ownership split (ADR-0004): OpenTofu owns service shape (identity, scaling, secret
  mounts, ingress); the deploy pipeline owns image, traffic and env vars. Keep those
  fields in `lifecycle.ignore_changes`.
- Least privilege: grant roles on the specific resource (service, secret, bucket), not
  the project, unless impossible. No primitive roles (owner/editor/viewer) for runtime
  identities. No `allUsers` except the public web invoker where intended.
- Stateful resources (buckets with data, databases) get deletion protection or
  `prevent_destroy`.
- You may `make infra-plan` for any env and `make infra-apply ENV=staging`. Production
  applies happen only in CI after merge.
- Run `tofu fmt -recursive infra` and `tofu validate` before committing.
