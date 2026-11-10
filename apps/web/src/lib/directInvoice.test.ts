import { describe, expect, it } from "vitest";
import type { Invoice, UnitProfile } from "../api";
import { billable, formProblem, productsFromInvoice, qtyProblem, unitsFor, untouched, withPrefill, type DraftLine } from "./directInvoice";

const prof = (code: string, unit: UnitProfile["unit_code"], d = false, active = true): UnitProfile => ({ id: code, code, unit_code: unit, pieces_per_unit: null, weight_lb_per_unit: null, is_default: d, active });

describe("direct invoice form", () => {
  it("offers the default case-size unit first", () => {
    expect(unitsFor({ stock_unit: "LB", unit_profiles: [prof("PC", "PIECE"), prof("30LB", "BOX", true)] })).toEqual(["BOX", "PIECE", "LB"]);
    expect(unitsFor({ stock_unit: "BOX", unit_profiles: [prof("OLD", "PIECE", false, false)] })).toEqual(["BOX"]);
  });

  it("checks quantities by unit", () => {
    expect(qtyProblem("BOX", "3")).toBeNull();
    expect(qtyProblem("BOX", "2.5")).toMatch(/whole/);
    expect(qtyProblem("LB", "12.25")).toBeNull();
    expect(qtyProblem("LB", "1.255")).toMatch(/2 decimals/);
    expect(qtyProblem("BOX", "0")).toMatch(/above 0/);
    expect(qtyProblem("", "3")).toMatch(/unit/);
  });

  it("reports the first problem in the form", () => {
    const l = (product_id: string, unit_code: "BOX" | "", qty: string) => ({ key: 1, product_id, unit_code, qty });
    expect(formProblem("", [l("p", "BOX", "1")])).toMatch(/customer/);
    expect(formProblem("c", [l("", "", "")])).toMatch(/at least one/);
    expect(formProblem("c", [l("p", "BOX", "1"), l("p", "BOX", "2")])).toMatch(/two lines/);
    expect(formProblem("c", [l("p", "BOX", "1"), l("", "", "")])).toBeNull();
  });

  it("bills prefilled lines only when a quantity is entered", () => {
    const pre = (product_id: string, qty: string) => ({ key: 2, product_id, unit_code: "BOX" as const, qty, prefilled: true });
    expect(formProblem("c", [pre("a", ""), pre("b", "")])).toMatch(/quantity for at least one/);
    expect(formProblem("c", [pre("a", ""), pre("b", "3")])).toBeNull();
    expect(billable([pre("a", ""), pre("b", "3"), { key: 3, product_id: "c", unit_code: "BOX", qty: "" }]).map((x) => x.product_id)).toEqual(["b", "c"]);
    expect(formProblem("c", [pre("b", "3"), { key: 3, product_id: "c", unit_code: "BOX", qty: "" }])).toMatch(/Line 2: Enter a quantity/);
  });

  it("applies a prefill only to an untouched list and keeps the user's own lines", () => {
    const own: DraftLine = { key: 1, product_id: "", unit_code: "", qty: "" };
    const old: DraftLine = { key: 2, product_id: "x", unit_code: "BOX", qty: "", prefilled: true };
    const pre: DraftLine = { key: 3, product_id: "y", unit_code: "BOX", qty: "", prefilled: true };
    const blank = () => ({ key: 9, product_id: "", unit_code: "" as const, qty: "" });
    expect(untouched([own, old])).toBe(true);
    expect(withPrefill([old, own], [pre], blank)).toEqual([pre, own]); // previous customer's lines go
    expect(withPrefill([old], [], blank)).toEqual([blank()]);
    const picked = { ...own, product_id: "z" };
    expect(untouched([picked])).toBe(false);
    expect(withPrefill([old, picked], [pre], blank)).toEqual([old, picked]); // never overwrites a pick
    const typed = { ...old, qty: "3" };
    expect(withPrefill([typed], [pre], blank)).toEqual([typed]);
  });

  it("lists the products of the last invoice once, weighed boxes as boxes", () => {
    const line = (product_id: string, unit_code: "BOX" | "LB" | "PIECE", cases: string | null) => ({ product_id, unit_code, cases }) as Invoice["lines"][number];
    expect(productsFromInvoice({ lines: [line("a", "BOX", "8"), line("b", "LB", "2"), line("b", "LB", "1"), line("c", "PIECE", null), line("a", "BOX", "1")] })).toEqual([
      { product_id: "a", unit_code: "BOX" },
      { product_id: "b", unit_code: "BOX" },
      { product_id: "c", unit_code: "PIECE" },
    ]);
  });
});
