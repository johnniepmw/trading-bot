# Phase 0 bootstrap: python stack, project docs, ADRs and gates

- **Status**: blocked (owner decision: `/approve-risk` on the bootstrap PR)
- **Branch / PR**: `claude/relaxed-thompson-22prga` (based on `claude/phase0-seed`) / https://github.com/johnniepmw/trading-bot/pull/1
- **Risk class**: high (`make risk`: `.claude/settings.json`, `.github/**`, `Dockerfile`, `flags.json`, `src/tradingbot/{app,brokers,config,execution,ledger,risk}/**`)
- **Started**: 2026-11-14 (brief) / 2026-11-21 (bootstrap)

## Goal
Turn the agentic template into this project: the python stack, the `tradingbot` package
layout with enforced boundaries, the owner's decisions recorded as ADRs, the gates
working, and a checklist of what only the owner can do. No strategy code.

## Acceptance criteria
| # | Criterion (observable, pass/fail) | Evidence |
|---|---|---|
| AC1 | Python stack applied; `make check` passes locally | `make check`: ruff, mypy strict (16 files), 38 unit tests (+1 strict xfail tripwire), 8 import contracts kept, docs-check 0 errors, flags-check 0 errors, gitleaks "no leaks found", actionlint and shellcheck clean (2026-11-21) |
| AC2 | `make e2e` passes: the job entry point runs as Cloud Run will run it | `tests/e2e/test_smoke.py`: 3 passed |
| AC3 | Package layout from the brief exists under `src/tradingbot/` | `find src/tradingbot -name __init__.py` (domain, data, strategy, portfolio, risk, execution, brokers/{trading212,fake}, ledger, config, app) |
| AC4 | The four import contracts are enforced, each fails when violated, and loosening them is high risk | `make arch` 8 contracts kept; planted-violation table below; `.importlinter` and `stack.mk` in risk paths (`risk_classify.py --paths stack.mk .importlinter` → high) |
| AC5 | `Money` refuses to mix GBP and GBX and rejects floats | `tests/domain/test_money.py` (23 tests incl. Hypothesis properties; currency must be a `Currency` member) |
| AC6 | Kill switch `new-orders` registered and visible to the job | `flags.json`; `tests/app/test_main.py`, `tests/e2e/test_smoke.py::test_kill_switch_override_is_visible_to_the_job` |
| AC7 | ADRs 0006–0011, 0013, 0014 accepted; 0012 proposed (deferred) | `docs/adr/README.md`; docs-check passes |
| AC8 | AGENTS.md, README, ARCHITECTURE.md filled in; AGENTS.md under 150 lines with the four extra rules | `wc -l AGENTS.md` = 124 |
| AC9 | High-risk paths from the brief in `.github/risk-paths.txt` | file diff |
| AC10 | Kit hooks run from `.claude/settings.json` without the plugin; each tested | hook table below; the guard blocked a real `core.hooksPath` override in this session |
| AC11 | First-time setup runbook describes the public repo, not GitHub Pro | `docs/runbooks/first-time-setup.md` §1 |
| AC12 | Container builds (digest-pinned base, root-owned code) and its command reports healthy | `make image` (base image via mirror.gcr.io; Docker Hub rate-limited); runtime uid 10001 gets "Permission denied" touching `/app/flags.json` (root 644); `docker run --rm trading-bot:local` printed `{"status": "ok", "version": "8ba8452", "new_orders": true}`; with `-e FLAG_NEW_ORDERS=off` → `"new_orders": false` |
| AC13 | A planted fake secret and a broken import rule are each rejected by the same checks CI runs | Local: pre-commit hook and `gitleaks dir` both rejected a fake `T212_API_KEY` (exit 1); `lint-imports` exits 1 on each planted violation. The CI demonstration is a post-merge follow-up (Plan step 8) |

## Non-goals
- Strategy code (Phase 1), the Cloud Run job module and job deploy workflow (Phase 2),
  any API keys or GCP changes.
