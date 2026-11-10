import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { canConfirm, confidencePct, needsReason, ocrMessage, openLines, pctText, trimQty, warningText } from "./ocr";

describe("ocr helpers", () => {
  it("only clear readings can be confirmed", () => {
    expect(canConfirm({ status: "OK", read_product_id: "p", read_qty: "2.0000" })).toBe(true);
    expect(canConfirm({ status: "LOW_CONFIDENCE", read_product_id: "p", read_qty: "2.0000" })).toBe(false);
    expect(canConfirm({ status: "UNMAPPED", read_product_id: null, read_qty: "2.0000" })).toBe(false);
    expect(canConfirm({ status: "OK", read_product_id: "p", read_qty: null })).toBe(false);
  });

  it("asks for a reason only when overriding a clear reading", () => {
    expect(needsReason({ status: "OK", kind: "LINE" }, "CORRECT")).toBe(true);
    expect(needsReason({ status: "OK", kind: "LINE" }, "REMOVE")).toBe(true);
    expect(needsReason({ status: "OK", kind: "MANUAL" }, "REMOVE")).toBe(false);
    expect(needsReason({ status: "LOW_CONFIDENCE", kind: "LINE" }, "CORRECT")).toBe(false);
  });

  it("converts confidence and ratios without floats", () => {
    expect(confidencePct("0.620")).toBe(62);
    expect(confidencePct("0.980")).toBe(98);
    expect(confidencePct("1.000")).toBe(100);
    expect(confidencePct("0.5")).toBe(50);
    expect(confidencePct(null)).toBeUndefined();
    expect(pctText("0.9512")).toBe("95.1%");
    expect(pctText("0.6667")).toBe("66.6%");
    expect(pctText("1.0000")).toBe("100.0%");
    expect(pctText(null)).toBe("—");
  });

  it("trims quantities for editing", () => {
    expect(trimQty("15.0000")).toBe("15");
    expect(trimQty("12.5000")).toBe("12.5");
    expect(trimQty("100")).toBe("100");
  });

  it("lists open lines", () => {
    const f = (seq: number, resolution: "NONE" | "CONFIRMED") => ({ seq, resolution }) as never;
    expect(openLines([f(1, "CONFIRMED"), f(2, "NONE"), f(3, "NONE")])).toEqual([2, 3]);
  });

  it("explains errors and warnings in plain words", () => {
    expect(ocrMessage(new ApiError(409, "FIELDS_UNRESOLVED", "x", { open_lines: [2, 4] }))).toContain("2, 4");
    expect(ocrMessage(new ApiError(422, "NEEDS_CORRECTION", "x"))).toMatch(/wasn't read clearly/);
    expect(ocrMessage(new Error("boom"))).toMatch(/Nothing was saved/);
    expect(warningText("NO_TEXT")).toMatch(/by hand/);
    expect(warningText("ODD")).toBe("ODD");
    expect(warningText("PACKET_INVOICE_MISMATCH")).toMatch(/CFIA Confirmation of Sale/);
  });
});
