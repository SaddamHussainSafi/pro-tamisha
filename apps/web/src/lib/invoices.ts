import { ApiError, type EmailStatus, type WeightRow } from "../api";
import type { Tone } from "../ui/primitives";
import { add, dec, isDecimal, mul, qty } from "./decimal";

export const EMAIL_STATUS: Record<EmailStatus, { tone: Tone; label: string }> = {
  NO_EMAIL: { tone: "neutral", label: "No email on file" },
  QUEUED: { tone: "info", label: "Email queued" },
  SENT: { tone: "success", label: "Emailed" },
  FAILED: { tone: "danger", label: "Email failed" },
};

/** One editable row in the case-weight form: `cases` boxes each weighing `weight` lb. */
export type WeightDraft = { cases: string; weight: string };

/** Totals for the entered weights. Returns null while any row is incomplete or invalid. */
export function weightTotals(rows: WeightDraft[]): { cases: number; lb: string } | null {
  if (rows.length === 0) return null;
  let cases = 0;
  const parts: bigint[] = [];
  for (const r of rows) {
    if (!/^\d{1,6}$/.test(r.cases) || Number(r.cases) < 1) return null;
    if (!isDecimal(r.weight) || (r.weight.split(".")[1]?.length ?? 0) > 4 || dec(r.weight) <= 0n) return null;
    cases += Number(r.cases);
    parts.push(mul(dec(r.weight), dec(r.cases)));
  }
  return { cases, lb: qty(add(...parts), 2) };
}

export const toWeightRows = (rows: WeightDraft[]): WeightRow[] => rows.map((r) => ({ cases: Number(r.cases), weight_lb: r.weight.trim() }));

export function invoiceMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return "Something went wrong. Nothing was issued — try again.";
  const x = err.extra;
  switch (err.code) {
    case "INSUFFICIENT_STOCK":
      return `${err.message} Nothing was issued.`;
    case "WEIGHTS_MISMATCH":
      return `Case weights must cover exactly ${String(x.expected ?? "")} cases (you entered ${String(x.given ?? "")}).`;
    case "WEIGHTS_NOT_ALLOWED":
    case "NO_PRICE":
    case "NOTHING_TO_INVOICE":
      return err.message;
    case "DISPATCH_NOT_CONFIRMED":
      return "Tick the dispatch box to confirm the goods have left the warehouse.";
    case "ALREADY_INVOICED":
      return "This picking sheet already has an invoice.";
    case "TAX_RULES_UNVERIFIED":
      return "Invoices are switched off until the accountant signs off the tax rules.";
    case "VERSION_CONFLICT":
    case "INVALID_STATE":
      return "This picking sheet changed since you opened it. It has been reloaded — check and try again.";
    case "EMAIL_QUEUED":
      return "This invoice is already waiting to be emailed.";
    case "NO_EMAIL_ADDRESS":
      return "The customer has no email address. Add one to the customer first.";
    case "FORBIDDEN":
    case "MFA_REQUIRED":
      return "You don't have permission to do that.";
    default:
      return err.message || "Something went wrong. Nothing was issued — try again.";
  }
}