- Recorded broker contract tests, the nightly demo contract check and backtest
  regression arrive in Phases 1–3.
- Changing the vendored kit (`.claude/skills/agentic/`).

## Rollout and rollback
- Flag / kill switch: `new-orders` (kill-switch, default true). Nothing trades yet.
- Data changes: none.
- Rollback: revert the PR. The deploy workflow is inactive until `make gh-vars`.

## Decisions
- `/agentic:bootstrap` didn't load (cloud sessions don't load repo plugins); followed
  `bootstrap/SKILL.md` by hand, as the brief and the owner said.
- `scripts/init-stack.sh python` stopped at `mise install` (GitHub releases and the
  python download host are unreachable from this session). Finished its remaining steps
  by hand with the system `uv` (init.sh, Dependabot entry, `.stack`, remove `stacks/`).
  CI uses `jdx/mise-action`, which can download. Logged in tech-debt.
- Template example app removed (`server.py`, `greeting`); `flags.py` moved to
  `tradingbot.app.flags`. The job's health check is `python -m tradingbot.app smoke`
  (status, version, kill-switch state) because a job has no `/healthz` (ADR-0011).
- `make e2e` runs the entry point as a subprocess; with `E2E_BASE_URL` set (the
  service-shaped preview job) it skips, since a job has no URL until Phase 2.
