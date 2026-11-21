# Exec plans

One file per change: `active/YYYY-MM-DD-<slug>.md` while in flight, moved to
`completed/` in the PR that ships it. The file holds the spec (goal, acceptance
criteria, non-goals, risk), the plan, a progress log, decisions, review notes and
learnings. It is the agent's external memory: any session can resume from it.

Template: the kit's `spec` skill (`.claude/skills/spec/plan-template.md`).

`tech-debt.md` collects problems noticed but deliberately not fixed in scope. The
`garden` skill picks from it weekly.
