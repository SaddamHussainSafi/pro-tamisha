import { ApiError, type InvoiceState, type PaymentMethod, type PaymentStatus } from "../api";
import { errorText } from "./errors";
import type { Tone } from "../ui/primitives";
import { dec, toFixed, type Dec } from "./decimal";

export const PAYMENT_STATUS: Record<PaymentStatus, { tone: Tone; label: string; hint: string }> = {
  REPORTED: { tone: "warning", label: "Reported", hint: "Customer says it was sent. Not confirmed at the bank yet." },
  VERIFIED: { tone: "info", label: "Confirmed", hint: "Found at the bank. Some money is not applied to an invoice yet." },
  APPLIED: { tone: "success", label: "Applied", hint: "Confirmed and fully applied to invoices." },
  REJECTED: { tone: "danger", label: "Rejected", hint: "Not found at the bank. Kept for the record." },
};

export const METHOD: Record<PaymentMethod, string> = {
  INTERAC: "Interac e-Transfer",
  EFT: "EFT / bank transfer",
  CHEQUE: "Cheque",
  CASH: "Cash",
};

/** What the employee must type as proof, per method. */
export const REF_LABEL: Record<PaymentMethod, string> = {
  INTERAC: "Bank reference of the deposit",
  EFT: "Bank reference of the deposit",
  CHEQUE: "Cheque number",
  CASH: "Cash receipt number",
};

export const INVOICE_STATE: Record<InvoiceState, { tone: Tone; label: string }> = {
  ISSUED: { tone: "warning", label: "Unpaid" },
  PARTIALLY_PAID: { tone: "info", label: "Partly paid" },
  PAID: { tone: "success", label: "Paid" },
  VOIDED: { tone: "neutral", label: "Voided" },
  CREDITED: { tone: "neutral", label: "Credited" },
};

const MONEY_RE = /^\d{1,10}(\.\d{1,2})?$/;

/** A positive CAD amount with at most 2 decimals, or null. */
export function moneyOrNull(s: string): Dec | null {
  const t = s.trim();
  if (!MONEY_RE.test(t)) return null;
  const v = dec(t);
  return v > 0n ? v : null;
}

export const money2 = (v: Dec) => toFixed(v, 2);

export type OpenInvoice = { id: string; balance: string; due_at: string };

/**
 * Suggest how to spread `amount` over open invoices: the invoice the customer named first,
 * then oldest due date first. Never more than an invoice's balance. Returns id -> amount.
 */
export function suggestAllocation(amount: string, invoices: OpenInvoice[], preferId?: string | null): Record<string, string> {
  let left = dec(amount);
  const order = [...invoices].sort((a, b) => {
    if (a.id === preferId) return -1;
    if (b.id === preferId) return 1;
    return a.due_at.localeCompare(b.due_at);
  });
  const out: Record<string, string> = {};
  for (const inv of order) {
    if (left <= 0n) break;
    const bal = dec(inv.balance);
    if (bal <= 0n) continue;
    const take = bal < left ? bal : left;
    out[inv.id] = money2(take);
    left -= take;
  }
  return out;
}

export type PlanCheck = { total: Dec; problems: string[] };

/** Client-side check of an allocation plan; the server re-checks everything under locks. */
export function checkPlan(plan: Record<string, string>, invoices: (OpenInvoice & { invoice_no: string })[], available: string): PlanCheck {
  const problems: string[] = [];
  let total = 0n;
  for (const inv of invoices) {
    const raw = (plan[inv.id] ?? "").trim();
    if (!raw) continue;
    const v = moneyOrNull(raw);
    if (v === null) {
      problems.push(`${inv.invoice_no}: enter an amount like 125.50`);
      continue;
    }
    if (v > dec(inv.balance)) problems.push(`${inv.invoice_no}: more than the CAD ${inv.balance} open`);
    total += v;
  }
  if (total > dec(available)) problems.push(`Total is more than the CAD ${available} available`);
  return { total, problems };
}

export function paymentMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return errorText(err);
  const x = err.extra;
  switch (err.code) {
    case "ALREADY_VERIFIED":
      return "This payment was already confirmed by someone else. The page now shows the latest state.";
    case "DUPLICATE_CONFIRMATION":
      return `That bank reference already confirmed payment ${String(x.payment_no ?? "")}. Check the bank statement — the customer may have reported the same transfer twice.`;
    case "DUPLICATE":
      return "That bank reference was just used to confirm another payment. Nothing was saved.";
    case "VERSION_CONFLICT":
      return "Someone changed this payment while you were looking at it. Reload and try again.";
    case "EXCEEDS_INVOICE_BALANCE":
      return `${err.message}. Nothing was applied.`;
    case "EXCEEDS_PAYMENT":
      return `${err.message}. Nothing was applied.`;
    case "INVOICE_NOT_OPEN":
      return `${err.message}. Nothing was applied.`;
    case "FORBIDDEN":
      return "You don't have permission to do this.";
    case "MFA_REQUIRED":
      return "Verify your authenticator code first.";
    case "VALIDATION":
      return "Check the highlighted values and try again.";
    default:
      return errorText(err);
  }
}
