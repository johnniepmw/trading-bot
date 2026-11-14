import { defineConfig, devices } from "@playwright/test";

// E2E_BASE_URL targets a deployment (PR preview, staging). Without it, Playwright starts
// the app locally. Tests tagged @flaky only run when E2E_INCLUDE_QUARANTINED is set
// (nightly), per docs/testing.md.
const external = process.env["E2E_BASE_URL"];
const ci = Boolean(process.env["CI"]);

export default defineConfig({
  testDir: "e2e",
  fullyParallel: true,
  forbidOnly: ci,
  retries: 0,
  ...(process.env["E2E_INCLUDE_QUARANTINED"] ? {} : { grepInvert: /@flaky/ }),
  reporter: ci ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: external ?? "http://localhost:8080",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  ...(external
    ? {}
    : {
        webServer: {
          command: "node --disable-warning=ExperimentalWarning src/server.ts",
          url: "http://localhost:8080/healthz",
          reuseExistingServer: !ci,
          timeout: 30_000,
        },
      }),
});
