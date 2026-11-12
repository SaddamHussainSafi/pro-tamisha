import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { invoiceMessage, toWeightRows, weightTotals } from "./invoices";

describe("weightTotals", () => {
  it("sums INV-006 weights exactly (205 lb over 7 cases)", () => {
    const rows = [20, 30, 40, 40, 25, 25, 25].map((w) => ({ cases: "1", weight: String(w) }));
    expect(weightTotals(rows)).toEqual({ cases: 7, lb: "205.00" });
    expect(weightTotals([{ cases: "2", weight: "40" }, { cases: "3", weight: "25.125" }])).toEqual({ cases: 5, lb: "155.38" });
  });
  it("rejects incomplete or invalid rows", () => {
    expect(weightTotals([])).toBeNull();
    expect(weightTotals([{ cases: "", weight: "20" }])).toBeNull();
    expect(weightTotals([{ cases: "1", weight: "1e3" }])).toBeNull();
    expect(weightTotals([{ cases: "1.5", weight: "20" }])).toBeNull();
    expect(weightTotals([{ cases: "1", weight: "0" }])).toBeNull();
    expect(weightTotals([{ cases: "1", weight: "0.12345" }])).toBeNull();
  });
  it("serialises without floats", () => {
    expect(toWeightRows([{ cases: "2", weight: " 40.10 " }])).toEqual([{ cases: 2, weight_lb: "40.10" }]);
  });
});

describe("invoiceMessage", () => {
  it("explains blocking errors", () => {
    expect(invoiceMessage(new ApiError(422, "WEIGHTS_MISMATCH", "x", { expected: "7.0000", given: "6" }))).toContain("7.0000");
    expect(invoiceMessage(new ApiError(422, "DISPATCH_NOT_CONFIRMED", "x"))).toContain("dispatch");
    expect(invoiceMessage(new Error("boom"))).toMatch(/check whether your last change was saved/);
  });
});
