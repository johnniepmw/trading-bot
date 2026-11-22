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
| [0006](0006-python-toolchain.md) | Python with uv, ruff, mypy (strict), pytest and Hypothesis | accepted |
| [0007](0007-own-core-on-libraries.md) | Our own small core on open-source libraries, not a trading engine | accepted |
| [0008](0008-broker-interface.md) | A broker interface, with Trading 212 first and IBKR as the fallback | accepted |
| [0009](0009-risk-veto-and-live-switch.md) | The risk layer has a veto; ceilings live in code; the live switch is owner-only | accepted |
| [0010](0010-decimal-money-utc-time.md) | Decimal money with an explicit currency, and UTC time | accepted |
| [0011](0011-cloud-run-job.md) | Run as a scheduled Cloud Run job, with a deploy window | accepted |
| [0012](0012-static-egress-ip.md) | Static egress IP via Direct VPC egress and Cloud NAT | proposed (deferred to Phase 4) |
| [0013](0013-ledger-sqlite-in-gcs.md) | Ledger storage: SQLite in a versioned GCS bucket with a generation-match lock | accepted |
| [0014](0014-public-repository.md) | Public repository | accepted |
| [0015](0015-long-or-cash-trend-first-strategy.md) | A long-or-cash ETF trend system as the first strategy | accepted |
| [0016](0016-order-path-types-and-market-data.md) | Order-path types, the market-data boundary and numeric types | accepted |
| [0017](0017-ledger-lock-checkpoints-reference-mark.md) | The ledger in GCS: a lock object, per-intent checkpoints and a reference mark | accepted |
| [0019](0019-deploying-the-job.md) | Deploying the job: image only, a check execution, prod behind a window and a dry run | accepted |
