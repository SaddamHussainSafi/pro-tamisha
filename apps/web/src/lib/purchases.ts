import { ApiError, type CorrectionKind, type PurchaseCorrection, type PurchaseItem, type PurchaseLineIn, type PurchaseStage, type Shipment, type ShipmentDetail, type UnitCode } from "../api";
import type { Tone } from "../ui/primitives";
import { add, dec, isDecimal, mul, toFixed } from "./decimal";
import { qtyText } from "./inventory";
import type { GridLine } from "./lineGrid";
import { receivingMessage } from "./receiving";

// Purchases (owner, 2026-11-12): one flow for every supplier, in stock the moment it is saved.
// Pure helpers for the screens; the server decides and totals everything.

export type Chip = { tone: Tone; label: string };

/** The Status column: is it in stock, and is anything about the stock itself still open. */
export const STAGE: Record<PurchaseStage, Chip> = {
  IN_STOCK: { tone: "success", label: "In stock" },
  TO_VERIFY: { tone: "success", label: "In stock" },
  TO_APPROVE: { tone: "success", label: "In stock" },
  TO_COMPLETE: { tone: "success", label: "In stock" },
  VERIFIED: { tone: "success", label: "Verified" },
  NOT_IN_STOCK: { tone: "neutral", label: "Not in stock yet" },
  CANCELLED: { tone: "neutral", label: "Cancelled" },
};

type Flags = Pick<Shipment, "stage" | "is_airport" | "needs_details" | "pending_corrections" | "review_count" | "matched_shipment_no" | "entry">;

/** What still needs someone, shown as badges next to the status. */
export function badges(s: Flags): Chip[] {
  const out: Chip[] = [];
  if (s.needs_details) out.push({ tone: "warning", label: "Details to complete" });
  if (s.is_airport && s.stage === "TO_VERIFY") out.push({ tone: "warning", label: "Airport · to verify" });
  if (s.stage === "TO_APPROVE") out.push({ tone: "warning", label: "Airport · needs manager's OK" });
  else if (s.pending_corrections > 0) out.push({ tone: "warning", label: `${s.pending_corrections} correction${s.pending_corrections === 1 ? "" : "s"} to approve` });
  // amber, not red: page headers sit on the grey canvas, where the red chip is too faint to read
  if (s.review_count > 0) out.push({ tone: "warning", label: "To review" });
  if (s.matched_shipment_no) out.push({ tone: "info", label: `Matched to ${s.matched_shipment_no}` });
  else if (s.entry === "QUICK" && !s.needs_details) out.push({ tone: "neutral", label: "Quick receive" });
  return out;
}

/** List filters of the Purchases tab → the server's `stage` parameter. */
export type PurchaseView = "all" | "complete" | "verify" | "stock" | "old" | "cancelled";
export const VIEW_STAGE: Record<PurchaseView, string | undefined> = {
  all: undefined,
  complete: "TO_COMPLETE",
  verify: "TO_VERIFY,TO_APPROVE",
  stock: "IN_STOCK,VERIFIED,TO_VERIFY,TO_APPROVE,TO_COMPLETE",
  old: "NOT_IN_STOCK",
  cancelled: "CANCELLED",
};

export const supplierName = (s: Pick<Shipment, "vendor_name">) => s.vendor_name || "Supplier invoice to follow";

const UNIT_WORD: Record<UnitCode, [string, string]> = { BOX: ["box", "boxes"], PIECE: ["piece", "pieces"], LB: ["lb", "lb"], KG: ["kg", "kg"] };
/** "5 boxes", "1 box", "12.5 lb". */
export function qtyWords(qty: string, unit: UnitCode): string {
  const n = qtyText(qty);
  return `${n} ${UNIT_WORD[unit][n === "1" ? 0 : 1]}`;
}

/** "5 boxes Yellow yam" / "5 boxes Yellow yam + 2 more". */
export function goodsText(items: PurchaseItem[], lineCount: number): string {
  const first = items[0];
  if (!first) return "No lines";
  const more = lineCount - 1;
  return `${qtyWords(first.qty, first.unit_code)} ${first.product_name}${more > 0 ? ` + ${more} more` : ""}`;
}

/** qty × cost to the cent (half up), or null while either is missing or not a number. */
export function lineAmount(qty: string, cost: string): string | null {
  const q = qty.trim();
  const c = cost.trim();
  if (!q || !c || !isDecimal(q) || !isDecimal(c)) return null;
  try {
    return toFixed(mul(dec(q), dec(c)), 2);
  } catch {
    return null;
  }
}

/** A cost cell: empty is allowed (cost to follow); otherwise a number with up to 4 decimals. */
export function costProblem(cost: string): string | null {
  const t = cost.trim();
  if (!t) return null;
  if (!isDecimal(t) || t.startsWith("-")) return "The cost must be a number.";
  if (/\.\d{5,}$/.test(t)) return "Use at most 4 decimals for the cost.";
  return null;
}

/** Total of the lines typed so far (cents) and how many have no cost yet. */
export function gridTotal(lines: Pick<GridLine, "product_id" | "qty" | "cost">[]): { total: string; unpriced: number } {
  let sum = 0n;
  let unpriced = 0;
  for (const l of lines) {
    if (!l.product_id || !l.qty.trim()) continue;
    const a = lineAmount(l.qty, l.cost ?? "");
    if (a === null) unpriced++;
    else sum = add(sum, dec(a));
  }
  return { total: toFixed(sum, 2), unpriced };
}

