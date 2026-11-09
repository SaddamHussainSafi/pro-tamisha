import { describe, expect, it } from "vitest";
import { returnTo } from "./returnTo";

describe("returnTo", () => {
  it("returns the page the user opened", () => {
    expect(returnTo({ from: "/products/abc?tab=x" })).toBe("/products/abc?tab=x");
    expect(returnTo({ from: "/" })).toBe("/");
  });
  it("falls back to Overview without a usable page", () => {
    expect(returnTo(null)).toBe("/");
    expect(returnTo(undefined)).toBe("/");
    expect(returnTo({})).toBe("/");
    expect(returnTo({ from: 42 })).toBe("/");
  });
  it("never leaves the app or loops back to sign-in", () => {
    expect(returnTo({ from: "//evil.example" })).toBe("/");
    expect(returnTo({ from: "/\\evil.example" })).toBe("/");
    expect(returnTo({ from: "https://evil.example" })).toBe("/");
    expect(returnTo({ from: "/login" })).toBe("/");
    expect(returnTo({ from: "/mfa?x=1" })).toBe("/");
    expect(returnTo({ from: "/login-help" })).toBe("/login-help");
  });
});
