#!/usr/bin/env bash
# Add current versions of the dev toolchain and write pnpm-lock.yaml.
set -euo pipefail
pnpm add -D \
  typescript @types/node \
  eslint @eslint/js typescript-eslint \
  prettier \
  vitest \
  @playwright/test \
  dependency-cruiser \
  @stryker-mutator/core @stryker-mutator/vitest-runner
pnpm install
