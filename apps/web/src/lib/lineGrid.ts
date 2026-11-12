import type { Product, UnitCode } from "../api";
import { qtyProblem, unitsFor } from "./directInvoice";

// Fast entry grid (Create invoice › Direct, New sales order): the row model, keyboard moves,
// product matching and Excel paste. Pure functions so they are unit-tested; the server still
// prices, checks stock and totals every line (see POST /sales-orders/preview).

/** One grid row. `text` is what is typed in the Item cell; `product_id` is set once it matches.
 * `prefilled` rows come from the customer's last invoice and are only billed once given a qty. */
export type GridLine = {
  key: number;
  text: string;
  product_id: string;
  unit_code: UnitCode | "";
  qty: string;
  lot_id: string;
  note: string;
  prefilled?: boolean;
  /** Pounds the whole line weighs (boxes; e.g. the WEIGHT on the order sheet): priced per lb. */
  weight?: string;
};

export const MIN_ROWS = 20;
export const SPARE_ROWS = 5;

let seq = 1;
export const nextKey = () => seq++;
export const blankLine = (key = nextKey()): GridLine => ({ key, text: "", product_id: "", unit_code: "", qty: "", lot_id: "", note: "" });

/** Anything typed or picked on the row. */
export const isFilled = (l: GridLine) => !!(l.product_id || l.text.trim() || l.qty.trim() || l.note.trim());

/** Rows always open: at least `min`, and `spare` empty rows after the last filled one, up to `max`. */
export function ensureRows(lines: GridLine[], max: number, min = MIN_ROWS, spare = SPARE_ROWS): GridLine[] {
  let last = -1;
  lines.forEach((l, i) => {
    if (isFilled(l)) last = i;
  });
  const want = Math.min(max, Math.max(min, last + 1 + spare));
  if (lines.length >= want) return lines;
  return [...lines, ...Array.from({ length: want - lines.length }, () => blankLine())];
}

/* ------------------------------------------------------------------ keyboard */

export type Pos = { row: number; col: number };
/** A grid column as navigation sees it: focusable cells, and whether Enter stops there. */
export type NavCol = { editable: boolean; enter?: boolean };
export type Move = "up" | "down" | "left" | "right" | "tab" | "shiftTab" | "enter";

/** Where a key takes the cursor. Enter follows the entry path (Item → Qty → next row's Item);
 * Tab walks every editable cell and wraps to the next row; arrows move one cell, clamped. */
export function move(pos: Pos, m: Move, cols: NavCol[], rows: number): Pos {
  const editable = cols.flatMap((c, i) => (c.editable ? [i] : []));
  const path = cols.flatMap((c, i) => (c.editable && c.enter ? [i] : []));
  if (!editable.length) return pos;
  const clampRow = (r: number) => Math.max(0, Math.min(rows - 1, r));
  switch (m) {
    case "up":
      return { row: clampRow(pos.row - 1), col: pos.col };
    case "down":
      return { row: clampRow(pos.row + 1), col: pos.col };
    case "left": {
      const prev = editable.filter((c) => c < pos.col);
      return prev.length ? { row: pos.row, col: prev[prev.length - 1] as number } : pos;
    }
    case "right": {
      const next = editable.find((c) => c > pos.col);
      return next === undefined ? pos : { row: pos.row, col: next };
    }
    case "tab": {
      const next = editable.find((c) => c > pos.col);
      if (next !== undefined) return { row: pos.row, col: next };
      return pos.row + 1 < rows ? { row: pos.row + 1, col: editable[0] as number } : pos;
    }
    case "shiftTab": {
      const prev = editable.filter((c) => c < pos.col);
      if (prev.length) return { row: pos.row, col: prev[prev.length - 1] as number };
      return pos.row > 0 ? { row: pos.row - 1, col: editable[editable.length - 1] as number } : pos;
    }
    case "enter": {
      const steps = path.length ? path : editable;
      const next = steps.find((c) => c > pos.col);
      if (next !== undefined) return { row: pos.row, col: next };
      return { row: Math.min(rows - 1, pos.row + 1), col: steps[0] as number };
    }
  }
}

/** The move a key press asks for, or null to let the cell handle it (typing, caret moves).
 * Left/Right leave a text cell only from its first/last character. */
