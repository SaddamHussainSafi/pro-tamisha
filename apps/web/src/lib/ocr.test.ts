import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { canConfirm, confidencePct, engineText, lineNotes, needsReason, ocrMessage, openLines, pctText, selectable, trimQty, warningText } from "./ocr";

describe("ocr helpers", () => {
  it("only clear readings can be confirmed", () => {
    expect(canConfirm({ status: "OK", read_product_id: "p", read_qty: "2.0000" })).toBe(true);
    expect(canConfirm({ status: "LOW_CONFIDENCE", read_product_id: "p", read_qty: "2.0000" })).toBe(false);
    expect(canConfirm({ status: "UNMAPPED", read_product_id: null, read_qty: "2.0000" })).toBe(false);
    expect(canConfirm({ status: "OK", read_product_id: "p", read_qty: null })).toBe(false);
    // only clear lines still waiting can be ticked for Confirm selected
    expect(selectable({ status: "OK", read_product_id: "p", read_qty: "2.0000", resolution: "NONE" })).toBe(true);
    expect(selectable({ status: "OK", read_product_id: "p", read_qty: "2.0000", resolution: "CONFIRMED" })).toBe(false);
    expect(selectable({ status: "LOW_CONFIDENCE", read_product_id: "p", read_qty: "2.0000", resolution: "NONE" })).toBe(false);
  });

  it("asks for a reason only when overriding a clear reading", () => {
    expect(needsReason({ status: "OK", kind: "LINE" }, "CORRECT")).toBe(false); // owner, 2026-11-11
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
    expect(ocrMessage(new Error("boom"))).toMatch(/check whether your last change was saved/);
    expect(warningText("NO_TEXT")).toMatch(/by hand/);
    expect(warningText("ODD")).toBe("ODD");
    expect(warningText("PACKET_INVOICE_MISMATCH")).toMatch(/CFIA Confirmation of Sale/);
  });
});

describe("Gemini readings in words", () => {
  it("explains a fallback, an unsure customer and a file with two orders", () => {
    expect(warningText("GEMINI_FALLBACK:GEMINI_DAILY_CAP")).toMatch(/online reader wasn't used \(today's free allowance is used up\).*built-in reader/);
    expect(warningText("LINE_COUNT_DIFFERS:local=1,gemini=2")).toBe("2 lines were read, but a second check counted 1. Check against the scan that no line is missing or extra.");
    expect(warningText("GEMINI_FALLBACK:GEMINI_NOT_CONFIGURED") + warningText("HEADER_DIFFERS:document_no")).not.toMatch(/gemini/i);
    expect(warningText("GEMINI_FALLBACK:GEMINI_UNAVAILABLE:p2,3")).toMatch(/for page 2,3 \(Google could not be reached\)/);
    expect(warningText("CUSTOMER_UNSURE:Sarangan Miss")).toMatch(/“Sarangan Miss”, but not clearly/);
    expect(warningText("MIXED_ORDERS:p1=080778;p2=080781")).toMatch(/more than one order.*p1: order 080778; p2: order 080781/);
    expect(warningText("TOTAL_DIFFERS:written=46/43,lines=22,sum=47:p1")).toMatch(/margin of page 1: 46, 43\. That doesn't match the 22 lines read \(they add up to 47\)/);
    expect(warningText("CUSTOMER_IS_SUPPLIER")).toMatch(/we are the seller/);
    expect(warningText("DOC_TYPE_MISMATCH:AIRWAY_BILL")).toMatch(/looks like an airway bill/);
  });
  it("lists what was seen on a line apart from the quantity", () => {
    expect(lineNotes(null)).toEqual([]);
    expect(lineNotes({ crossed_out: "8", no_mark: true, pkg: "cs", notes: "small", checkmark: true, supplied: null, flags: ["RECHECK_DIFFERS:3", "NO_INK_SEEN", "QTY_UNREADABLE"] })).toEqual([
      "Crossed out: 8",
      "“NO” is written on this row",
      "Package: cs",
      "Notes: small",
      "Ticked",
      "A second look read 3.",
      "No writing was found in this row's box: the number may belong to another row.",
    ]);
  });
  it("says how long the reading took, never which reader or model", () => {
    expect(engineText(undefined)).toBe("");
    expect(engineText({ provider: "gemini", model: "gemini-3.8-flash", seconds: 13.6 })).toBe("Read in 14 s.");
    expect(engineText({ provider: "tesseract", model: "gemini-3.8-flash", seconds: 6 })).toBe("Read in 6 s.");
  });
});
