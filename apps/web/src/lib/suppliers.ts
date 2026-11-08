import { ApiError, type SupplierPayMethod } from "../api";
import { dec, toFixed } from "./decimal";

// Supplier ledger helpers. Amounts keep their own currency; nothing is converted (D-04).

export const PAY_METHOD: Record<SupplierPayMethod, string> = {
  WIRE: "Wire transfer",
  EFT: "EFT / bank transfer",
  CHEQUE: "Cheque",
  CASH: "Cash",
  OTHER: "Other",
};

function group(i: string) {
  return i.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** "USD 1,234.50", "−CAD 50.00". */
export function amountIn(currency: string, v: string): string {
  const s = toFixed(dec(v), 2);
  const neg = s.startsWith("-");
  const [i = "0", f = "00"] = (neg ? s.slice(1) : s).split(".");
  return `${neg ? "−" : ""}${currency} ${group(i)}.${f}`;
}

/** What a balance means in words: we owe, nothing owed, or paid ahead. */
export function balanceWords(currency: string, v: string): string {
  const d = dec(v);
  if (d === 0n) return "Nothing owed";
  return d > 0n ? `We owe ${amountIn(currency, v)}` : `Paid ${amountIn(currency, toFixed(-d, 2))} ahead`;
}

const MONEY = /^\d{1,12}(\.\d{1,2})?$/;
/** Positive amount with at most 2 decimals, else a message. */
export function amountProblem(s: string): string | null {
  const t = s.trim();
  if (!MONEY.test(t) || dec(t) <= 0n) return "Enter an amount above 0 with at most 2 decimals.";
  return null;
}

export function supplierMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return "Something went wrong. Try again.";
  switch (err.code) {
    case "DUPLICATE_REFERENCE":
      return `${err.message}. Check you're not entering the same payment twice.`;
    case "ALREADY_VOIDED":
      return "That payment is already voided.";
    case "FUTURE_DATE":
      return "The payment date can't be in the future.";
    case "INVALID_SHIPMENT":
      return "That shipment is not from this supplier.";
    case "FORBIDDEN":
      return "You don't have permission to record supplier payments.";
    case "MFA_REQUIRED":
      return "Verify two-step sign-in first.";
    case "VALIDATION":
      return "Check the amount, date and method.";
    default:
      return err.message;
  }
}
