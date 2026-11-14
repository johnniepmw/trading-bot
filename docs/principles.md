# Engineering principles

The beliefs behind AGENTS.md, with examples. Where a principle can be checked by a tool,
it should be (see "Enforced by").

## How we work with agents
1. **Humans steer, agents execute.** The owner's leverage is the goal, the acceptance
   criteria and this harness, not the code. Improving the harness compounds; hand-fixing
   code does not.
2. **What the agent can't see doesn't exist.** Decisions made in chat, in someone's head
   or in a doc elsewhere are invisible next session. If it matters, it goes in the repo:
   a plan, an ADR, a doc, a test.
3. **Map, not manual.** AGENTS.md stays short and points to detail. Long instruction files
   dilute every line. Enforced by: `make docs-check` (line limits).
4. **Rules that must always hold are code.** If an instruction is broken twice, turn it
   into a hook, lint rule, architecture test or CI check. Lint messages should say how to
   fix the problem, because the agent reads them.
5. **A check to run beats an instruction to follow.** Agents are excellent at looping
   until a pass/fail signal goes green. Give every task one.
6. **Fresh eyes review.** The author's context is a liability in review. Reviewers get
   files, not reasoning.

## How we write code
7. **Simplest design that meets the spec.** Three similar lines beat a premature
   abstraction. Add configurability when a second real use appears, not before.
8. **Surgical diffs.** A change touches what its goal needs and nothing else. Example:
   fixing a date bug in `orders.ts` does not reformat `orders.ts` or rename helpers in it.
9. **Functional core, imperative shell.** Business rules are pure functions over data;
   I/O (HTTP, DB, clocks, randomness, brokers) lives at the edges behind small interfaces.
   This is what makes fast, reliable tests possible. Enforced by: architecture rules
   (dependency-cruiser or import-linter) in `make check`.
10. **Make illegal states unrepresentable.** Strong types at boundaries: parse, don't
    validate. Money is a decimal type with currency; time is UTC with an injectable
    clock; IDs are distinct types.
11. **Fail closed, loudly.** When unsure (missing config, unexpected response, failed
    check) stop the operation and surface the error, especially where money, data or
    security is involved. Never swallow exceptions.
12. **Idempotent by default.** Jobs, handlers and deploys can run twice without harm.
    Record intent before side effects; reconcile on start.
13. **One way to do each thing.** Before adding a pattern, find the existing one. If the
    existing one is wrong, replace it everywhere (separate PR) or write an ADR.

## How we ship
14. **Main is always releasable.** Unfinished work hides behind release flags.
15. **Deploys are boring and reversible.** Build once, promote the same image, smoke
    test before traffic, roll back automatically. Database changes are expand/contract
    so code and schema can deploy independently.
16. **Every risky feature has an off switch** that has been tested.
17. **Observability is part of done.** New failure modes produce a log line, a metric or
    an alert someone (or an agent) will see.
18. **Blameless, mechanical learning.** Postmortem actions are tests, checks and alerts,
    not resolutions to be careful.
