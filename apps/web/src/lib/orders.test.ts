import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { lineAmount, ordersMessage, pickOutcome } from "./orders";

describe("pickOutcome", () => {
  it("0 packed is NO, not a cancellation", () => {
    expect(pickOutcome("15", "0")).toBe("NO");
    expect(pickOutcome("15", "0.0000")).toBe("NO");
  });
  it("partial is SHORT, full is PICKED", () => {
    expect(pickOutcome("15", "10")).toBe("SHORT");
    expect(pickOutcome("15.0000", "15")).toBe("PICKED");
  });
});

describe("lineAmount", () => {
  it("multiplies exactly and rounds half-up to cents", () => {
    expect(lineAmount("26.5000", "15")).toContain("397.50");
    expect(lineAmount("0.4500", "3.3333")).toContain("1.50");
    expect(lineAmount("1.3350", "1")).toContain("1.34");
  });
});

describe("ordersMessage", () => {
  it("explains backend codes in plain language", () => {
    expect(ordersMessage(new ApiError(409, "EXCEEDS_PENDING", "x", { pending: "5.0000" }))).toBe(
      "Only 5.0000 is still pending.",
    );
    expect(ordersMessage(new ApiError(409, "AWAITING_INVOICE", "x"))).toMatch(/Invoice them/);
    expect(ordersMessage(new Error("boom"))).toMatch(/check whether your last change was saved/);
  });
});
