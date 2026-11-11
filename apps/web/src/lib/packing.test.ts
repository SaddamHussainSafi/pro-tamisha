import { describe, expect, it } from "vitest";
import { packingSheetPdfUrl } from "../api";
import { PACKING_COPIES, packingTotal, sheetDate } from "./packing";

describe("packing sheet", () => {
  it("adds up boxes and pieces; lb and kg lines are listed beside the total", () => {
    const t = packingTotal([
      { unit_code: "BOX", requested_qty: "10.0000" },
      { unit_code: "PIECE", requested_qty: "2.0000" },
      { unit_code: "LB", requested_qty: "12.5000" },
      { unit_code: "BOX", requested_qty: "1.0000" },
      { unit_code: "KG", requested_qty: "3.2500" },
      { unit_code: "LB", requested_qty: "0.5000" },
    ]);
    expect(t).toEqual({ qty: "13", byWeight: "+ 3.25 KG + 13 LB by weight" });
    expect(packingTotal([])).toEqual({ qty: "0", byWeight: "" });
  });

  it("prints the date like the office forms (dd-mm-yyyy)", () => {
    expect(sheetDate("2026-11-09")).toBe("09-11-2026");
    expect(sheetDate("")).toBe("");
  });

  it("prints 2 copies by default", () => {
    expect(PACKING_COPIES).toBe(2);
    expect(packingSheetPdfUrl("abc")).toBe("/api/v1/pick-attempts/abc/sheet.pdf?copies=2");
    expect(packingSheetPdfUrl("abc", 1)).toBe("/api/v1/pick-attempts/abc/sheet.pdf?copies=1");
  });
});
