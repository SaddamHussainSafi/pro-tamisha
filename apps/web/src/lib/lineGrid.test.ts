import { describe, expect, it } from "vitest";
import type { UnitProfile } from "../api";
import {
  applyPaste,
  gridUnits,
  blankLine,
  billable,
  cleanQty,
  ensureRows,
  gridProblem,
  isBlock,
  keyMove,
  move,
  parseClipboard,
  pickProduct,
  resolveProduct,
  previewRows,
  rowProblem,
  searchProducts,
  weightOf,
  type GridLine,
  type GridProduct,
  type NavCol,
  type PasteField,
  untouched,
  withPrefill,
} from "./lineGrid";

// Invoice grid columns: S/N, Item, Qty, Unit, Batch, SO Link, LB, Rate, Gross
const COLS: NavCol[] = [
  { editable: false },
  { editable: true, enter: true },
  { editable: true, enter: true },
  { editable: true },
  { editable: true },
  { editable: false },
  { editable: false },
  { editable: false },
  { editable: false },
];
const FIELDS: PasteField[] = [null, "item", "qty", "unit", null, null, null, null, null];

const P: GridProduct[] = [
  { id: "okr", sku: "OKR", name: "Indian Okra", aliases: ["bhindi", "okra indian"], units: ["BOX", "LB"], stock_unit: "BOX" },
  { id: "pap", sku: "PAP", name: "Papaya", aliases: [], units: ["BOX", "PIECE", "LB"], stock_unit: "BOX" },
  { id: "ccu", sku: "CCU", name: "Callaloo Jamaican - Packaged", aliases: ["callaloo"], units: ["BOX", "LB"], stock_unit: "BOX" },
  { id: "cas", sku: "CAS", name: "Cassava", aliases: ["yuca"], units: ["BOX", "LB"], stock_unit: "BOX" },
  { id: "cfz", sku: "CFZ", name: "Cassava frozen", aliases: [], units: ["BOX", "LB"], stock_unit: "BOX" },
];

const line = (p: Partial<GridLine> = {}): GridLine => ({ ...blankLine(), ...p });
const rows = (n: number) => Array.from({ length: n }, () => blankLine());

describe("grid rows", () => {
  it("always shows 20 rows and keeps 5 spare after the last filled one", () => {
    expect(ensureRows([], 100)).toHaveLength(20);
    const filled = ensureRows(rows(20), 100).map((l, i) => (i === 18 ? { ...l, qty: "2" } : l));
    expect(ensureRows(filled, 100)).toHaveLength(24);
    expect(ensureRows(filled, 22)).toHaveLength(22); // capped at the document's line limit
  });
});

describe("keyboard", () => {
  it("Enter goes Item → Qty → next row's Item", () => {
    expect(move({ row: 0, col: 1 }, "enter", COLS, 20)).toEqual({ row: 0, col: 2 });
    expect(move({ row: 0, col: 2 }, "enter", COLS, 20)).toEqual({ row: 1, col: 1 });
    expect(move({ row: 0, col: 4 }, "enter", COLS, 20)).toEqual({ row: 1, col: 1 }); // from Batch
    expect(move({ row: 19, col: 2 }, "enter", COLS, 20)).toEqual({ row: 19, col: 1 }); // last row stays
  });

  it("Tab walks every editable cell and wraps rows; Shift+Tab goes back", () => {
    expect(move({ row: 0, col: 2 }, "tab", COLS, 20)).toEqual({ row: 0, col: 3 });
    expect(move({ row: 0, col: 4 }, "tab", COLS, 20)).toEqual({ row: 1, col: 1 });
    expect(move({ row: 1, col: 1 }, "shiftTab", COLS, 20)).toEqual({ row: 0, col: 4 });
    expect(move({ row: 0, col: 1 }, "shiftTab", COLS, 20)).toEqual({ row: 0, col: 1 });
  });

  it("arrows move one cell, skip read-only columns and stop at the edges", () => {
    expect(move({ row: 3, col: 2 }, "up", COLS, 20)).toEqual({ row: 2, col: 2 });
    expect(move({ row: 0, col: 2 }, "up", COLS, 20)).toEqual({ row: 0, col: 2 });
    expect(move({ row: 19, col: 2 }, "down", COLS, 20)).toEqual({ row: 19, col: 2 });
    expect(move({ row: 3, col: 1 }, "left", COLS, 20)).toEqual({ row: 3, col: 1 });
    expect(move({ row: 3, col: 4 }, "right", COLS, 20)).toEqual({ row: 3, col: 4 });
    expect(move({ row: 3, col: 3 }, "right", COLS, 20)).toEqual({ row: 3, col: 4 });
  });

  it("left/right leave a text cell only from its ends; modifiers are left alone", () => {
    const k = (key: string, more = {}) => ({ key, shiftKey: false, altKey: false, ctrlKey: false, metaKey: false, ...more });
    expect(keyMove(k("ArrowLeft"), { kind: "text", caret: 2, caretEnd: 2, length: 4 })).toBeNull();
    expect(keyMove(k("ArrowLeft"), { kind: "text", caret: 0, caretEnd: 0, length: 4 })).toBe("left");
    expect(keyMove(k("ArrowRight"), { kind: "text", caret: 4, caretEnd: 4, length: 4 })).toBe("right");
    expect(keyMove(k("ArrowRight"), { kind: "text", caret: 0, caretEnd: 4, length: 4 })).toBeNull(); // a selection
    expect(keyMove(k("ArrowDown"), { kind: "select" })).toBe("down");
    expect(keyMove(k("ArrowDown", { altKey: true }), { kind: "select" })).toBeNull(); // opens the list
    expect(keyMove(k("Enter"), { kind: "text" })).toBe("enter");
    expect(keyMove(k("Enter", { shiftKey: true }), { kind: "text" })).toBeNull();
    expect(keyMove(k("a"), { kind: "text" })).toBeNull();
  });
});