- Contract 3 allows `app` as well as `brokers` to import HTTP libraries: the template's
  flag override reader (`GcsSource`) fetches from Cloud Storage with `urllib`. Moving it
  into `brokers` would be wrong (it isn't a broker). The pure packages (contract 2a) and
  everything else outside `app` and `brokers` (3a) still can't.
- Contract 2 forbids `os` (environment variables) and `socket` too, and an
  `independence` contract stops strategy, portfolio and risk importing each other
  ("only domain and config").
- `Money` currencies: GBP, GBX, USD. `to_gbp()` converts pence; USD needs an explicit FX
  rate later.
- Hooks: copied the four entries from `hooks/hooks.json` into `.claude/settings.json`
  with `$CLAUDE_PROJECT_DIR/.claude/skills/agentic/hooks/...` paths (owner request). If a
  session also loads the plugin, hooks run twice (tech-debt).
- `scripts/github-setup.sh` left unchanged; public-repo settings are a manual checklist
  in the runbook (tech-debt to script them).
- The kit's bootstrap owner checklist still mentions GitHub Pro; it's vendored, so the
  fix goes upstream (tech-debt).
- `phase0_report.json` added to `.gitignore`: it holds demo account data (ADR-0014).
- The seed's `tests/scripts/` stay where they are; they pass and are ruff-clean.
- Review round 1: import contracts moved from `pyproject.toml` to `.importlinter`, and
  that file plus `stack.mk` added to risk paths, so disabling a gate is high risk.
  `pyproject.toml` stays standard (else every Dependabot bump needs the owner); moving
  ruff and mypy settings out is tech-debt.
- `src/tradingbot/domain/money.py` added to risk paths (AGENTS.md calls money high risk;
  the brief's list omitted `domain`).
- The `new-orders` kill switch fails open in a job if Cloud Storage is unreachable.
  Harmless in Phase 0 (nothing trades); recorded in tech-debt to fix before any order
  path reads it, and flagged in the PR's owner brief.
- Security round 1: `.gitleaks.toml`, `mise.toml` and `scripts/**` added to risk paths
  (gate scripts, the deploy and kill-switch scripts, and scripts the owner runs with their
  own credentials). `agent-review` now runs Claude with hooks disabled, because wiring
  hooks into `.claude/settings.json` would otherwise run PR-controlled hook scripts in a
  job that holds the review token. Unverified until the first CI run: whether the action
  honours `--settings '{"disableAllHooks": true}'`.
- Round 2: `make arch` passes `--config .importlinter` (a new `setup.cfg` would
  otherwise shadow it), and config files that could shadow gate settings (`setup.cfg`,
  `tox.ini`, `pytest.ini`, `mypy.ini`, `ruff.toml`, `.gitleaksignore` and dotted
  variants) are risk paths; they don't exist, so they cost no approvals until created.
- Round 2 (security): 3b–3d became allow-lists (`type = protected`), so new subpackages
  can't reach `execution` or `brokers` by default; 3a stays a deny-list because protected
  contracts can't name packages nothing imports yet (`httpx`). A strict-xfail test is a
  tripwire for the kill-switch fail-open. The risk-gate self-approval fix stays an owner
  decision (it changes how the owner's own approvals are checked), flagged in the brief.
- Contract 3c added (only `app` imports `execution`; only `execution` and `app` import
  `brokers`); contract 2a also forbids `importlib`, `subprocess`, `asyncio`, `ssl` and
  `pathlib`; 3a forbids `socket` and `subprocess`.
- Dockerfile: base image pinned by digest; code and `flags.json` root-owned. `uv` pinned
  to 0.11 in `mise.toml`.
- Deferred to tech-debt with reasons: kill-switch fail-open, market-data/order port split,
  redirect credential forwarding, the phase0 ref-price trap, Dependabot cooldown, and the
  pre-existing risk-gate self-approval gap (owner decision).

## Open questions
- CI canary PRs for AC13 need branches other than this session's one
  (`claude/relaxed-thompson-22prga`), and a planted secret must not land in the
  bootstrap PR's history. — default if unanswered: after the bootstrap merges, open two
  throwaway draft PRs (fake secret; broken import rule), record the failing checks, close
  them unmerged.

## Plan
1. Apply the python stack pack — check: `uv sync`, files in place
2. Package skeleton, `Money`, smoke entry point, import contracts — check: `make check-fast`, `make arch`, planted violations
3. Wire kit hooks into `.claude/settings.json` — check: payload tests below
4. Docs: AGENTS.md, README, ARCHITECTURE.md, ADRs, risk paths, runbooks, tech-debt — check: `make docs-check`
5. Full verification — check: `make check`, `make e2e`, `make image`
6. Fresh-eyes review — check: reviewer findings resolved
7. PR, escalate brief for `/approve-risk`, owner checklist
8. After merge: two throwaway draft PRs (fake secret; broken import rule) to show CI's `secrets-scan` and `check` jobs fail, then close them unmerged — check: failing check runs linked here (brief's "Done when")

## Progress log
- 2026-11-21 step 1: stack pack applied (manual finish after mise download failure) — evidence: commit `chore: apply the python stack pack`
- 2026-11-21 step 2: `src/tradingbot` layout, `Money`, `smoke`, contracts — evidence: 35 unit + 3 e2e tests pass; contracts table below
- 2026-11-21 step 3: hooks in `.claude/settings.json` — evidence: hook tables below; guard blocked a live `git -c core.hooksPath=/dev/null` in this session
- 2026-11-21 step 4: docs and ADRs — evidence: docs-check 0 errors
- 2026-11-21 step 6: two review rounds each (reviewer, security-reviewer); all blocking findings fixed — evidence: Review section
- 2026-11-21 step 7: PR https://github.com/johnniepmw/trading-bot/pull/1 opened with the owner brief
- 2026-11-21 CI: `risk-gate-evaluate` set the `risk-gate` status to pending correctly but failed posting its explanatory comment (`gh api` exit 1, error text not logged). Likely cause: the job had `pull-requests: read`; the workflows that comment successfully have `write`. Fixed both: permission raised and the script now prints GitHub's error. The workflow runs from `main`, so the fix applies after merge; the red check on this PR isn't required for merge.
- 2026-11-21 CI: `image` failed trivy (HIGH, fixed upstream): `zlib1g` CVE in the base image's Debian packages and `msgpack`/`setuptools` CVEs in pip's vendored libraries. Dockerfile now applies Debian security updates and removes pip (unused at runtime). Locally verified pip is gone and `smoke` still runs; the apt upgrade can't be exercised here (containers can't reach deb.debian.org), so CI's trivy run is the evidence.
- 2026-11-21 CI: `agent-review` fails on its first model call (2 s, no usage): the `CLAUDE_CODE_OAUTH_TOKEN` secret is present but appears invalid or not entitled — owner checklist item 4. Re-run once.
- 2026-11-21 step 5: `make check`, `make e2e`, `make image` green (gitleaks, actionlint, shellcheck installed locally from Go/PyPI for this run)

### Import contracts: planted violations (`PYTHONPATH=src uv run lint-imports`)
| Planted in | Statement | Contract broken |
|---|---|---|
| `risk` | `import urllib.request` | 2a and 3a |
| `strategy` | `import os` | 2a |
| `portfolio` | `import tradingbot.risk` | 2b |
| `domain` | `import tradingbot.config` | 1 |
| `execution` | `import tradingbot.brokers.fake` | 3b |
| `ledger` | `import http.client` | 3a |
| `data` | `import research.notebook` | 4 |
| `data` | `import tradingbot.execution` | 3c |
| `data` | `from tradingbot import brokers` | 3c |
| `config` | `import tradingbot.brokers` | 3c |
| `strategy` | `import importlib` | 2a |
| `strategy` | `import subprocess` | 2a and 3a |
| `ledger` | `import socket` | 3a |
| new `reporting` package | `import tradingbot.execution` | 3c (allow-list) |
| new `reporting` package | `import tradingbot.brokers` | 3d (allow-list) |
| `execution` | `import tradingbot.brokers.fake` | 3b |
| `data`, with a placeholder `setup.cfg` present | `import tradingbot.execution` | 3c (the shadow file is ignored) |
Clean tree: 8 kept, 0 broken. Known gap: a *new* subpackage importing `urllib` passes until
it is added to 3a's sources (documented in `.importlinter` and ARCHITECTURE.md).

### Hook tests (payload piped into the exact command from `.claude/settings.json`)
| Hook | Payload | Expected | Exit | Result |
|---|---|---|---|---|
| guard-bash | `git push origin main` | block | 2 | pass |
| guard-bash | `git push -u origin claude/relaxed-thompson-22prga` | allow | 0 | pass |
| guard-bash | `git push --force origin feat/x` | block | 2 | pass |
| guard-bash | `git commit --no-verify -m x` | block | 2 | pass |
| guard-bash | `gh api repos/o/r/statuses/abc -f state=success` | block | 2 | pass |
| guard-bash | `gh pr comment 1 --body "/approve-risk abc"` | block | 2 | pass |
| guard-bash | `make secret-set NAME=operator ENV=prod` | block | 2 | pass |
| guard-bash | `gcloud secrets versions access latest --secret=operator` | block | 2 | pass |
| guard-bash | `cd infra/envs/prod && tofu apply` | block | 2 | pass |
| guard-bash | `make check-fast` | allow | 0 | pass |
| guard-bash | `gh workflow run ops.yml -f action=kill -f env=prod -f flag=new-orders` | allow | 0 | pass |
| guard-write | Write `.env` | block | 2 | pass |
| guard-write | Write `.env.example` | allow | 0 | pass |
| guard-write | Write `.claude/skills/agentic/README.md` | block | 2 | pass |
| guard-write | Write `src/tradingbot/domain/money.py` | allow | 0 | pass |
| guard-write | Write `.git/config` | block | 2 | pass |
| guard-write | Write `infra/envs/prod/terraform.tfstate` | block | 2 | pass |
| orient | SessionStart `startup` | orientation printed, exit 0 | 0 | pass |
| stop-gate | clean tree | allow | 0 | pass |
| stop-gate | uncommitted change, `check-fast` passes | allow | 0 | pass |
| stop-gate | uncommitted change, lint fails (unused imports) | block with F401 output | 2 | pass |
| stop-gate | same, with `.agentic/allow-stop` | allow once, file removed | 0 | pass |
| stop-gate | same again | block (escape hatch is single use) | 2 | pass |
Stop-gate cases ran in a temporary git worktree.

## Owner checklist (owner-only; run in Google Cloud Shell)
1. Merge this PR: read the escalate brief, then comment `/approve-risk <sha>`.
2. `make github-setup` (repo settings, `main` ruleset, labels, Dependabot).
3. Public-repo settings from ADR-0014 (`docs/runbooks/first-time-setup.md` §1.2):
   interaction limits to collaborators; fork PR workflows need approval for all external
   contributors; secret scanning and push protection on; commit email private.
4. Agent-review token: `npm install -g @anthropic-ai/claude-code && claude setup-token`,
   then `gh secret set CLAUDE_CODE_OAUTH_TOKEN` (paste at the prompt, never in chat).
5. GCP project (europe-west2) with billing: `gcloud auth login && gcloud auth
   application-default login`, fill `infra/bootstrap/bootstrap.tfvars`,
   `make infra-bootstrap`. **Don't run `make gh-vars` until the Phase 2 job module
   lands**: with the variables set, CI's preview job and the deploy workflow try to
   deploy the bot as a service and fail on every PR.
6. Run `scripts/phase0_api_check.py` from the merged `main` (not a PR branch) with a
   demo key entered via `read -rs`; keep `phase0_report.json` out of the repo and share
   it only privately.
7. Decide the risk-gate self-approval gap (tech-debt: the agent acts under your GitHub
   identity in cloud sessions, so a `/approve-risk` comment it posted would count).

## Review
- Round 1 (reviewer): changes required, 2 blocking / 7 non-blocking. Fixed: gate files
  (`stack.mk`, contracts) now high risk; AC13 restated with a post-merge step;
  `Money` validates its currency; `money.py` high risk; `gh-vars` warning; interim notes
  in operations.md and testing.md; phase0 script docstring says account data stays
  private; ARCHITECTURE notes on contract upkeep, `os`-free config types and GCP
  libraries; kill-switch fail-open logged as tech-debt.
- Round 1 (security-reviewer): changes required, 2 blocking / 7 other + notes. Fixed:
  gate config and `scripts/**` high risk; hooks disabled in agent-review; contract 3c and
  wider 2a/3a; Dockerfile digest pin and root-owned files; phase0 usage reads keys
  without shell history, says Python 3.11+, and the ignore pattern covers
  `phase0_report*.json`. Deferred with reasons: see Decisions and tech-debt.
- Round 2 (reviewer): 1 blocking (`setup.cfg` could shadow `.importlinter`) fixed by
  naming the file in `make arch` and listing shadowing config names as risk paths;
  ARCHITECTURE contract list and AC12 evidence corrected.
- Round 2 (security-reviewer): same blocker (fixed); verified that `disableAllHooks`
  stops project hooks in claude-code-action (CLI 2.1.330, action @v1 source read);
  contracts 3b–3d moved to allow-lists; kill-switch tripwire test added; remaining
  deferrals judged acceptable for Phase 0. Residual: `claude-code-action@v1` is a floating
  tag (pre-existing; tech-debt).

## Outcome
<filled when shipped>

## Learnings
- Cloud sessions: repo plugins don't load, so hooks must come from project settings;
  mise can't download from GitHub; gitleaks/actionlint/shellcheck install via
  `go install` and PyPI wrappers; Docker Hub may rate-limit, `mirror.gcr.io` works.

---

## Owner brief (2026-11-14, as given)

The owner starts the first Claude Code on the web session on `johnniepmw/trading-bot`
(after the one-time environment setup in `docs/runbooks/cloud-sessions.md`) with:

```
/agentic:bootstrap trading-bot python "Risk-first automated trading bot: a long-or-cash trend system on UK-listed ETFs via the Trading 212 API, run as a scheduled Cloud Run job on GCP"

Base your work on branch claude/phase0-seed and follow docs/exec-plans/active/2026-11-14-phase0-kickoff.md.
```

This file is that brief. Turn it into the bootstrap's exec plan as you go (same path). If
`/agentic:bootstrap` didn't load, follow `.claude/skills/agentic/skills/bootstrap/SKILL.md`
by hand, and check the harness as `docs/runbooks/cloud-sessions.md` describes.

## Context: decided by the owner, don't re-litigate
- **Goals:** learning first, profit second. Strong safeguards against outsized losses.
- **Account:** up to £1,000, no leverage, Trading 212 Invest (general investment account,
  so capital gains tax applies).
- **Strategy:** a long-or-cash trend system on 6–8 UK-listed ETFs. Daily data and risk
  checks; trades weekly to monthly.
- **Hosting:** GCP europe-west2. A Cloud Run **job** started by Cloud Scheduler (not a
  service). Direct VPC egress plus Cloud NAT gives a static IP for the IP-locked live key,
  added in Phase 4.
- **Repository:** public, instead of paying for GitHub Pro (ADR 0014). Code, Actions logs,
  PR and issue comments, check summaries and workflow artifacts are world-readable.
- **Owner role:** reviews no code. Approves high-risk PRs with `/approve-risk <sha>` after
  an escalate brief. Owner-only commands run in Google Cloud Shell.
- **Real-money controls stay owner-only and outside CI:** the live switch and capital
  allocation sit in an owner-only Secret Manager secret, `operator`, which the deploy
  identity can't write. Missing or invalid means paper trading only.
- **Kill switch:** the `ops` workflow's `kill` action stops new orders. Closing out
  positions is an action only the owner takes.
- **Alerts:** Cloud Monitoring, with the Google Cloud mobile app for urgent alerts and
  email for the daily summary. Both channels are free.
- **Full plan:** https://claude.ai/artifact/USLXmedSRi1myYH5HvVtUm (Engineering & CI/CD
  tab). This brief is the part this session needs.

