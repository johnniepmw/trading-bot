---
name: adr
description: Record an architecture decision record (ADR) in docs/adr, or supersede an existing one. Use when a change adds a dependency, service, datastore or pattern, crosses an architecture boundary, or makes a choice that would be expensive to reverse or that someone will later ask "why?" about.
argument-hint: "[decision title]"
---

# ADR: write down why, so future sessions don't drift

Decision: $ARGUMENTS

1. List `docs/adr/`. Find the next number (four digits). Check whether an existing ADR
   already covers this; if the new decision replaces it, you are superseding.
2. Copy [template.md](template.md) to `docs/adr/NNNN-<kebab-title>.md` and fill it in.
   Keep it under one screen. Options must be real alternatives you considered, each with
   its main trade-off. Consequences must include the costs, not only the benefits.
3. Status:
   - `accepted` for decisions inside the agent's authority (reversible, standard risk).
   - `proposed` when the decision is high risk or changes a non-negotiable; the PR then
     goes through the `escalate` skill and the owner's approval flips it to accepted.
4. Superseding: set the old ADR's status to `superseded by NNNN` and link both ways.
   Never delete or rewrite an accepted ADR's reasoning; history is the point.
5. Add the ADR to the index in `docs/adr/README.md`.
6. If the decision creates a rule that should always hold, also enforce it: a lint rule,
   an architecture test (dependency-cruiser / import-linter), a CI check, or a line in
   AGENTS.md. Note the enforcement in the ADR's "Enforcement" line.
