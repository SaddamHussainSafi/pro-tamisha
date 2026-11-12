import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { todayIso } from "./format";
import { countTotal, defaultUnit, diffText, docsInKg, findProduct, kgSavedAsLb, receiveUnits, moneyText, productLabel, receivingMessage, saleableSummary, unitPlaces } from "./receiving";

describe("receiving helpers", () => {
  it("adds count parts exactly and treats blanks as zero", () => {
    expect(countTotal("23", "2", "")).toBe("25.0000");
    expect(countTotal("0.1", "0.2", "0")).toBe("0.3000");
    expect(countTotal("", "", "")).toBe("0.0000");
    expect(countTotal("abc", "1", "")).toBe("1.0000");
  });

  it("formats differences with a real minus sign", () => {
    expect(diffText("-2.0000", "BOX")).toBe("−2 box");
    expect(diffText("1.5000", "LB")).toBe("+1.5 lb");
    expect(diffText("0.0000", "BOX")).toBe("0");
    expect(diffText(null, "BOX")).toBe("—");
  });

  it("summarises saleable stock from accepted only", () => {
    expect(
      saleableSummary([
        { accepted_qty: "23.0000", unit_code: "BOX" },
        { accepted_qty: "2.0000", unit_code: "BOX" },
        { accepted_qty: "10.2500", unit_code: "LB" },
        { accepted_qty: null, unit_code: "KG" },
      ]),
    ).toBe("25 box + 10.25 lb");
    expect(saleableSummary([])).toBe("nothing");
  });

  it("formats money with currency code and grouping", () => {
    expect(moneyText("1234.5", "USD")).toBe("USD 1,234.50");
    expect(moneyText("80.25", "CAD")).toBe("CAD 80.25");
  });

  it("whole units for boxes and pieces", () => {
    expect(unitPlaces("BOX")).toBe(0);
    expect(unitPlaces("PIECE")).toBe(0);
    expect(unitPlaces("LB")).toBe(4);
  });

  it("maps server errors to plain language", () => {
    const e = (code: string, msg = "x") => new ApiError(422, code, msg, {});
    expect(receivingMessage(e("DISCREPANCY_ACK_REQUIRED"))).toMatch(/acknowledgement/);
    expect(receivingMessage(e("VERSION_CONFLICT"))).toMatch(/reloaded/);
    expect(receivingMessage(e("UNMAPPED_LABEL", "“X” isn't known"))).toBe("“X” isn't known");
    expect(receivingMessage(new Error("boom"))).toMatch(/check whether your last change was saved/);
  });
});

describe("count grid product lookup", () => {
  const ps = [
    { sku: "ALO", name: "Aloe" },
    { sku: "ALOP", name: "Aloe (packaged)" },
    { sku: "CAS", name: "Cassava" },
  ];
  it("finds a product by its list label, code or name, in any case", () => {
    expect(productLabel({ sku: "ALO", name: "Aloe" })).toBe("ALO — Aloe");
    expect(findProduct(ps, "ALO — Aloe")?.sku).toBe("ALO");
    expect(findProduct(ps, "alop")?.sku).toBe("ALOP");
    expect(findProduct(ps, " cassava ")?.sku).toBe("CAS");
  });
  it("finds nothing while the text names no single product", () => {
    expect(findProduct(ps, "")).toBeNull();
    expect(findProduct(ps, "AL")).toBeNull();
    expect(findProduct(ps, "Aloe (pack")).toBeNull();
  });
});

describe("today's date for date fields", () => {
  it("is the local calendar day as YYYY-MM-DD", () => {
    expect(todayIso(new Date(2026, 10, 11, 23, 59))).toBe("2026-11-11");
    expect(todayIso(new Date(2027, 0, 5, 0, 1))).toBe("2027-01-05");
  });
});

describe("receiving units: box, lb and kg everywhere; lb unless the documents are in kg", () => {
  it("always offers box, lb and kg; box needs a case size, piece only when used", () => {
    expect(receiveUnits(null).map((u) => u.value)).toEqual(["BOX", "LB", "KG"]);
    expect(receiveUnits(["BOX", "LB"]).every((u) => !u.disabled)).toBe(true);
    const lbOnly = receiveUnits(["LB"]);
    expect(lbOnly.map((u) => [u.value, !!u.disabled])).toEqual([["BOX", true], ["LB", false], ["KG", false]]);
    expect(receiveUnits(["PIECE", "LB"]).map((u) => u.value)).toEqual(["BOX", "PIECE", "LB", "KG"]);
  });
  it("defaults to kg only when a document line is in kg", () => {
    const line = (raw_label: string, unit_code: "LB" | "KG" = "LB", removed_at: string | null = null) => ({ raw_label, unit_code, removed_at });
    expect(defaultUnit(docsInKg([]))).toBe("LB");
    expect(defaultUnit(docsInKg([line("0910990000 THYME co 105 KG 15 55.00")]))).toBe("KG");
    expect(docsInKg([line("YAM 40 LB", "KG")])).toBe(true);
    expect(docsInKg([line("THYME 105 KG", "LB", "2026-11-12T00:00:00Z")])).toBe(false); // withdrawn
    expect(docsInKg([line("KGALA ROOTS 10 LB")])).toBe(false);
  });
  it("says when kg will be saved as lb", () => {
    expect(kgSavedAsLb("KG", ["LB"])).toBe(true);
    expect(kgSavedAsLb("KG", ["KG", "LB"])).toBe(false);
    expect(kgSavedAsLb("LB", ["LB"])).toBe(false);
    expect(kgSavedAsLb("KG", null)).toBe(false);
  });
});
