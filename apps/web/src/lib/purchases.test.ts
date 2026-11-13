import { describe, expect, it } from "vitest";
import { ApiError, type PurchaseCorrection, type Shipment } from "../api";
import { badges, correctionText, costProblem, goodsText, gridTotal, lineAmount, matchingQuick, purchaseMessage, qtyWords, shortfall, STAGE, stockNow, VIEW_STAGE } from "./purchases";

const flags = (o: Partial<Shipment>) => ({ stage: "IN_STOCK", is_airport: false, needs_details: false, pending_corrections: 0, review_count: 0, matched_shipment_no: null, entry: "PURCHASE", ...o }) as Shipment;

describe("purchase status and badges", () => {
  it("says In stock from the moment it is saved, Verified once the pieces were checked", () => {
    expect(STAGE.IN_STOCK.label).toBe("In stock");
    expect(STAGE.TO_VERIFY.label).toBe("In stock");
    expect(STAGE.TO_COMPLETE.label).toBe("In stock");
    expect(STAGE.VERIFIED.label).toBe("Verified");
    expect(STAGE.NOT_IN_STOCK.label).toBe("Not in stock yet");
  });
  it("shows what still needs someone", () => {
    expect(badges(flags({}))).toEqual([]);
    expect(badges(flags({ stage: "TO_VERIFY", is_airport: true })).map((b) => b.label)).toEqual(["Airport · to verify"]);
    expect(badges(flags({ stage: "TO_COMPLETE", needs_details: true, entry: "QUICK" })).map((b) => b.label)).toEqual(["Details to complete"]);
    expect(badges(flags({ stage: "TO_APPROVE", is_airport: true, pending_corrections: 1 })).map((b) => b.label)).toEqual(["Airport · needs manager's OK"]);
    expect(badges(flags({ pending_corrections: 2, review_count: 1 })).map((b) => b.label)).toEqual(["2 corrections to approve", "To review"]);
    expect(badges(flags({ entry: "QUICK", matched_shipment_no: "SHP-1" })).map((b) => b.label)).toEqual(["Matched to SHP-1"]);
  });
  it("maps the list filters to server stages", () => {
    expect(VIEW_STAGE.all).toBeUndefined();
    expect(VIEW_STAGE.complete).toBe("TO_COMPLETE");
    expect(VIEW_STAGE.verify).toBe("TO_VERIFY,TO_APPROVE");
  });
});

describe("quantities and money (no floats)", () => {
  it("words quantities", () => {
    expect(qtyWords("1.0000", "BOX")).toBe("1 box");
    expect(qtyWords("5.0000", "BOX")).toBe("5 boxes");
    expect(qtyWords("12.5000", "LB")).toBe("12.5 lb");
    expect(goodsText([], 0)).toBe("No lines");
    expect(goodsText([{ product_id: "p", sku: "YAM", product_name: "Yellow yam", unit_code: "BOX", qty: "5.0000" }], 3)).toBe("5 boxes Yellow yam + 2 more");
  });
  it("multiplies qty by cost to the cent, half up", () => {
    expect(lineAmount("10", "20.00")).toBe("200.00");
    expect(lineAmount("3", "0.335")).toBe("1.01");
    expect(lineAmount("22.0462", "1.8144")).toBe("40.00");
    expect(lineAmount("5", "")).toBeNull();
    expect(lineAmount("x", "2")).toBeNull();
  });
  it("totals the grid and counts lines without a cost", () => {
    expect(gridTotal([{ product_id: "a", qty: "10", cost: "20" }, { product_id: "b", qty: "50", cost: "0.30" }, { product_id: "c", qty: "2", cost: "" }, { product_id: "", qty: "", cost: "" }])).toEqual({ total: "215.00", unpriced: 1 });
  });
  it("checks a cost cell", () => {
    expect(costProblem("")).toBeNull();
    expect(costProblem("12.3456")).toBeNull();
    expect(costProblem("12.34567")).toMatch(/4 decimals/);
    expect(costProblem("abc")).toMatch(/number/);
  });
  it("knows how many are missing for the work at hand", () => {
    expect(shortfall("5.0000", "0.0000")).toBe("5.0000");
    expect(shortfall("5", "3")).toBe("2.0000");
    expect(shortfall("5", "5")).toBeNull();
    expect(shortfall("5", "9")).toBeNull();
  });
});

