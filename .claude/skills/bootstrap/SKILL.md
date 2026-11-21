---
name: bootstrap
description: Set up a new project from the agentic template (pick a stack pack, fill in project docs, get CI green) or retrofit the template onto an existing repository. Run once per project.
argument-hint: "[project name] [typescript-web|python] [one-line purpose]"
disable-model-invocation: true
---

# Bootstrap a project

Input: $ARGUMENTS

## 0. Gather the minimum
Needed: project name, one-line purpose, stack pack (`typescript-web` or `python`), and
which parts of the domain are high risk (money, personal data, external side effects).
Read what you can from the repo and the owner's message; ask once for anything missing,
with a recommended default for each.

## 1. Get the template in place
- **New repo created from the template**: the files are already here. Continue.
- **Existing repo**: `git clone https://github.com/johnniepmw/agentic-dev-template /tmp/agentic-dev-template`, then
  copy in everything except `stacks/` that does not already exist. Merge (do not
  overwrite) `.gitignore`, `Makefile`, `mise.toml` and any existing CI. List conflicts
  in the plan and resolve them conservatively.
- Make sure the kit is installed: `bash scripts/kit-sync.sh` puts its skills, subagents,
  hooks and scripts into `.claude/` and lists them in `.claude/kit.lock`. Check that
  `.claude/settings.json` wires the four hooks as the template's does.

## 2. Apply the stack pack
`bash scripts/init-stack.sh <pack>`. It copies the pack, installs the toolchain with mise,
adds current dev dependencies, writes lockfiles, and wires `stack.mk`. Requires network.

## 3. Make it this project
- `README.md`, the Project section of `AGENTS.md`, and `ARCHITECTURE.md` (bird's-eye
  view, initial codemap, invariants). Keep AGENTS.md under 150 lines.
- `.github/risk-paths.txt`: add project-specific high-risk paths (for example the
  trading bot's `src/**/risk/**` and limits config).
- `flags.json`: remove the example flag once real flags exist.
- ADR for the stack and any project-specific choices (`adr` skill). The template's
  ADRs 0001–0005 stay as they are unless this project deliberately differs; supersede
  rather than edit.

## 4. Prove the pipeline locally
`make setup && make check && make e2e` until green. Then `make image` if Docker is available.

## 5. Owner-only steps (list them; do not do them yourself)
These need the owner's accounts, money or admin rights. Put them in the final report as a
checklist with exact commands:
1. GitHub Pro (private-repo rulesets and auto-merge), then `make github-setup`
   (repo settings, ruleset, labels, Dependabot). Owner-run: the guard hook blocks agents
   from changing repository settings.
2. `claude setup-token`, then `gh secret set CLAUDE_CODE_OAUTH_TOKEN` (owner pastes the
   token into the terminal prompt, never into chat).
3. GCP project with billing, then `gcloud auth login && gcloud auth application-default login`,
   `make infra-bootstrap`, `make gh-vars`.
4. Secret values: `make secret-set NAME=<name> ENV=<env>` (reads from the terminal).

## 6. First PR
Ship the bootstrap as one PR through the `deliver` steps (review included). Because it
edits risk paths, it will need the owner's `/approve-risk`; the escalate brief is the
checklist above plus a summary of the project setup.