## Package layout (`src/tradingbot/`)
- `domain`
- `data`
- `strategy`
- `portfolio`
- `risk`
- `execution`
- `brokers`: a Broker interface, with `trading212/` and `fake/` implementations
- `ledger`
- `config`
- `app`: entry points, scheduler, alerts, kill switch

`research/` holds notebooks and is never imported by `src`.

## import-linter contracts
1. `domain` imports nothing else from the project.
2. `strategy`, `portfolio` and `risk` import only `domain` and config types. They can't
   import brokers, data I/O, HTTP libraries or environment variables.
3. Only `brokers` imports HTTP libraries. Only `app` wires in concrete brokers.
4. `research` is never imported by `src`.

## Conventions (put these in ARCHITECTURE.md as invariants)
- **Money:** `Decimal` money with an explicit currency, and a `Money` type that refuses to
  mix GBP with GBX (pence).
- **Time:** UTC only, with an injectable clock. Market hours come from the broker's
  schedule.
- **Fail closed:** any unexpected error means no new orders, plus an alert.
- **Idempotent runs:** write intended orders to the ledger before sending them, and
  reconcile with the broker at the start of every run.
- **Config:** typed and validated at start-up. Invalid config means the bot refuses to
  start.
- **Logs:** structured JSON, tagged with the run ID, written to Cloud Logging.
- **Account data stays private:** balances, positions, orders, fills, P&L and raw broker
  responses never appear anywhere public (see ADR 0014).
