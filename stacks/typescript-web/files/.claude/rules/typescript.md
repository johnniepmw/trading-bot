---
paths:
  - "src/**/*.ts"
  - "e2e/**/*.ts"
---
- TypeScript runs directly on Node 24 (type stripping): use only erasable syntax. No
  enums, namespaces or constructor parameter properties; import local files with `.ts`.
- `strict` + `noUncheckedIndexedAccess`. No `any`, no non-null `!`; narrow instead.
- Parse untrusted input at the boundary (request params, env, JSON) into typed values.
- `src/core/` is pure: no Node built-ins, no I/O, no adapters (dependency-cruiser enforces).
- Feature checks go through `flags.isEnabled("<name>")` with the name registered in `flags.json`.
- Errors: throw `Error` subclasses with context; never swallow; log JSON with `severity`.
- Escape all user-controlled text in HTML output.
- Tests: Vitest next to the code (`*.test.ts`), Playwright in `e2e/`.
