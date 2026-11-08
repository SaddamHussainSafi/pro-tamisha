import { describe, expect, it } from "vitest";
import type { UnitProfile } from "../api";
import { formProblem, qtyProblem, unitsFor } from "./directInvoice";

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
});
