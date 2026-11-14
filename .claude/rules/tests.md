---
paths:
  - "tests/**"
  - "e2e/**"
  - "**/*.test.ts"
  - "**/*.spec.ts"
  - "**/test_*.py"
---
- Name tests after behaviour: `rejects orders over the daily limit`, not `test_validate_2`.
- One behaviour per test; arrange-act-assert; no logic (loops, ifs) in test bodies.
- No sleeps: wait for a condition with a timeout. Inject clocks and randomness.
- E2E tests read the target from `E2E_BASE_URL`, create uniquely named data (prefix with
  the run ID) and clean up. They must pass against local, preview and staging.
- Never weaken an assertion or skip a test to get green. Quarantine flaky tests only per
  docs/testing.md (tag + issue + tech-debt entry).
