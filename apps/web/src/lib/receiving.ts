import { ApiError, type CompareRow, type DeclaredLine, type ProductSummary, type UnitCode, type CompareStatus, type LandedCostKind, type ShipmentDocKind, type ShipmentStatus } from "../api";
import { errorText } from "./errors";
import type { Tone } from "../ui/primitives";
import { add, dec, isDecimal, toFixed } from "./decimal";
import { qtyText } from "./inventory";

export const SHIPMENT_STATUS: Record<ShipmentStatus, { tone: Tone; label: string }> = {
  OPEN: { tone: "info", label: "Receiving" },
  COUNTED: { tone: "warning", label: "Waiting for approval" },
  POSTED: { tone: "success", label: "Posted to stock" },
  CANCELLED: { tone: "neutral", label: "Cancelled" },
};

export const DOC_KIND: Record<ShipmentDocKind, string> = {
  INVOICE: "Supplier invoice",
  AWB: "Airway bill",
  PACKING_LIST: "Packing list",
  COMBINED: "Invoice + packing list",
  OTHER: "Other document",
};

export const COST_KIND: Record<LandedCostKind, string> = {
  FREIGHT: "Freight",
  DUTY: "Duty",
  BROKERAGE: "Brokerage",
  INSURANCE: "Insurance",
  OTHER: "Other",
};

export const COMPARE: Record<CompareStatus, { tone: Tone; label: string; hint: string }> = {
  MATCH: { tone: "success", label: "Matches", hint: "Counted what the documents say." },
  SHORT: { tone: "danger", label: "Short", hint: "Fewer arrived than the documents say." },
  OVER: { tone: "warning", label: "Over", hint: "More arrived than the documents say." },
  DAMAGED: { tone: "warning", label: "Damaged / rejected", hint: "Some arrived damaged or were refused." },
  NOT_DECLARED: { tone: "warning", label: "Not on documents", hint: "Counted, but no supplier document lists it." },
  NOT_RECEIVED: { tone: "danger", label: "Not received", hint: "Listed on a document, nothing counted." },
  DOC_CONFLICT: { tone: "danger", label: "Documents disagree", hint: "The supplier documents list different quantities." },
};

const WEIGHTS = new Set(["LB", "KG"]);
export const unitPlaces = (unit: string) => (WEIGHTS.has(unit) ? 4 : 0);

/** Sum of a count line (accepted + damaged + rejected) as a 4 dp string; "" inputs count as 0. */
export function countTotal(accepted: string, damaged: string, rejected: string): string {
  const v = (s: string) => (s !== "" && isDecimal(s) ? dec(s) : 0n);
  return toFixed(add(v(accepted), v(damaged), v(rejected)), 4);
}

/** "+2 box" / "−2 box" / "0" for a difference (received − declared). */
export function diffText(d: string | null, unit: string): string {
  if (d === null) return "—";
  if (/^-?0(\.0+)?$/.test(d)) return "0";
  return d.startsWith("-") ? `−${qtyText(d.slice(1), unit)}` : `+${qtyText(d, unit)}`;
}

/** Saleable quantity that posting will add: accepted only, never declared or damaged. */
export function saleableSummary(rows: Pick<CompareRow, "accepted_qty" | "unit_code">[]): string {
  const by = new Map<string, bigint>();
  for (const r of rows) {
    if (!r.accepted_qty) continue;
    by.set(r.unit_code, (by.get(r.unit_code) ?? 0n) + dec(r.accepted_qty));
  }
  if (by.size === 0) return "nothing";
  return [...by.entries()].map(([u, v]) => qtyText(toFixed(v, 4), u)).join(" + ");
}

export function moneyText(amount: string, currency: string): string {
  const [i = "0", f = "00"] = toFixed(dec(amount), 2).split(".");
  return `${currency} ${i.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}.${f}`;
}