/** A date field's value as the moment the server stores (noon, so no time zone moves the day). */
export const expiryIso = (d: string | undefined | null) => (d ? new Date(`${d}T12:00:00`).toISOString() : null);

export function toLineIn(l: GridLine): PurchaseLineIn {
  return { product_id: l.product_id, unit_code: l.unit_code as UnitCode, qty: l.qty.trim(), unit_cost: (l.cost ?? "").trim() || null, label: l.note.trim() || null, expires_at: expiryIso(l.expires) };
}

/** How many of this product + unit are missing for the work at hand, or null when enough. */
export function shortfall(needed: string, available: string): string | null {
  if (!isDecimal(needed) || !isDecimal(available)) return null;
  const d = dec(needed) - dec(available);
  return d > 0n ? toFixed(d, 4) : null;
}

/** Quick receives waiting for an invoice that cover goods on the lines being entered. */
export function matchingQuick(open: Shipment[], lines: { product_id: string; unit_code: string }[], except?: string): Shipment[] {
  const keys = new Set(lines.filter((l) => l.product_id).map((l) => `${l.product_id}:${l.unit_code}`));
  return open.filter((q) => q.id !== except && q.needs_details && q.items.some((i) => keys.has(`${i.product_id}:${i.unit_code}`)));
}

export const CORRECTION: Record<CorrectionKind, { label: string; verb: string }> = {
  SHORT: { label: "Short", verb: "fewer arrived" },
  DAMAGED: { label: "Damaged", verb: "arrived damaged" },
  RETURNED: { label: "Returned to supplier", verb: "sent back" },
  OVER: { label: "More than invoiced", verb: "extra arrived" },
};

/** One plain sentence for a correction row. */
export function correctionText(c: Pick<PurchaseCorrection, "kind" | "qty" | "unit_code" | "product_name" | "status" | "posted_qty" | "uncovered_qty">): string {
  const what = `${qtyWords(c.qty, c.unit_code)} ${c.product_name}`;
  const head = c.kind === "OVER" ? `${what} more than the invoice` : c.kind === "DAMAGED" ? `${what} damaged` : c.kind === "RETURNED" ? `${what} returned to the supplier` : `${what} short`;
  if (c.status === "PENDING") return `${head} — waiting for a manager's OK. Stock is unchanged.`;
  if (c.status === "REJECTED") return `${head} — refused. Stock was not changed.`;
  const posted = c.posted_qty ?? "0";
  if (dec(c.uncovered_qty) > 0n) {
    return `${head}. ${qtyWords(posted, c.unit_code)} taken from stock; ${qtyWords(c.uncovered_qty, c.unit_code)} could not be taken because it was already invoiced or reserved.`;
  }
  return c.kind === "OVER" ? `${head} — added to stock.` : c.kind === "DAMAGED" ? `${head} — moved out of saleable stock.` : `${head} — taken from stock.`;
}

/** Stock lines of a purchase with what later corrections did to them. */
export function stockNow(detail: Pick<ShipmentDetail, "counted" | "corrections">): Map<string, string> {
  const out = new Map<string, bigint>();
  for (const c of detail.counted) out.set(c.id, dec(c.accepted_qty));
  for (const x of detail.corrections) {
    if (x.status !== "POSTED" || !out.has(x.grn_line_id)) continue;
    const q = dec(x.posted_qty ?? "0");
    out.set(x.grn_line_id, (out.get(x.grn_line_id) ?? 0n) + (x.kind === "OVER" ? q : x.kind === "DAMAGED" ? 0n : -q));
  }
  return new Map([...out].map(([k, v]) => [k, toFixed(v, 4)]));
}

type Extra = { differences?: { product_name: string; unit: UnitCode; invoice_qty: string; received_qty: string }[]; expected?: number; counted?: number };

export function purchaseMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return receivingMessage(err);
  const x = err.extra as Extra;
  switch (err.code) {
    case "INVOICE_ALREADY_ENTERED":
    case "QUICK_NOT_ON_INVOICE":
    case "PIECES_DIFFER":
    case "MORE_THAN_RECEIVED":
      return err.message;
    case "MATCH_REASON_REQUIRED":
      return Array.isArray(x.differences) && x.differences.length
        ? `The invoice and what was already added to stock differ (${x.differences.map((d) => `${d.product_name}: invoice ${qtyWords(d.invoice_qty, d.unit)}, added ${qtyWords(d.received_qty, d.unit)}`).join("; ")}). Say why below.`
        : "The invoice and what was already added to stock differ. Say why below.";
    case "QUICK_ALREADY_MATCHED":
      return "That quick receive was already matched to an invoice. Reload and try again.";
    case "REASON_REQUIRED":
      return "Give a reason (at least 3 characters).";
    case "NOT_AIRPORT":
      return "This applies to airport shipments only.";
    case "SUPPLIER_NEEDED":
      return "Complete the details (supplier and invoice) first; documents are filed under the supplier.";
    case "VERSION_CONFLICT":
      return "Someone else changed this purchase. It has been reloaded — check it and try again.";
    case "INVALID_STATE":
      return err.message || "This purchase has moved on. Reload to see where it stands.";
    case "INVALID_PRODUCT":
      return "One of the products is no longer in the product list.";
    case "PRODUCT_INACTIVE":
      return `${err.message}. Reactivate it under Products, or pick another product.`;
    case "INVALID_UNIT":
      return `${err.message}. Choose another unit for that line.`;
    case "FORBIDDEN":
      return "You don't have permission for this step. Ask a manager.";
    default:
      return receivingMessage(err);
  }
}
