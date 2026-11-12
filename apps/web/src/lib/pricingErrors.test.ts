import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { pricingMessage, rateText, scopeLabel } from "./pricingErrors";

describe("pricingMessage", () => {
  it("explains conflicts and duplicates in plain language", () => {
    expect(pricingMessage(new ApiError(409, "VERSION_CONFLICT", "x"))).toMatch(/changed this price book/);
    expect(pricingMessage(new ApiError(409, "DUPLICATE_UPLOAD", "x"))).toMatch(/already uploaded/);
    expect(pricingMessage(new ApiError(409, "INVALID_STATE", "x"))).toMatch(/new draft/);
  });
  it("counts CSV problems", () => {
    expect(pricingMessage(new ApiError(422, "CSV_INVALID", "x", { error_count: 3 }))).toMatch(/3 problem/);
  });
  it("falls back to server message and generic text", () => {
    expect(pricingMessage(new ApiError(422, "INVALID_UNIT", "CAS has no PIECE"))).toBe("CAS has no PIECE");
    expect(pricingMessage(new Error("boom"))).toMatch(/check whether your last change was saved/);
  });
});

describe("scopeLabel", () => {
  it("labels scopes", () => {
    expect(scopeLabel("STANDARD", null)).toBe("Standard");
    expect(scopeLabel("GROUP", "WHOLESALE")).toBe("Group · WHOLESALE");
    expect(scopeLabel("CUSTOMER", "Island")).toBe("Customer · Island");
  });
});

describe("rateText", () => {
  it("keeps at least two decimals and trims trailing zeros", () => {
    expect(rateText("1.5000")).toBe("CAD 1.50");
    expect(rateText("29")).toBe("CAD 29.00");
    expect(rateText("1.2340")).toBe("CAD 1.234");
    expect(rateText("0.0125")).toBe("CAD 0.0125");
  });
});
