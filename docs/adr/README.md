# Architecture decision records

Format: MADR-style, see [0000-template.md](0000-template.md). Use the kit's `adr` skill.
Accepted ADRs are never rewritten; a new ADR supersedes them.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | accepted |
| [0002](0002-agent-operating-model.md) | Agents deliver end to end; the owner decides only high-risk changes | accepted |
| [0003](0003-delivery-pipeline.md) | Trunk-based delivery with auto-merge, staged deploys and automatic rollback | accepted |
| [0004](0004-gcp-cloud-run.md) | Run on Google Cloud Run, one project per app, infrastructure in OpenTofu | accepted |
| [0005](0005-secrets-and-flags.md) | Secrets in Secret Manager as files; flags with remote kill switches | accepted |
