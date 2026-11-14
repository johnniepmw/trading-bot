@AGENTS.md

## Claude Code specifics
- The agentic kit is vendored at `.claude/skills/agentic/` and loads as the `agentic`
  plugin: skills `/agentic:deliver`, `spec`, `review`, `escalate`, `adr`, `fix-ci`,
  `feature-flag`, `incident`, `garden`, `optimize`, `bootstrap`; subagents
  `agentic:reviewer`, `agentic:security-reviewer`, `agentic:architect`, `agentic:qa`.
  In Claude Code Projects, add the plugin in Project settings (threads don't load repo
  plugins). If neither is available, follow `docs/workflow.md` directly.
- Kit hooks: orientation at session start, a Bash and file-write guard, and a stop gate
  that runs `make check-fast` when there are uncommitted changes.
- For long unattended runs the owner may set `/goal`; treat it as the definition of done
  on top of AGENTS.md, not instead of it.
- Prefer subagents for independent review and for parallel investigation; give them file
  paths, not summaries of your reasoning.
- Personal, uncommitted preferences go in `CLAUDE.local.md` (gitignored).
