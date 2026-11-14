# Docs index

| Doc | Read it when |
|---|---|
| [workflow.md](workflow.md) | Starting any change; how PRs merge; when to ask the owner |
| [principles.md](principles.md) | Making a design choice; wondering why a rule exists |
| [testing.md](testing.md) | Writing or fixing tests; dealing with a flaky test |
| [security.md](security.md) | Touching secrets, auth, dependencies, CI or infra |
| [operations.md](operations.md) | Deploying, rolling back, debugging production |
| [feature-flags.md](feature-flags.md) | Shipping something risky or unfinished |
| [adr/](adr/README.md) | Before structural changes; when recording a decision |
| [exec-plans/](exec-plans/README.md) | Resuming work; finding what's in flight; tech debt |
| [runbooks/](runbooks/) | Step-by-step operational procedures (incl. [cloud sessions](runbooks/cloud-sessions.md)) |
| [postmortems/](postmortems/) | Learning from past incidents |

Docs follow the code: a PR that changes behaviour updates the doc that describes it.
`make docs-check` catches broken links, an over-long AGENTS.md, and ADRs missing from
the index.
