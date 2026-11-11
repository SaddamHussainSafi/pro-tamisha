import { ApiError, type OrderStatus, type PickStatus, type ShortageReason } from "../api";
import type { Tone } from "../ui/primitives";
import { cad, dec, mul } from "./decimal";

export const ORDER_STATUS: Record<OrderStatus, { tone: Tone; label: string }> = {
  DRAFT: { tone: "neutral", label: "Draft" },
  CONFIRMED: { tone: "info", label: "Confirmed" },
  PICKING: { tone: "warning", label: "Packing" },
  PARTIALLY_FULFILLED: { tone: "warning", label: "Partly supplied" },
  FULFILLED: { tone: "success", label: "Fulfilled" },
  CLOSED_WITH_SHORTAGE: { tone: "neutral", label: "Closed short" },
  CANCELLED: { tone: "danger", label: "Cancelled" },
};

export const PICK_STATUS: Record<PickStatus, { tone: Tone; label: string }> = {
  CREATED: { tone: "info", label: "To pack" },
  IN_PROGRESS: { tone: "warning", label: "Packing" },
  SUBMITTED: { tone: "warning", label: "Awaiting check" },
  NEEDS_CORRECTION: { tone: "danger", label: "Needs correction" },
  VERIFIED: { tone: "success", label: "Verified" },
  INVOICED: { tone: "success", label: "Invoiced" },
  ABORTED: { tone: "neutral", label: "Voided" },
};

export const SHORTAGE_LABEL: Record<ShortageReason, string> = {
  OUT_OF_STOCK: "Out of stock",
  DAMAGED: "Damaged",
  NOT_FOUND: "Couldn't find",
  OTHER: "Other",
};

export const AMENDMENT_LABEL: Record<string, string> = {
  QTY_CANCELLED: "Quantity cancelled",
  SHORTAGE_CANCELLED: "Shortfall cancelled at verification",
  ORDER_CLOSED: "Closed with shortage",
  ORDER_CANCELLED: "Order cancelled",
};

/** What the warehouse packed vs requested: PICKED / SHORT / NO (0 packed — not a cancellation). */
export function pickOutcome(requested: string, picked: string): "PICKED" | "SHORT" | "NO" {
  const p = dec(picked);
  if (p === 0n) return "NO";
  return p >= dec(requested) ? "PICKED" : "SHORT";
}

/** Money: rate × qty rounded half-up to cents, formatted CAD (display only; invoices compute server-side). */
export const lineAmount = (rate: string, q: string): string => cad(mul(dec(rate), dec(q)));

export function ordersMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return "Something went wrong. Nothing was saved — try again.";
  const x = err.extra;
  switch (err.code) {
    case "NO_PRICE":
      return `${err.message}. Publish a price for it in Pricing, then confirm again.`;
    case "INVALID_UNIT":
    case "UNIT_NOT_DIVISIBLE":
    case "DUPLICATE_LINE":
    case "PRODUCT_INACTIVE":
    case "INVALID_PRODUCT":
      return err.message;
    case "INVALID_CUSTOMER":
    case "CUSTOMER_INACTIVE":
      return "Choose an active customer.";
    case "OPEN_PICK_EXISTS":
      return `${err.message}. Finish or void it before printing another.`;
    case "NOTHING_TO_PICK":
      return "Nothing is pending on this order.";
    case "OVER_PICK":
      return `${err.message}. Enter at most the requested quantity.`;
    case "SHORTAGE_REASON_REQUIRED":
      return `${err.message} (out of stock, damaged…).`;
    case "PICK_INCOMPLETE":
      return "Enter a packed quantity for every line (0 if none was packed).";
    case "REASON_REQUIRED":
      return "Give a reason for cancelling the shortfall.";
    case "EXCEEDS_PENDING":
      return x.pending !== undefined ? `Only ${String(x.pending)} is still pending.` : err.message;
    case "AWAITING_INVOICE":
      return "Packed goods are waiting to be invoiced. Invoice them before closing the order.";
    case "ORDER_HAS_PICKS":
      return "This order has been packed. Close it with a shortage reason instead.";
    case "VERSION_CONFLICT":
      return "Someone else changed this record. The latest data has been loaded — review and retry.";
    case "INVALID_STATE":
      return "This record was already processed. The latest data has been loaded.";
    case "FORBIDDEN":
      return "You don't have permission for this action in this warehouse.";
    case "MFA_REQUIRED":
      return "Verify two-step authentication first.";
    default:
      return err.message;
  }
}
