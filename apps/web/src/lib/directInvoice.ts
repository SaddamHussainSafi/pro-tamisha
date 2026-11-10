import type { Invoice, Product, UnitCode } from "../api";
import { dec, isDecimal } from "./decimal";

// Direct invoice (no picking sheet) form helpers. The server prices, checks stock and issues.

/** `prefilled` lines come from the customer's last invoice: left without a quantity, they are
 * simply not billed. Lines the user added must have a quantity. */
export type DraftLine = { key: number; product_id: string; unit_code: UnitCode | ""; qty: string; prefilled?: boolean };

/** Lines that go on the invoice. */
export function billable(lines: DraftLine[]): DraftLine[] {
  return lines.filter((l) => l.product_id && !(l.prefilled && !l.qty.trim()));
}

/** Nothing typed or picked by hand yet (only empty lines, or prefilled ones without a quantity). */
export function untouched(lines: DraftLine[]): boolean {
  return lines.every((l) => !l.product_id || (l.prefilled && !l.qty.trim()));
}

/** The list after a prefill for another customer arrives: the new prefilled lines, then the
 * user's own (blank) lines with their keys kept, so a pick made meanwhile is never lost. A list
 * someone already worked on is left as it is. */
export function withPrefill(cur: DraftLine[], pre: DraftLine[], blank: () => DraftLine): DraftLine[] {
  if (!untouched(cur)) return cur;
  const own = cur.filter((l) => !l.prefilled);
  return [...pre, ...(own.length ? own : [blank()])];
}

/** Products and units of an earlier invoice, in its order, once each. Weighed boxes (billed per
 * lb) come back as boxes, the unit they were ordered in. */
export function productsFromInvoice(inv: Pick<Invoice, "lines">): { product_id: string; unit_code: UnitCode }[] {
  const out: { product_id: string; unit_code: UnitCode }[] = [];
  for (const l of inv.lines) {
    const unit_code: UnitCode = l.unit_code === "LB" && l.cases ? "BOX" : l.unit_code;
    if (!out.some((x) => x.product_id === l.product_id && x.unit_code === unit_code)) out.push({ product_id: l.product_id, unit_code });
  }
  return out;
}

/** Units a product can be sold in: its active case sizes' units, then its stock unit. */
export function unitsFor(p: Pick<Product, "stock_unit" | "unit_profiles">): UnitCode[] {
  const out: UnitCode[] = [];
  const active = p.unit_profiles.filter((x) => x.active);
  const def = active.find((x) => x.is_default);
  for (const u of [def?.unit_code, ...active.map((x) => x.unit_code), p.stock_unit]) if (u && !out.includes(u)) out.push(u);
  return out;
}

/** Boxes and pieces are whole numbers; lb and kg allow 2 decimals. */
export function qtyProblem(unit: UnitCode | "", qty: string): string | null {
  const t = qty.trim();
  if (!unit) return "Choose the unit.";
  if (!isDecimal(t) || dec(t) <= 0n) return "Enter a quantity above 0.";
  if ((unit === "BOX" || unit === "PIECE") && t.includes(".") && dec(t) % 1_000_000n !== 0n) return "Boxes and pieces are whole numbers.";
  if (/\.\d{3,}$/.test(t)) return "Use at most 2 decimals.";
  return null;
}

/** First problem in the form, or null when it can be previewed. */
export function formProblem(customer: string, lines: DraftLine[]): string | null {
  if (!customer) return "Choose the customer.";
  const used = billable(lines);
  if (!used.length) return lines.some((l) => l.prefilled) ? "Enter a quantity for at least one product." : "Add at least one product.";
  for (const [i, l] of used.entries()) {
    const p = qtyProblem(l.unit_code, l.qty);
    if (p) return `Line ${i + 1}: ${p}`;
  }
  const seen = new Set<string>();
  for (const l of used) {
    const k = `${l.product_id}|${l.unit_code}`;
    if (seen.has(k)) return "The same product and unit is on two lines. Add the quantities together.";
    seen.add(k);
  }
  return null;
}