export function keyMove(
  e: { key: string; shiftKey: boolean; altKey: boolean; ctrlKey: boolean; metaKey: boolean },
  cell: { kind: "text" | "select"; caret?: number | null; caretEnd?: number | null; length?: number },
): Move | null {
  if (e.ctrlKey || e.metaKey || e.altKey) return null;
  switch (e.key) {
    case "Enter":
      return e.shiftKey ? null : "enter";
    case "ArrowUp":
      return "up";
    case "ArrowDown":
      return "down";
    case "ArrowLeft":
      if (cell.kind === "select") return "left";
      return cell.caret === 0 && cell.caretEnd === 0 ? "left" : null;
    case "ArrowRight":
      if (cell.kind === "select") return "right";
      return cell.caret === (cell.length ?? 0) && cell.caretEnd === (cell.length ?? 0) ? "right" : null;
    default:
      return null;
  }
}

/* ------------------------------------------------------------------ products */

/** What the grid needs about a product: names to match and units to offer. */
export type GridProduct = { id: string; sku: string; name: string; aliases: string[]; units: UnitCode[]; stock_unit: UnitCode };

/** Units the server accepts for a product: its case sizes' units, its stock unit, LB (catch
 * weight is always allowed) and KG — kg for a product kept in lb is saved and invoiced in lb
 * (D-38, owner 2026-11-12). The product's default case size comes first. */
export function gridUnits(p: Pick<Product, "stock_unit" | "unit_profiles">): UnitCode[] {
  const u = unitsFor(p);
  return [...u, ...(["LB", "KG"] as UnitCode[]).filter((x) => !u.includes(x))];
}

export function toGridProduct(p: Product): GridProduct {
  return { id: p.id, sku: p.sku, name: p.name, aliases: p.aliases.map((a) => a.alias), units: gridUnits(p), stock_unit: p.stock_unit };
}

export const norm = (s: string) =>
  s
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim();

/** Best matches first: exact SKU, exact name/alias, SKU prefix, name/alias prefix, a word
 * starting with each typed word, then plain contains. Every typed word must match. */
export function searchProducts(products: GridProduct[], q: string, limit = 8): GridProduct[] {
  const t = norm(q);
  if (!t) return [];
  const words = t.split(" ");
  const scored: { p: GridProduct; s: number }[] = [];
  for (const p of products) {
    const sku = norm(p.sku);
    const names = [norm(p.name), ...p.aliases.map(norm)];
    const hay = [sku, ...names].join(" ");
    if (!words.every((w) => hay.includes(w))) continue;
    let s: number;
    if (sku === t) s = 0;
    else if (names.includes(t)) s = 1;
    else if (sku.startsWith(t)) s = 2;
    else if (names.some((n) => n.startsWith(t))) s = 3;
    else if (words.every((w) => hay.split(" ").some((x) => x.startsWith(w)))) s = 4;
    else s = 5;
    scored.push({ p, s });
  }
  scored.sort((a, b) => a.s - b.s || a.p.name.localeCompare(b.p.name));
  return scored.slice(0, limit).map((x) => x.p);
}

/** The one product `text` means: exact SKU, name or alias, else the only search match. */
export function resolveProduct(products: GridProduct[], text: string): GridProduct | null {
  const t = norm(text);
  if (!t) return null;
  const exact = products.filter((p) => norm(p.sku) === t || norm(p.name) === t || p.aliases.some((a) => norm(a) === t));
  if (exact.length === 1) return exact[0] as GridProduct;
  if (exact.length > 1) return exact.find((p) => norm(p.sku) === t) ?? null;
  const hits = searchProducts(products, text, 2);
  return hits.length === 1 ? (hits[0] as GridProduct) : null;
}

/** The row after picking a product: its name shown, the unit kept if the product sells in it,
 * else its default unit; a batch chosen for another product is dropped. */
export function pickProduct(l: GridLine, p: GridProduct): GridLine {
  const unit = l.unit_code && p.units.includes(l.unit_code) ? l.unit_code : (p.units[0] ?? "");
  return { ...l, product_id: p.id, text: p.name, unit_code: unit, lot_id: l.product_id === p.id ? l.lot_id : "", prefilled: false };
}

/* ------------------------------------------------------------------ paste */

