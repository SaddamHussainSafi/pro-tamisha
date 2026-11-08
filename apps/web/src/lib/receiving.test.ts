import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { countTotal, diffText, moneyText, receivingMessage, saleableSummary, unitPlaces } from "./receiving";

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
    expect(receivingMessage(new Error("boom"))).toMatch(/Nothing was saved/);
  });
});
