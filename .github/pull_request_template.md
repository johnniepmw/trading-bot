## Summary
<!-- What changes for users or operators, in plain English. 2–4 lines. -->

**Plan:** `docs/exec-plans/…/<file>.md`

## Acceptance criteria → evidence
| # | Criterion | Evidence (test name, command + result, screenshot) |
|---|---|---|
| AC1 | | |

## Risk
- **Class:** standard | high — <why, from `make risk`>
- **Blast radius if wrong:** <who/what is affected>

## Rollout and rollback
- **Flag / kill switch:** none | `<name>` (default …)
- **Data changes:** none | expand/contract step …
- **Rollback:** revert PR | `ops` rollback | kill switch `<name>`

## Decisions made
- <decision> — <reason> (ADR-NNNN)

## Checklist
- [ ] `make check` and `make e2e` pass locally
- [ ] Fresh-eyes review done (`review` skill); blocking findings fixed
- [ ] Docs, `flags.json` and ADRs updated
- [ ] High risk only: "Owner decision needed" section below

<!-- For high-risk PRs, the escalate skill adds:
## Owner decision needed
**Decision:** … **Why it is flagged:** … **What changes if approved:** … **Safety net:** …
**Options:** … **Recommendation:** … **If no answer:** …
-->
