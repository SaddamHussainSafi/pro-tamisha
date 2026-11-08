import type { Product, UnitCode } from "../api";
import { dec, isDecimal } from "./decimal";

// Direct invoice (no picking sheet) form helpers. The server prices, checks stock and issues.

export type DraftLine = { key: number; product_id: string; unit_code: UnitCode | ""; qty: string };

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
  const used = lines.filter((l) => l.product_id);
  if (!used.length) return "Add at least one product.";
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
