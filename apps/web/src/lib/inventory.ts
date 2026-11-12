import { ApiError, type MovementType, type Product, type UnitCode } from "../api";
import { errorText } from "./errors";
import { dec, toFixed } from "./decimal";

/** Plain-language messages for inventory API errors. */
export function inventoryMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return errorText(err);
  const x = err.extra;
  switch (err.code) {
    case "INSUFFICIENT_STOCK":
      return x.available !== undefined
        ? `Not enough stock: only ${qtyText(String(x.available))} available (on-hand minus reserved). Nothing was posted.`
        : "Not enough stock for this change. Nothing was posted.";
    case "INVALID_UNIT":
      return Array.isArray(x.allowed_units)
        ? `That unit isn't stocked for this product. Use ${x.allowed_units.join(", ")}.`
        : "That unit isn't stocked for this product.";
    case "UNIT_NOT_DIVISIBLE":
      return "Boxes and pieces must be whole numbers.";
    case "ZERO_QUANTITY":
      return "Enter a quantity greater than zero.";
    case "EXPIRY_REQUIRED":
      return "This product tracks expiry — enter the expiry date.";
    case "DUPLICATE_UNIT":
      return "Each unit can appear only once per lot.";
    case "DUPLICATE":
      return "That lot code is already used.";
    case "LOT_NOT_SALEABLE":
      return "This lot is quarantined, damaged or expired and can't be reserved.";
    case "LOT_RESERVED":
      return "Stock in this lot is reserved for orders. Release the reservations first.";
    case "STALE_COUNT":
      return "Stock moved after this count was taken. Reject it and count again — nothing was posted.";
    case "COUNT_BELOW_RESERVED":
      return "The counted quantity is below what is reserved for orders. Release reservations or recount.";
    case "ALREADY_REVERSED":
      return "This movement has already been reversed.";
    case "NOT_REVERSIBLE":
      return "This movement can't be reversed here. Receipts and dispatches are corrected from their source document.";
    case "SOURCE_DOCUMENT_REQUIRED":
      return "Correct this movement from its source document (receipt or invoice).";
    case "VERSION_CONFLICT":
      return "Someone else changed this lot. The latest data has been loaded — review and retry.";
    case "INVALID_STATE":
      return "This record was already processed. The latest data has been loaded.";
    case "REBUILD_CONFLICT":
      return "Stock moved during the rebuild. Run the check again.";
    case "WRONG_WAREHOUSE":
      return "Every counted lot must be in the same warehouse.";
    case "FORBIDDEN":
      return "You don't have permission for this action in this warehouse.";
    case "MFA_REQUIRED":
      return "Verify two-step authentication to change stock.";
    case "VALIDATION":
      return "Check the highlighted fields.";
    default:
      return errorText(err);
  }
}

/** Quantities are 4 dp on the server; trim trailing zeros, keep whole numbers whole. */
export function qtyText(s: string, unit?: string): string {
  const f = toFixed(dec(s), 4).replace(/\.?0+$/, "");
  const shown = f === "" || f === "-" ? "0" : f;
  return unit ? `${shown} ${unit.toLowerCase()}` : shown;
}

export const signed = (s: string) => (s.startsWith("-") ? `−${qtyText(s.slice(1))}` : `+${qtyText(s)}`);

export const MOVEMENT_LABEL: Record<MovementType, string> = {
  OPENING_BALANCE: "Opening balance",
  GRN_RECEIPT: "Goods received",
  INVOICE_DISPATCH: "Invoice dispatch",
  ADJUSTMENT: "Adjustment",
  COUNT_ADJUSTMENT: "Count correction",
  DAMAGE: "Damage",
  RETURN: "Return",
  REVERSAL: "Reversal",
};

/** Mirrors the server's stock_units(): stock unit, LB, active profile units; KG only for KG-stocked products. */
export function stockUnits(p: Product): UnitCode[] {
  const out = new Set<UnitCode>([p.stock_unit, "LB"]);
  for (const prof of p.unit_profiles) if (prof.active) out.add(prof.unit_code);
  if (p.stock_unit !== "KG") out.delete("KG");
  const order: UnitCode[] = ["BOX", "PIECE", "LB", "KG"];
  return order.filter((u) => out.has(u));
}

export const wholeOnly = (u: UnitCode) => u === "BOX" || u === "PIECE";