describe("quick receives and corrections", () => {
  const q = (id: string, product_id: string, unit_code = "BOX") => flags({ id, needs_details: true, items: [{ product_id, sku: "X", product_name: "X", unit_code: unit_code as "BOX", qty: "5.0000" }] });
  it("finds quick receives that the invoice lines cover", () => {
    const open = [q("1", "cas"), q("2", "pln", "PIECE"), q("3", "yam")];
    const lines = [{ product_id: "cas", unit_code: "BOX" }, { product_id: "pln", unit_code: "BOX" }];
    expect(matchingQuick(open, lines).map((x) => x.id)).toEqual(["1"]);
    expect(matchingQuick(open, lines, "1")).toEqual([]);
  });
  const c = (o: Partial<PurchaseCorrection>) => ({ kind: "SHORT", qty: "2.0000", unit_code: "BOX", product_name: "Cassava", status: "POSTED", posted_qty: "2.0000", uncovered_qty: "0.0000", ...o }) as PurchaseCorrection;
  it("describes a correction in one plain sentence", () => {
    expect(correctionText(c({}))).toBe("2 boxes Cassava short — taken from stock.");
    expect(correctionText(c({ status: "PENDING", posted_qty: null }))).toMatch(/waiting for a manager's OK. Stock is unchanged/);
    expect(correctionText(c({ status: "REJECTED", posted_qty: null }))).toMatch(/refused/);
    expect(correctionText(c({ posted_qty: "1.0000", uncovered_qty: "1.0000" }))).toBe("2 boxes Cassava short. 1 box taken from stock; 1 box could not be taken because it was already invoiced or reserved.");
    expect(correctionText(c({ kind: "OVER", qty: "1.0000", posted_qty: "1.0000" }))).toBe("1 box Cassava more than the invoice — added to stock.");
    expect(correctionText(c({ kind: "DAMAGED" }))).toMatch(/moved out of saleable stock/);
  });
  it("shows what a line holds after posted corrections", () => {
    const detail = {
      counted: [{ id: "l1", accepted_qty: "10.0000" }, { id: "l2", accepted_qty: "4.0000" }],
      corrections: [c({ grn_line_id: "l1" }), c({ grn_line_id: "l1", kind: "DAMAGED", posted_qty: "1.0000" }), c({ grn_line_id: "l2", kind: "OVER", posted_qty: "1.0000" }), c({ grn_line_id: "l2", status: "PENDING", posted_qty: null })],
    } as Parameters<typeof stockNow>[0];
    expect(Object.fromEntries(stockNow(detail))).toEqual({ l1: "8.0000", l2: "5.0000" });
  });
  it("turns server refusals into plain words", () => {
    const err = (code: string, message = "", extra: Record<string, unknown> = {}) => new ApiError(409, code, message, extra);
    expect(purchaseMessage(err("INVOICE_ALREADY_ENTERED", "Invoice 7 from X was already entered as SHP-1."))).toMatch(/already entered as SHP-1/);
    expect(purchaseMessage(err("MATCH_REASON_REQUIRED", "", { differences: [{ product_name: "Cassava", unit: "BOX", invoice_qty: "6.0000", received_qty: "5.0000" }] }))).toBe("The invoice and what was already added to stock differ (Cassava: invoice 6 boxes, added 5 boxes). Say why below.");
    expect(purchaseMessage(err("SUPPLIER_NEEDED"))).toMatch(/Complete the details/);
    expect(purchaseMessage(err("FORBIDDEN"))).toMatch(/permission/);
  });
});