describe("product cell", () => {
  it("matches by name, SKU or alias, best first", () => {
    expect(searchProducts(P, "okr").map((p) => p.sku)[0]).toBe("OKR");
    expect(searchProducts(P, "bhin").map((p) => p.sku)).toEqual(["OKR"]);
    expect(searchProducts(P, "cass").map((p) => p.sku)).toEqual(["CAS", "CFZ"]);
    expect(searchProducts(P, "cfz").map((p) => p.sku)).toEqual(["CFZ"]);
    expect(searchProducts(P, "jam call").map((p) => p.sku)).toEqual(["CCU"]); // every word, any order
    expect(searchProducts(P, "")).toEqual([]);
  });

  it("resolves typed or pasted text to one product, or none when unclear", () => {
    expect(resolveProduct(P, "Papaya")?.id).toBe("pap");
    expect(resolveProduct(P, "pap")?.id).toBe("pap");
    expect(resolveProduct(P, "yuca")?.id).toBe("cas");
    expect(resolveProduct(P, "Cassava")?.id).toBe("cas"); // exact name beats the longer one
    expect(resolveProduct(P, "cass")).toBeNull(); // two candidates
    expect(resolveProduct(P, "mango")).toBeNull();
  });

  it("picking a product shows its name and fills its default unit", () => {
    const l = pickProduct(line({ text: "okr", lot_id: "x", prefilled: true }), P[0] as GridProduct);
    expect([l.product_id, l.text, l.unit_code, l.lot_id, l.prefilled]).toEqual(["okr", "Indian Okra", "BOX", "", false]);
    expect(pickProduct(line({ unit_code: "PIECE" }), P[1] as GridProduct).unit_code).toBe("PIECE"); // kept: papaya sells by piece
  });
});

describe("paste from Excel", () => {
  it("parses tabs, lines and quoted cells", () => {
    expect(parseClipboard("Okra\t8\r\nPapaya\t12\r\n")).toEqual([["Okra", "8"], ["Papaya", "12"]]);
    expect(parseClipboard('"Callaloo, ""fresh"""\t1\n')).toEqual([['Callaloo, "fresh"', "1"]]);
    expect(isBlock("Okra\t8")).toBe(true);
    expect(isBlock("Okra\n")).toBe(false);
    expect(cleanQty("1,234.50")).toBe("1234.50");
  });

  it("fills Item, Qty and Unit from the cell the block lands on, adding rows as needed", () => {
    const start = ensureRows([], 100);
    const block = parseClipboard(Array.from({ length: 22 }, (_, i) => `${i % 2 ? "Papaya" : "OKR"}\t${i + 1}`).join("\n"));
    const { lines, unmatched } = applyPaste(start, { row: 0, col: 1 }, block, FIELDS, P, 100);
    expect(unmatched).toEqual([]);
    expect(billable(lines)).toHaveLength(22);
    expect(lines.length).toBe(27); // 22 + 5 spare
    expect([lines[0]?.product_id, lines[0]?.qty, lines[1]?.text, lines[21]?.qty]).toEqual(["okr", "1", "Papaya", "22"]);
  });

  it("skips a header row, reads units and '4 box' cells, and keeps unknown names for fixing", () => {
    const block = parseClipboard("Item\tQty\tUnit\nPapaya\t3\tpcs\nyuca\t4 box\t\nMango\t2\t");
    const { lines, unmatched } = applyPaste(ensureRows([], 100), { row: 2, col: 1 }, block, FIELDS, P, 100);
    expect([lines[2]?.product_id, lines[2]?.unit_code, lines[2]?.qty]).toEqual(["pap", "PIECE", "3"]);
    expect([lines[3]?.product_id, lines[3]?.unit_code, lines[3]?.qty]).toEqual(["cas", "BOX", "4"]);
    expect([lines[4]?.product_id, lines[4]?.text]).toEqual(["", "Mango"]);
    expect(unmatched).toEqual([4]);
    expect(rowProblem(lines[4] as GridLine, 4, lines)).toMatch(/No product matches/);
  });

  it("a block pasted on the Qty column fills quantities only; S/N and figures are skipped", () => {
    const base = applyPaste(ensureRows([], 100), { row: 0, col: 1 }, [["OKR"], ["PAP"]], FIELDS, P, 100).lines;
    const { lines } = applyPaste(base, { row: 0, col: 2 }, [["5"], ["6"]], FIELDS, P, 100);
    expect(lines.slice(0, 2).map((l) => [l.product_id, l.qty])).toEqual([["okr", "5"], ["pap", "6"]]);
    // A full Focus-style copy (S/N .. Gross) pasted on S/N: only Item/Qty land
    const focus = parseClipboard("1\tIndian Okra\t8.00\tZOL15393\t080710\t112.00\t36.00\t288.00");
    const r = applyPaste(ensureRows([], 100), { row: 0, col: 0 }, focus, FIELDS, P, 100).lines[0];
    expect([r?.product_id, r?.qty, r?.unit_code]).toEqual(["okr", "8.00", "BOX"]);
  });
});

