import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { checkPlan, moneyOrNull, paymentMessage, suggestAllocation } from "./payments";

const invs = [
  { id: "a", invoice_no: "INV-1", balance: "1250.00", due_at: "2026-10-01T00:00:00Z" },
  { id: "b", invoice_no: "INV-2", balance: "100.00", due_at: "2026-09-01T00:00:00Z" },
];

describe("payments", () => {
  it("accepts positive amounts with at most 2 decimals", () => {
    expect(moneyOrNull("700")).toBe(700_000000n);
    expect(moneyOrNull(" 700.5 ")).toBe(700_500000n);
    for (const bad of ["0", "-1", "1.234", "abc", "", "1e3"]) expect(moneyOrNull(bad)).toBeNull();
  });

  it("700 on a 1,250 invoice leaves 550 (suggestion uses the named invoice first)", () => {
    expect(suggestAllocation("700.00", invs, "a")).toEqual({ a: "700.00" });
  });

  it("spreads oldest due first and never exceeds a balance", () => {
    expect(suggestAllocation("300.00", invs)).toEqual({ b: "100.00", a: "200.00" });
    expect(suggestAllocation("5000.00", invs)).toEqual({ b: "100.00", a: "1250.00" });
  });

  it("flags plans over an invoice balance or over the money available", () => {
    expect(checkPlan({ a: "700.00" }, invs, "700.00").problems).toEqual([]);
    expect(checkPlan({ b: "150" }, invs, "700.00").problems).toEqual(["INV-2: more than the CAD 100.00 open"]);
    expect(checkPlan({ a: "600", b: "100" }, invs, "650.00").problems).toEqual(["Total is more than the CAD 650.00 available"]);
    expect(checkPlan({ a: "1.234" }, invs, "10").problems[0]).toMatch(/enter an amount/);
  });

  it("explains a duplicate confirmation in plain words", () => {
    const e = new ApiError(409, "DUPLICATE_CONFIRMATION", "x", { payment_no: "PAY-2026-000001" });
    expect(paymentMessage(e)).toMatch(/already confirmed payment PAY-2026-000001/);
    expect(paymentMessage(new Error("boom"))).toMatch(/check whether your last change was saved/);
  });
});
