# Uniform verbs for people, agents and CI. CI calls only these targets.
# Stack-specific commands come from stack.mk (written by scripts/init-stack.sh), which
# sets the STACK_* variables below. Without a stack, those steps print "skipped".
SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

-include stack.mk

# Use mise-managed tools without shell activation (local, cloud sessions and CI alike).
export PATH := $(or $(MISE_DATA_DIR),$(HOME)/.local/share/mise)/shims:$(PATH)

APP              ?= $(shell basename "$(CURDIR)")
ENV              ?= staging
REGION           ?= europe-west2
STACK_SETUP      ?= @echo "setup: no stack yet (scripts/init-stack.sh)"
STACK_FMT        ?= @echo "fmt: skipped (no stack)"
STACK_FMT_CHECK  ?= @echo "fmt-check: skipped (no stack)"
STACK_LINT       ?= @echo "lint: skipped (no stack)"
STACK_TYPECHECK  ?= @echo "typecheck: skipped (no stack)"
STACK_TEST_UNIT  ?= @echo "test-unit: skipped (no stack)"
STACK_TEST_INT   ?= @echo "test-integration: skipped (no stack)"
STACK_TEST_ONE   ?= @echo "test-one: skipped (no stack)"
STACK_ARCH       ?= @echo "arch: skipped (no stack)"
STACK_E2E_SETUP  ?= @echo "e2e-setup: skipped (no stack)"
STACK_E2E        ?= @echo "e2e: skipped (no stack)"
STACK_DEV        ?= @echo "dev: no stack yet"
STACK_MUTATION   ?= @echo "mutation: skipped (no stack)"

.PHONY: help setup fmt fmt-check lint typecheck test-unit test-integration test-one arch \
        check-fast check e2e e2e-setup dev image mutation docs-check flags-check \
        secrets-scan repo-lint risk logs smoke kit-sync \
        github-setup gh-vars secret-set infra-bootstrap infra-plan infra-apply env-up env-down

help: ## List targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ---------- everyday ----------------------------------------------------------------------
setup: ## Install toolchain and deps, enable git hooks
	@command -v mise >/dev/null && mise install || echo "mise not found: install from https://mise.jdx.dev (CI uses jdx/mise-action)"
	git config core.hooksPath .githooks
	chmod +x .githooks/* scripts/*.sh 2>/dev/null || true
	$(STACK_SETUP)

fmt: ## Format code
	$(STACK_FMT)
fmt-check:
	$(STACK_FMT_CHECK)
lint:
	$(STACK_LINT)
typecheck:
	$(STACK_TYPECHECK)
test-unit:
	$(STACK_TEST_UNIT)
test-integration:
	$(STACK_TEST_INT)
test-one: ## Run one test file/pattern: make test-one T=path
	$(STACK_TEST_ONE)
arch: ## Architecture/dependency rules
	$(STACK_ARCH)

check-fast: fmt-check lint typecheck test-unit ## Fast checks (<60s); the stop gate runs this
check: check-fast test-integration arch docs-check flags-check secrets-scan repo-lint ## Everything CI's check job runs

e2e-setup:
	$(STACK_E2E_SETUP)
e2e: ## End-to-end tests (E2E_BASE_URL to target a deployment)
	$(STACK_E2E)
dev: ## Run the app locally
	$(STACK_DEV)
mutation: ## Mutation tests (nightly)
	$(STACK_MUTATION)

image: ## Build the container image locally
	@if [ -f Dockerfile ]; then docker build --build-arg GIT_SHA=$$(git rev-parse --short HEAD) -t $(APP):local . ; else echo "image: no Dockerfile yet"; fi

# ---------- repo hygiene ------------------------------------------------------------------
docs-check: ## Docs links, AGENTS.md size, ADR index
	python3 scripts/check_docs.py
flags-check: ## flags.json valid, no expired release flags, no unregistered flags in code
	python3 scripts/check_flags.py
secrets-scan: ## gitleaks over the repo (and history in CI)
	@if command -v gitleaks >/dev/null; then gitleaks git --no-banner --redact --config .gitleaks.toml $${GITLEAKS_LOG_OPTS:+--log-opts="$$GITLEAKS_LOG_OPTS"} . ; else echo "secrets-scan: gitleaks not installed (make setup)"; [ -z "$${CI:-}" ]; fi
repo-lint: ## Lint workflows and shell scripts
	@if command -v actionlint >/dev/null; then actionlint; else echo "repo-lint: actionlint missing"; [ -z "$${CI:-}" ]; fi
	@if command -v shellcheck >/dev/null; then shellcheck -S warning scripts/*.sh .githooks/*; else echo "repo-lint: shellcheck missing"; [ -z "$${CI:-}" ]; fi
risk: ## Risk class of this branch vs origin/main
	python3 scripts/risk_classify.py
kit-sync: ## Update the vendored agentic kit (.claude/skills/agentic)
	bash scripts/kit-sync.sh

# ---------- operations (read-only for agents) ---------------------------------------------
logs: ## Recent service logs: make logs ENV=prod
	gcloud run services logs read "$(APP)-$(ENV)" --region "$(REGION)" --project "$${GCP_PROJECT:-$$(gcloud config get-value project 2>/dev/null)}" --limit 100
smoke: ## Smoke test a URL: make smoke URL=https://...
	bash scripts/smoke.sh "$(URL)"

# ---------- owner-only (the agent's guard hook blocks these) -------------------------------
github-setup: ## Repo settings, ruleset, labels, Dependabot (needs admin)
	bash scripts/github-setup.sh
gh-vars: ## Copy bootstrap outputs into GitHub Actions variables
	bash scripts/gh-vars.sh
secret-set: ## Add a secret version: make secret-set NAME=x ENV=staging (prompts for value)
	bash scripts/secret-set.sh "$(NAME)" "$(ENV)"
infra-bootstrap: ## One-time GCP bootstrap (state bucket, registry, CI identities)
	bash infra/bootstrap/bootstrap.sh

# ---------- infrastructure ----------------------------------------------------------------
infra-plan: ## make infra-plan ENV=staging|prod
	bash scripts/tofu.sh "$(ENV)" plan
infra-apply: ## make infra-apply ENV=staging (prod: CI or owner only)
	bash scripts/tofu.sh "$(ENV)" apply
env-up: infra-apply ## Create/update an environment from code
env-down: ## Destroy an environment (staging; prod has deletion protection)
	bash scripts/tofu.sh "$(ENV)" destroy
