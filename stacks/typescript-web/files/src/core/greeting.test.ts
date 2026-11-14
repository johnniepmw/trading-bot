import { describe, expect, it } from "vitest";
import { greeting } from "./greeting.ts";

describe("greeting", () => {
  it("greets by name", () => {
    expect(greeting("Ada")).toBe("Hello, Ada!");
  });

  it("falls back to the world for missing or blank names", () => {
    expect(greeting(null)).toBe("Hello, world!");
    expect(greeting("   ")).toBe("Hello, world!");
  });

  it("caps very long names", () => {
    expect(greeting("x".repeat(100))).toBe(`Hello, ${"x".repeat(40)}!`);
  });
});