- **Risk ceilings are constants in code:** no leverage; a hard stop at a 20% drawdown from
  peak; position-size caps. Day-to-day limits may only be tighter than the ceilings.

## High-risk paths to add to `.github/risk-paths.txt`
```
src/tradingbot/risk/**
src/tradingbot/brokers/**
src/tradingbot/execution/**
src/tradingbot/ledger/**
src/tradingbot/config/**
src/tradingbot/app/**
flags.json
```

## ADRs (the template keeps 0001–0005)
Write these now:
- **0006:** Python with uv, ruff, mypy in strict mode, pytest and Hypothesis.
- **0007:** our own small core built on open-source libraries, not a trading engine.
- **0008:** a broker interface, with Trading 212 first and IBKR as the fallback.
- **0009:** the risk layer has a veto; ceilings live in code; the live switch is owner-only
  and outside CI.
- **0010:** `Decimal` money with an explicit currency, and UTC time.
- **0011:** run as a scheduled Cloud Run job, with a deploy window. Never deploy prod while
  an execution is running, and only while the London market is closed. The template's
  module targets services, so this needs a job variant (the module itself is built in
  Phase 2).
- **0013 (accepted):** ledger storage is SQLite in a versioned GCS bucket, with a
  generation-match lock so only one run writes at a time. Rejected alternatives:
  Firestore, and Cloud SQL (about £7–10 a month, too costly for this account).