describe("checks before sending", () => {
  it("flags rows without a product, bad quantities and repeats; prefilled rows without qty are skipped", () => {
    const ls = [
      line({ product_id: "okr", text: "Indian Okra", unit_code: "BOX", qty: "8" }),
      line({ product_id: "pap", text: "Papaya", unit_code: "BOX", qty: "", prefilled: true }),
      line({ product_id: "okr", text: "Indian Okra", unit_code: "BOX", qty: "2" }),
      line({ qty: "3" }),
      line({ product_id: "pap", text: "Papaya", unit_code: "BOX", qty: "1.5" }),
    ];
    expect(rowProblem(ls[1] as GridLine, 1, ls)).toBeNull();
    expect(rowProblem(ls[2] as GridLine, 2, ls)).toMatch(/Already on line 1/);
    expect(rowProblem(ls[3] as GridLine, 3, ls)).toMatch(/Choose the product/);
    expect(rowProblem(ls[4] as GridLine, 4, ls)).toMatch(/whole/);
    expect(gridProblem(ls)).toMatch(/^Line 3:/);
    expect(gridProblem(ensureRows([], 100))).toMatch(/at least one product/);
    expect(billable(ls).map((l) => l.qty)).toEqual(["8", "2", "1.5"]);
  });
});

describe("last-invoice prefill", () => {
  it("fills an untouched grid only", () => {
    const pre = [line({ product_id: "okr", text: "Indian Okra", unit_code: "BOX", prefilled: true })];
    const empty = ensureRows([], 100);
    expect(untouched(empty)).toBe(true);
    const filled = withPrefill(empty, pre, 100);
    expect([filled.length, filled[0]?.product_id, untouched(filled)]).toEqual([20, "okr", true]);
    expect(filled[1]?.key).toBe(empty[0]?.key); // empty rows keep their keys (the cursor stays put)
    expect(withPrefill(empty, [], 100)).toBe(empty); // nothing to prefill: untouched grid as it is
    expect(withPrefill(filled, [], 100).some((l) => l.prefilled)).toBe(false); // another customer: prefill gone
    const typed = filled.map((l, i) => (i === 0 ? { ...l, qty: "3", prefilled: false } : l));
    expect(withPrefill(typed, [], 100)).toBe(typed); // someone typed: kept as it is
  });
});

describe("weight (boxes are sold per lb)", () => {
  it("reads pounds, refuses anything else, and only boxes carry one", () => {
    expect(weightOf(line({ weight: " 35 " }))).toBe("35");
    expect(weightOf(line({ weight: "41.5 lb" }))).toBe("41.5");
    expect(weightOf(line({ weight: "" }))).toBeNull();
    for (const bad of ["0", "-3", "3/4", "abc", "35 kg"]) expect(weightOf(line({ weight: bad }))).toBe("bad");
    const box = line({ product_id: "okr", text: "Okra", unit_code: "BOX", qty: "1", weight: "35" });
    expect(rowProblem(box, 0, [box])).toBeNull();
    expect(rowProblem({ ...box, weight: "lots" }, 0, [box])).toMatch(/pounds/);
    expect(rowProblem({ ...box, unit_code: "LB" }, 0, [box])).toMatch(/only for boxes/);
    const rows = previewRows([box, { ...box, key: 99, weight: "x" }, line({ product_id: "lb", unit_code: "LB", qty: "2", weight: "2" })]);
    expect(rows.lines.map((l) => l.weight_lb)).toEqual(["35", undefined, undefined]); // bad or not a box: not sent
  });
});

describe("grid units (D-38: box, lb and kg; kg for an lb product is invoiced in lb)", () => {
  const prof = (code: string, unit: UnitProfile["unit_code"], d = false): UnitProfile => ({ id: code, code, unit_code: unit, pieces_per_unit: null, weight_lb_per_unit: null, is_default: d, active: true });
  it("adds lb and kg after the product's own units", () => {
    expect(gridUnits({ stock_unit: "BOX", unit_profiles: [prof("CASE40", "BOX", true)] })).toEqual(["BOX", "LB", "KG"]);
    expect(gridUnits({ stock_unit: "LB", unit_profiles: [] })).toEqual(["LB", "KG"]);
    expect(gridUnits({ stock_unit: "KG", unit_profiles: [prof("PC", "PIECE")] })).toEqual(["PIECE", "KG", "LB"]);
  });
});
