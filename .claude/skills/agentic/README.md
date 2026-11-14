# agentic (Claude Code plugin)

An opinionated agentic-engineering kit. It lets Claude take a goal from the owner to a
merged, deployed and verified change with minimal prompting, while guardrails keep
quality high and risk contained.

- **Skills**: `deliver` (the full loop), `spec`, `review`, `escalate`, `adr`, `fix-ci`,
  `feature-flag`, `incident`, `garden` (weekly upkeep), `optimize` (Karpathy-style
  ratchet loop), `bootstrap` (new projects).
- **Subagents**: `reviewer`, `security-reviewer`, `architect`, `qa`: fresh-eyes reviewers
  that only see files, never the author's reasoning.
- **Scripts**: `gh-pr.sh`, a pull-request helper that also works in cloud sessions.
- **Hooks**: session orientation; guards against gate bypasses, secret access and direct
  production changes; a stop gate that keeps `make check-fast` green.

Designed for repositories created from `johnniepmw/agentic-dev-template`, which provides the
`make` verbs, CI gates, docs layout and infrastructure the skills expect. Projects vendor
this folder at `.claude/skills/agentic/` with `scripts/kit-sync.sh`.
