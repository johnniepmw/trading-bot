// End-to-end tests run against E2E_BASE_URL (preview, staging) or a local server.
// They assume flags are at their registry defaults.
import { expect, test } from "@playwright/test";

test("health endpoint reports ok and a version", async ({ request }) => {
  const response = await request.get("/healthz");
  expect(response.ok()).toBe(true);
  const body = (await response.json()) as { status: string; version: string };
  expect(body.status).toBe("ok");
  expect(body.version).not.toBe("");
});

test("home page greets the visitor by name", async ({ page }) => {
  await page.goto("/?name=Ada");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Hello, Ada!");
});

test("home page shows user input as text, not markup", async ({ page }) => {
  await page.goto("/?name=" + encodeURIComponent("<b>x</b>"));
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Hello, <b>x</b>!");
});

test("unknown paths return 404", async ({ request }) => {
  const response = await request.get("/definitely-not-here");
  expect(response.status()).toBe(404);
});