/** Rows and cells of a clipboard copy from Excel/Sheets: tab-separated, one row per line,
 * cells with tabs/newlines/quotes wrapped in double quotes ("" = a quote). */
export function parseClipboard(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  let i = 0;
  const s = text.replace(/\r\n?/g, "\n");
  while (i < s.length) {
    const ch = s[i] as string;
    if (quoted) {
      if (ch === '"' && s[i + 1] === '"') {
        cell += '"';
        i += 2;
        continue;
      }
      if (ch === '"') quoted = false;
      else cell += ch;
      i++;
      continue;
    }
    if (ch === '"' && cell === "") quoted = true;
    else if (ch === "\t") {
      row.push(cell);
      cell = "";
    } else if (ch === "\n") {
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else cell += ch;
    i++;
  }
  if (cell !== "" || row.length) {
    row.push(cell);
    rows.push(row);
  }
  return rows.map((r) => r.map((c) => c.trim()));
}

/** More than one cell: a block to spread over the grid, not text for one cell. */
export const isBlock = (text: string) => /[\t\n]/.test(text.replace(/\r?\n$/, ""));

/** What each column takes from a pasted block; null = a column paste skips (S/N, figures). */
export type PasteField = "item" | "qty" | "unit" | "note" | null;

const HEADER = /^(s\/?n|#|no\.?|item|items|product|products|description|sku|code|qty|quantity|unit|uom|batch.*|lot.*|so.*|lb|lbs|rate|price|gross|amount|as written|note)$/i;

const UNIT_WORDS: Record<string, UnitCode> = {
  box: "BOX", boxes: "BOX", bx: "BOX", cs: "BOX", case: "BOX", cases: "BOX", ctn: "BOX",
  lb: "LB", lbs: "LB", pound: "LB", pounds: "LB",
  kg: "KG", kgs: "KG", kilo: "KG", kilos: "KG",
  pc: "PIECE", pcs: "PIECE", piece: "PIECE", pieces: "PIECE", ea: "PIECE", each: "PIECE",
};
export const unitFromText = (s: string): UnitCode | null => UNIT_WORDS[norm(s).replace(/ /g, "")] ?? (["BOX", "LB", "KG", "PIECE"].includes(s.toUpperCase()) ? (s.toUpperCase() as UnitCode) : null);

/** "1,234.50" / " 12 " → "1234.50"; anything else is kept as typed so the row shows a problem. */
export const cleanQty = (s: string) => {
  const t = s.replace(/[,\s]/g, "");
  return /^\d+(\.\d+)?$/.test(t) ? t : s.trim();
};

/** Spread a pasted block over the grid from `at`: pasted columns land on grid columns left to
 * right (a block pasted on the Item column fills Item, then Qty, ...); a header row is skipped.
 * Returns the new rows and the rows whose item text matched no product. */
export function applyPaste(
  lines: GridLine[],
  at: Pos,
  cells: string[][],
  fields: PasteField[],
  products: GridProduct[],
  max: number,
): { lines: GridLine[]; unmatched: number[] } {
  const block = cells.length && cells[0]?.every((c) => !c || HEADER.test(c)) ? cells.slice(1) : cells;
  const out = [...lines];
  const unmatched: number[] = [];
  block.forEach((vals, r) => {
    const row = at.row + r;
    if (row >= max) return;
    while (out.length <= row) out.push(blankLine());
    let l = { ...(out[row] as GridLine) };
    let unit: UnitCode | null = null;
    vals.forEach((v, c) => {
      const f = fields[at.col + c];
      if (!f) return;
      if (f === "item") {
        if (!v) return;
        const p = resolveProduct(products, v);
        l = p ? pickProduct(l, p) : { ...l, text: v, product_id: "", unit_code: "", lot_id: "", prefilled: false };
        if (!p) unmatched.push(row);
      } else if (f === "qty") {
        // "4 box" in one cell: quantity and unit
        const m = /^\s*([\d.,]+)\s*([A-Za-z]+)\s*$/.exec(v);
        if (m && unitFromText(m[2] as string)) {
          l.qty = cleanQty(m[1] as string);
          unit = unitFromText(m[2] as string);
        } else l.qty = cleanQty(v);
        l.prefilled = false;
      } else if (f === "unit") unit = unitFromText(v) ?? unit;
      else if (f === "note") l.note = v;
    });
    if (unit && l.product_id) {
      const p = products.find((x) => x.id === l.product_id);
      if (p?.units.includes(unit)) l.unit_code = unit;
    }
    out[row] = l;
  });
  return { lines: ensureRows(out, max), unmatched };
}

/* ------------------------------------------------------------------ checks */

/** Rows that go on the document: a product, and a quantity unless it is an untouched prefill. */
export const billable = (lines: GridLine[]) => lines.filter((l) => l.product_id && !(l.prefilled && !l.qty.trim()));

/** Per-row problem shown on the grid before anything is sent (null = fine or empty). */
/** The weight typed on a row as pounds, or null when blank. "bad" when it isn't a weight. */
export function weightOf(l: GridLine): string | null | "bad" {
  const w = (l.weight ?? "").trim().replace(/\s*lbs?$/i, "");
  if (!w) return null;
  return /^\d{1,10}(\.\d{1,4})?$/.test(w) && Number(w) > 0 ? w : "bad";
}

export function rowProblem(l: GridLine, i: number, lines: GridLine[]): string | null {
  if (!isFilled(l) || (l.prefilled && !l.qty.trim())) return null;
  if (!l.product_id) return l.text.trim() ? `No product matches “${l.text.trim()}”.` : "Choose the product.";
  const q = qtyProblem(l.unit_code, l.qty);
  if (q) return q;
  const w = weightOf(l);
  if (w === "bad") return "Enter the weight in pounds, e.g. 35.";
  if (w && l.unit_code !== "BOX") return "A weight is only for boxes.";
  const first = lines.findIndex((x) => x.product_id === l.product_id && x.unit_code === l.unit_code && billable([x]).length);
  if (first !== -1 && first < i) return `Already on line ${first + 1}. Add the quantities together.`;
  return null;
}

/** First problem in the grid as "Line N: ...", or null when it can be sent. */
export function gridProblem(lines: GridLine[]): string | null {
  for (const [i, l] of lines.entries()) {
    const p = rowProblem(l, i, lines);
    if (p) return `Line ${i + 1}: ${p}`;
  }
  if (!billable(lines).length) return lines.some((l) => l.prefilled) ? "Enter a quantity for at least one product." : "Add at least one product.";
  return null;
}

/** Prefilled rows (the customer's last invoice) start the grid while nothing is typed by hand. */
export const untouched = (lines: GridLine[]) => lines.every((l) => !isFilled(l) || (l.prefilled && !l.qty.trim()));

/* ------------------------------------------------------------------ server preview */

/** Rows sent for the live line preview: every row with a product and unit (an untyped or
 * unreadable quantity goes as 0, so the row still gets its price and batches), with its key. */
export function previewRows(lines: GridLine[]): { keys: number[]; lines: { product_id: string; unit_code: UnitCode; qty: string; lot_id: string | null; weight_lb?: string }[] } {
  const rows = lines.filter((l) => l.product_id && l.unit_code);
  return {
    keys: rows.map((l) => l.key),
    lines: rows.map((l) => {
      const w = l.unit_code === "BOX" ? weightOf(l) : null;
      return {
        product_id: l.product_id,
        unit_code: l.unit_code as UnitCode,
        qty: /^\d{1,12}(\.\d{1,4})?$/.test(l.qty.trim()) ? l.qty.trim() : "0",
        lot_id: l.lot_id || null,
        ...(w && w !== "bad" ? { weight_lb: w } : {}),
      };
    }),
  };
}

/** The grid after the customer's last-invoice products arrive: those rows (no quantities)
 * replace the previous prefill at the top of an untouched grid. The empty rows keep their
 * keys (the cursor may be in one); a grid someone already typed into is left as it is. */
export function withPrefill(cur: GridLine[], pre: GridLine[], max: number): GridLine[] {
  if (!untouched(cur) || (!pre.length && !cur.some((l) => l.prefilled))) return cur;
  const out = [...pre, ...cur.filter((l) => !l.prefilled)].slice(0, max);
  const keep = Math.max(MIN_ROWS, pre.length + SPARE_ROWS);
  while (out.length > keep && !isFilled(out[out.length - 1] as GridLine)) out.pop();
  return ensureRows(out, max);
}