export function receivingMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return errorText(err);
  switch (err.code) {
    case "VERSION_CONFLICT":
      return "Someone else changed this shipment. It has been reloaded — check it and try again.";
    case "INVALID_STATE":
      return "This shipment has moved on (counted, posted or cancelled). Reload to see its current state.";
    case "DISCREPANCY_ACK_REQUIRED":
      return "Some lines differ from the supplier documents. Tick the acknowledgement and explain the difference.";
    case "UNMAPPED_LABEL":
      return `${err.message}`;
    case "EMPTY_COUNT":
      return "Enter how many were accepted, damaged or refused.";
    case "EXPIRY_REQUIRED":
      return `${err.message}.`;
    case "NOTHING_COUNTED":
      return "Count at least one line before sending for approval.";
    case "ALREADY_ATTACHED":
      return "That document is already on this shipment.";
    case "SCAN_NOT_RECEIVED":
      return "That scan has not arrived yet.";
    case "VENDOR_INACTIVE":
      return "That supplier is inactive. Reactivate it under Suppliers first.";
    case "UNIT_NOT_DIVISIBLE":
      return "Boxes and pieces must be whole numbers.";
    case "FILE_TOO_LARGE":
      return "That file is too large (20 MB max).";
    case "UNSUPPORTED_FILE":
    case "FILE_TYPE_MISMATCH":
      return "Upload a PDF, PNG, JPEG or TIFF file.";
    case "EMPTY_FILE":
      return "That file is empty.";
    case "DUPLICATE":
      return "A supplier with that code already exists.";
    case "FORBIDDEN":
      return "You don't have permission for this step.";
    case "MFA_REQUIRED":
      return "Verify two-step sign-in to approve receiving.";
    default:
      return errorText(err);
  }
}

export const productLabel = (p: Pick<ProductSummary, "sku" | "name">) => `${p.sku} — ${p.name}`;

/** Count grid: the product the typed text names exactly ("ALO — Aloe", "ALO" or "Aloe",
 * any case), or null while it names none. */
export function findProduct<P extends Pick<ProductSummary, "sku" | "name">>(products: P[], text: string): P | null {
  const t = text.trim().toLowerCase();
  if (!t) return null;
  return (
    products.find((p) => productLabel(p).toLowerCase() === t) ??
    products.find((p) => p.sku.toLowerCase() === t) ??
    products.find((p) => p.name.toLowerCase() === t) ??
    null
  );
}

/* ---------- units on the receiving screen (owner, 2026-11-12: "kg, lb and box everywhere,
   lb by default, kg only if the shipment's documents are in kg") ---------- */

export type UnitChoice = { value: UnitCode; label: string; disabled?: boolean };

const KG_WORD = /\bkgs?\b|\bkilo/i;

/** Box, lb and kg are always offered (piece only for products sold by the piece). A product
 * kept in lb may be entered in kg: the server saves it as lb. A box needs a case size. */
export function receiveUnits(stock: UnitCode[] | null): UnitChoice[] {
  const out: UnitChoice[] = [];
  const known = stock === null; // no product chosen yet
  out.push(known || stock.includes("BOX") ? { value: "BOX", label: "box" } : { value: "BOX", label: "box (no case size)", disabled: true });
  if (stock?.includes("PIECE")) out.push({ value: "PIECE", label: "piece" });
  out.push({ value: "LB", label: "lb" }, { value: "KG", label: "kg" });
  return out;
}

/** The shipment's documents are in kg: a line read from them says KG. */
export function docsInKg(declared: Pick<DeclaredLine, "raw_label" | "unit_code" | "removed_at">[]): boolean {
  return declared.some((d) => !d.removed_at && (d.unit_code === "KG" || KG_WORD.test(d.raw_label)));
}

export const mentionsKg = (text: string) => KG_WORD.test(text);

/** lb unless the documents are in kg. */
export const defaultUnit = (kg: boolean): UnitCode => (kg ? "KG" : "LB");

/** True when kg is chosen for a product whose stock is kept in lb (saved as lb). */
export const kgSavedAsLb = (unit: UnitCode, stock: UnitCode[] | null) => unit === "KG" && !!stock && !stock.includes("KG");
