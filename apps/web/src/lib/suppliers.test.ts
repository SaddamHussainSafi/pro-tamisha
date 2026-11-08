import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { amountIn, amountProblem, balanceWords, supplierMessage } from "./suppliers";

describe("supplier helpers", () => {
  it("formats amounts in their own currency", () => {
    expect(amountIn("USD", "1234.5")).toBe("USD 1,234.50");
    expect(amountIn("CAD", "-50.00")).toBe("−CAD 50.00");
  });

  it("says what a balance means", () => {
    expect(balanceWords("USD", "112.50")).toBe("We owe USD 112.50");
    expect(balanceWords("USD", "0.00")).toBe("Nothing owed");
    expect(balanceWords("CAD", "-50.00")).toBe("Paid CAD 50.00 ahead");
  });

  it("checks amounts", () => {
    expect(amountProblem("200.00")).toBeNull();
    for (const bad of ["0", "-5", "1.001", "abc", ""]) expect(amountProblem(bad)).not.toBeNull();
  });

  it("explains errors", () => {
    expect(supplierMessage(new ApiError(409, "DUPLICATE_REFERENCE", "This reference is already recorded as SPAY-2026-000001"))).toMatch(/SPAY-2026-000001.*same payment twice/);
  });
});
