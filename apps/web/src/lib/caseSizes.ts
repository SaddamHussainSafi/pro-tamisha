import type { PriceVersion, UnitCode, UnitProfile } from "../api";
import { dec, qty } from "./decimal";

// Case sizes are product unit profiles: e.g. Cassava "30LB" = one box weighing 30 lb.
// Conversions never change once created; a new size is added and the old one deactivated.

export const CASE_CODE_RE = /^[A-Za-z0-9_-]{1,32}$/;

/** "30LB · 30 lb a box", "PC · 1 piece", "LB · sold by weight". */
export function caseSizeText(p: Pick<UnitProfile, "code" | "unit_code" | "weight_lb_per_unit" | "pieces_per_unit">): string {
  if (p.unit_code === "LB" || p.unit_code === "KG") return `${p.code} · sold by ${p.unit_code.toLowerCase()}`;
  const per = p.unit_code === "BOX" ? "a box" : "a piece";
  const w = p.weight_lb_per_unit ? `${qty(dec(p.weight_lb_per_unit), 2).replace(/\.00$/, "")} lb ${per}` : "weight not set";
  const pcs = p.pieces_per_unit && p.unit_code === "BOX" ? ` · ${qty(dec(p.pieces_per_unit), 0)} pcs` : "";
  return `${p.code} · ${w}${pcs}`;
}

/** Suggested code from a weight: "30" → "30LB", "35.5" → "35.5LB". */
export function suggestCode(unit: UnitCode, weight: string): string {
  const w = weight.trim();
  if (unit !== "BOX" || !/^\d{1,4}(\.\d{1,2})?$/.test(w)) return "";
  return `${w.replace(/\.0+$/, "")}LB`;
}

/** The active default size, else the first active one. */
export function defaultSize<T extends Pick<UnitProfile, "is_default" | "active">>(sizes: T[]): T | undefined {
  const active = sizes.filter((s) => s.active);
  return active.find((s) => s.is_default) ?? active[0];
}

/** Weight each to prefill when billing a box by weight: the default case size's weight, if set. */
export function defaultCaseWeight(sizes: UnitProfile[], unit: UnitCode): string | null {
  const d = defaultSize(sizes.filter((s) => s.unit_code === unit));
  return d?.weight_lb_per_unit ?? null;
}

/** Code for a new price draft made from a product page, e.g. "PB-20261108-1432". */
export function draftCode(now = new Date()): string {
  const p = (n: number) => String(n).padStart(2, "0");
  return `PB-${now.getFullYear()}${p(now.getMonth() + 1)}${p(now.getDate())}-${p(now.getHours())}${p(now.getMinutes())}`;
}

/** The price book in force now, and the newest open draft to add changes to. */
export function priceBooks(versions: PriceVersion[]): { current?: PriceVersion; draft?: PriceVersion } {
  const current = versions.find((v) => v.state === "CURRENT");
  const draft = versions
    .filter((v) => v.state === "DRAFT")
    .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  return { current, draft };
}