- **0014 (accepted): public repository.** Chosen over GitHub Pro ($4 a month) because a
  public repo gets rulesets, auto-merge, secret scanning and push protection on the free
  plan. Exposure is small: no secrets live in the repo, and the strategy is a well-known
  public approach. Consequences:
  - Account data (balances, positions, orders, fills, P&L, raw broker responses) never
    goes to Actions logs, PR or issue comments, check summaries or artifacts. It goes to
    Cloud Logging and the ledger only. CI and tests use fake data.
  - Owner settings: PR and issue creation restricted to collaborators; fork PR workflows
    need approval for all external contributors; secret scanning and push protection on;
    the owner's commit email kept private.
  - Revisit if the strategy ever becomes something worth keeping private: then GitHub Pro
    and a private repo.

Leave **0012 as proposed, deferred**: a static egress IP via Direct VPC egress and Cloud
NAT, about $4 a month. The owner reviews it at Phase 4.

ADR 0015, the first strategy, comes in Phase 1.

## Extra lines for AGENTS.md
- Never place orders against the live Trading 212 API. Demo only until the owner's
  `operator` secret says otherwise.
- This repo is public. Never write account data (balances, positions, orders, fills, P&L,
  raw broker responses) to Actions logs, PR or issue comments, check summaries or
  artifacts. Cloud Logging and the ledger only.
