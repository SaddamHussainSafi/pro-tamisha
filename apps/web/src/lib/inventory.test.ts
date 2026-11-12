import { describe, expect, it } from "vitest";
import { ApiError, type Product } from "../api";
import { inventoryMessage, qtyText, signed, stockUnits } from "./inventory";

describe("inventory helpers", () => {
  it("formats quantities without float drift", () => {
    expect(qtyText("7.0000")).toBe("7");
    expect(qtyText("205.5000", "LB")).toBe("205.5 lb");
    expect(qtyText("0.0000")).toBe("0");
    expect(qtyText("0.1000")).toBe("0.1");
    expect(signed("-3.0000")).toBe("−3");
    expect(signed("2.2500")).toBe("+2.25");
  });

  it("explains oversell with the available quantity", () => {
    const e = new ApiError(409, "INSUFFICIENT_STOCK", "x", { available: "2.0000", requested: "6" });
    expect(inventoryMessage(e)).toContain("only 2 available");
    expect(inventoryMessage(new Error("boom"))).toMatch(/check whether your last change was saved/);
  });

  it("lists allowed units like the server", () => {
    const base = { stock_unit: "LB", unit_profiles: [] } as unknown as Product;
    expect(stockUnits(base)).toEqual(["LB"]);
    const p = {
      stock_unit: "KG",
      unit_profiles: [
        { unit_code: "BOX", active: true },
        { unit_code: "PIECE", active: false },
      ],
    } as unknown as Product;
    expect(stockUnits(p)).toEqual(["BOX", "LB", "KG"]);
  });
});
