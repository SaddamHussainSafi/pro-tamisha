import { describe, expect, it } from "vitest";
import type { PriceVersion, UnitProfile } from "../api";
import { caseSizeText, defaultCaseWeight, defaultSize, draftCode, priceBooks, suggestCode } from "./caseSizes";

const size = (code: string, w: string | null, d = false, active = true, unit: UnitProfile["unit_code"] = "BOX"): UnitProfile => ({
  id: code,
  code,
  unit_code: unit,
  pieces_per_unit: null,
  weight_lb_per_unit: w,
  is_default: d,
  active,
});

describe("case sizes", () => {
  it("describes sizes plainly", () => {
    expect(caseSizeText(size("30LB", "30.0000"))).toBe("30LB · 30 lb a box");
    expect(caseSizeText({ ...size("C24", "12.5000"), pieces_per_unit: "24.0000" })).toBe("C24 · 12.50 lb a box · 24 pcs");
    expect(caseSizeText(size("CASE", null))).toBe("CASE · weight not set");
    expect(caseSizeText(size("LB", null, false, true, "LB"))).toBe("LB · sold by lb");
  });

  it("suggests a code from the weight", () => {
    expect([suggestCode("BOX", "30"), suggestCode("BOX", "35.5"), suggestCode("BOX", "abc"), suggestCode("LB", "30")]).toEqual(["30LB", "35.5LB", "", ""]);
  });

  it("picks the active default, else the first active", () => {
    expect(defaultSize([size("A", "1"), size("B", "2", true)])?.code).toBe("B");
    expect(defaultSize([size("A", "1", true, false), size("B", "2")])?.code).toBe("B");
    expect(defaultSize([size("A", "1", true, false)])).toBeUndefined();
    expect(defaultCaseWeight([size("30LB", "30.0000", true), size("35LB", "35.0000")], "BOX")).toBe("30.0000");
    expect(defaultCaseWeight([size("PC", null, true, true, "PIECE")], "BOX")).toBeNull();
  });
});

describe("price drafts", () => {
  it("names drafts by date and time", () => {
    expect(draftCode(new Date(2026, 10, 8, 9, 5))).toBe("PB-20261108-0905");
  });

  it("finds the current book and the newest draft", () => {
    const v = (id: string, state: PriceVersion["state"], created_at: string) => ({ id, state, created_at }) as PriceVersion;
    const r = priceBooks([v("a", "SUPERSEDED", "1"), v("b", "CURRENT", "2"), v("c", "DRAFT", "3"), v("d", "DRAFT", "4")]);
    expect([r.current?.id, r.draft?.id]).toEqual(["b", "d"]);
    expect(priceBooks([]).draft).toBeUndefined();
  });
});