- The `optimize` skill must never tune strategy parameters without a held-out period it
  can't see.
- Bug fixes in the risk layer start with a failing test. The risk layer must have 100%
  branch coverage once it exists.

## Phase 0 scripts (already on `claude/phase0-seed`)
- `scripts/phase0_api_check.py`: demo-only, standard library only. The owner runs it in
  Cloud Shell with a demo key; its report shapes the broker adapter.
- `tests/scripts/mock_t212.py` and `tests/scripts/test_phase0_api_check.py` test it
  offline with no network. They pass and are ruff-clean under the Python pack's config;
  move them if the pack's layout wants other paths.
- Note in the exec plan that recorded broker contract tests, the nightly demo contract
  check and backtest regression arrive in Phases 1–3.

## Template text to update for this repo
- The bootstrap skill's owner checklist and `docs/runbooks/first-time-setup.md` assume a
  private repo on GitHub Pro. This repo is public: drop the Pro step and list the
  public-repo settings from ADR 0014 instead.

## Out of scope this session
- Strategy code
- The GCP job module (Phase 2)
- Any API keys or GCP changes

## Done when
- The bootstrap PR is merged. It needs the owner's `/approve-risk`.
- Follow-up PRs have merged through all three gates.
- A deliberately planted fake secret and a deliberately broken import rule each fail CI.
- The owner checklist is reported:
  - `make github-setup`
  - the public-repo settings (ADR 0014)
  - the agent-review token
  - the GCP project
