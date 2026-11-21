@AGENTS.md

## Claude Code specifics
- The agentic kit is installed into `.claude/` as plain project files, listed in
  `.claude/kit.lock`: skills `/deliver`, `/spec`, `/review`, `/escalate`, `/adr`,
  `/fix-ci`, `/feature-flag`, `/incident`, `/garden`, `/optimize`, `/bootstrap`;
  subagents `reviewer`, `security-reviewer`, `architect`, `qa` and an `Explore` that
  replaces the built-in one (read-only, Haiku); hooks in
  `.claude/hooks/`, wired in `.claude/settings.json`; the PR helper
  `.claude/scripts/gh-pr.sh`. Plain project files load in every surface. Plugins don't:
  cloud sessions and Projects threads load no plugin declared in a repository, nor a
  plugin folder under `.claude/skills/`.
- Model and effort: desktop and CLI sessions start on Opus at high effort
  (`.claude/settings.json`); cloud sessions use the session picker instead. The kit's
  subagents pin their own. See `docs/workflow.md` → Model and effort.
- Kit hooks: orientation at session start; guards on Bash and on file reads and writes
  (secrets, git internals, kit files); a stop gate that runs `make check-fast` when
  there are uncommitted changes.
- If the skills ever fail to load, follow `docs/workflow.md` directly, and run reviewers
  as general-purpose subagents with the prompts in `.claude/agents/`.
- For long unattended runs the owner may set `/goal`; treat it as the definition of done
  on top of AGENTS.md, not instead of it.
- Prefer subagents for independent review and for parallel investigation; give them file
  paths, not summaries of your reasoning.
- Personal, uncommitted preferences go in `CLAUDE.local.md` (gitignored).
