import { describe, expect, it } from "vitest";
import { envOverride, Flags, type FlagSource, type Registry } from "./flags.ts";

const registry: Registry = {
  "home-greeting": { type: "kill-switch", default: true },
  "csv-export": { type: "release", default: false },
};

function fakeSource(results: Array<Record<string, boolean> | Error>): FlagSource & { calls: number } {
  const source = {
    calls: 0,
    fetchOverrides(): Promise<Record<string, boolean>> {
      const next = results[Math.min(source.calls, results.length - 1)];
      source.calls += 1;
      return next instanceof Error ? Promise.reject(next) : Promise.resolve(next ?? {});
    },
  };
  return source;
}

describe("envOverride", () => {
  it("parses common truthy and falsy spellings", () => {
    expect(envOverride("csv-export", { FLAG_CSV_EXPORT: "on" })).toBe(true);
    expect(envOverride("csv-export", { FLAG_CSV_EXPORT: " FALSE " })).toBe(false);
  });

  it("ignores unknown values instead of guessing", () => {
    expect(envOverride("csv-export", { FLAG_CSV_EXPORT: "maybe" })).toBeUndefined();
  });
});

describe("Flags", () => {
  it("uses the registry default when nothing overrides it", () => {
    const flags = new Flags(registry, { env: {} });
    expect(flags.isEnabled("home-greeting")).toBe(true);
    expect(flags.isEnabled("csv-export")).toBe(false);
  });

  it("throws for unregistered flags", () => {
    const flags = new Flags(registry, { env: {} });
    expect(() => flags.isEnabled("nope")).toThrow(/flags.json/);
  });

  it("applies remote overrides after a refresh (kill switch turns the feature off)", async () => {
    const flags = new Flags(registry, { env: {}, source: fakeSource([{ "home-greeting": false }]) });
    await flags.refresh();
    expect(flags.isEnabled("home-greeting")).toBe(false);
  });

  it("lets an environment variable win over the remote override", async () => {
    const flags = new Flags(registry, {
      env: { FLAG_HOME_GREETING: "true" },
      source: fakeSource([{ "home-greeting": false }]),
    });
    await flags.refresh();
    expect(flags.isEnabled("home-greeting")).toBe(true);
  });

  it("keeps the last good overrides when the source fails", async () => {
    const flags = new Flags(registry, {
      env: {},
      source: fakeSource([{ "home-greeting": false }, new Error("storage down")]),
    });
    await flags.refresh();
    await flags.refresh();
    expect(flags.isEnabled("home-greeting")).toBe(false);
  });

  it("refreshes at most once per interval", async () => {
    let now = 0;
    const source = fakeSource([{}]);
    const flags = new Flags(registry, { env: {}, source, now: () => now });
    await flags.refresh();
    flags.isEnabled("home-greeting");
    expect(source.calls).toBe(1);
    now = 31_000;
    flags.isEnabled("home-greeting");
    expect(source.calls).toBe(2);
  });
});
