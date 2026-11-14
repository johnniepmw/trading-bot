# typescript-web stack: Node 24 + pnpm. Root Makefile targets call these.
STACK_SETUP      := pnpm install --frozen-lockfile
STACK_FMT        := pnpm run fmt
STACK_FMT_CHECK  := pnpm run fmt:check
STACK_LINT       := pnpm run lint
STACK_TYPECHECK  := pnpm run typecheck
STACK_TEST_UNIT  := pnpm run test
STACK_TEST_INT   := @echo "test-integration: none yet (add tests/integration and a Vitest project)"
STACK_TEST_ONE   := pnpm exec vitest run $(T)
STACK_ARCH       := pnpm run arch
STACK_E2E_SETUP  := pnpm run e2e:setup
STACK_E2E        := pnpm run e2e
STACK_DEV        := pnpm run dev
STACK_MUTATION   := pnpm run mutation
