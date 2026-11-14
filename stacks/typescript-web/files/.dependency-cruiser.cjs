// Architecture rules (ARCHITECTURE.md "Boundaries"). Messages tell the agent how to fix.
/** @type {import('dependency-cruiser').IConfiguration} */
module.exports = {
  forbidden: [
    {
      name: "core-is-pure",
      severity: "error",
      comment:
        "src/core holds business rules and must not import adapters, frameworks or Node built-ins. " +
        "Move the I/O to an adapter outside src/core and pass data or an interface in.",
      from: { path: "^src/core", pathNot: "\\.test\\.ts$" },
      to: { pathNot: "^src/core" },
    },
    {
      name: "no-circular",
      severity: "error",
      comment: "Circular imports make modules impossible to reason about. Extract the shared part.",
      from: {},
      to: { circular: true },
    },
    {
      name: "no-test-imports-in-prod",
      severity: "error",
      comment: "Production code must not import test files or test frameworks.",
      from: { path: "^src", pathNot: "\\.test\\.ts$" },
      to: { path: "(\\.test\\.ts$|^node_modules/(vitest|@playwright))" },
    },
  ],
  options: {
    doNotFollow: { path: "node_modules" },
    tsPreCompilationDeps: true,
    tsConfig: { fileName: "tsconfig.json" },
  },
};
