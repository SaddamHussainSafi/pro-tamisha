import { ApiError, type Customer, type CustomerIn, type CustomerPatch } from "../api";

// Customers screen helpers: form <-> API mapping and plain-language errors. The server re-checks
// every rule; these checks only spare the user a round trip.

export type CustomerForm = {
  ref: string;
  name: string;
  email: string;
  phone: string;
  fax: string;
  bill_to: string; // address lines, one per line
  ship_to_name: string; // "" = same as the name
  ship_to: string; // "" = same as bill-to
  vendor_no: string; // the number this customer gave us ("Vendor #" on invoices)
  group_id: string; // "" = no group
  terms: string; // days, as typed
  active: boolean;
};

export const EMPTY_FORM: CustomerForm = {
  ref: "",
  name: "",
  email: "",
  phone: "",
  fax: "",
  bill_to: "",
  ship_to_name: "",
  ship_to: "",
  vendor_no: "",
  group_id: "",
  terms: "0",
  active: true,
};

/** Address as stored: lines trimmed, blank lines dropped. */
export function addressText(v: string): string {
  return v
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .join("\n");
}

// Text fields printed on invoices: same name in the form and the API; addresses are renamed.
const TEXT = ["fax", "ship_to_name", "vendor_no"] as const;
const ADDRESS = [
  ["bill_to", "bill_to_address"],
  ["ship_to", "ship_to_address"],
] as const;

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const REF_RE = /^[A-Za-z0-9._-]+$/;

export function termsLabel(days: number): string {
  return days === 0 ? "Due on receipt" : `Net ${days} ${days === 1 ? "day" : "days"}`;
}

export function formFrom(c: Customer): CustomerForm {
  return {
    ref: c.external_ref ?? "",
    name: c.name,
    email: c.email ?? "",
    phone: c.phone ?? "",
    fax: c.fax ?? "",
    bill_to: c.bill_to_address ?? "",
    ship_to_name: c.ship_to_name ?? "",
    ship_to: c.ship_to_address ?? "",
    vendor_no: c.vendor_no ?? "",
    group_id: c.group_id ?? "",
    terms: String(c.payment_terms_days),
    active: c.active,
  };
}

/** Field -> problem, empty when the form can be sent. `isNew` also checks the reference. */
export function formProblems(f: CustomerForm, isNew = false): Partial<Record<keyof CustomerForm, string>> {
  const out: Partial<Record<keyof CustomerForm, string>> = {};
  if (!f.name.trim()) out.name = "Enter the customer's name.";
  if (f.email.trim() && !EMAIL_RE.test(f.email.trim())) out.email = "Enter an email like accounts@example.com, or leave it empty.";
  if (!/^\d{1,3}$/.test(f.terms.trim()) || Number(f.terms) > 365) out.terms = "Enter whole days from 0 to 365.";
  if (isNew && f.ref.trim() && !REF_RE.test(f.ref.trim())) out.ref = "Use letters, numbers, dot, dash or underscore only.";
  return out;
}

export function buildCreate(f: CustomerForm): CustomerIn {
  return {
    external_ref: f.ref.trim() || null,
    name: f.name.trim(),
    email: f.email.trim() || null,
    phone: f.phone.trim() || null,
    fax: f.fax.trim() || null,
    bill_to_address: addressText(f.bill_to) || null,
    ship_to_name: f.ship_to_name.trim() || null,
    ship_to_address: addressText(f.ship_to) || null,
    vendor_no: f.vendor_no.trim() || null,
    group_id: f.group_id || null,
    payment_terms_days: Number(f.terms.trim()),
  };
}

/** Only the fields that changed, or null when nothing did. Empty email/phone clears them. */
export function buildPatch(c: Customer, f: CustomerForm): CustomerPatch | null {
  const p: CustomerPatch = { version: c.version };
  if (f.name.trim() !== c.name) p.name = f.name.trim();
  if (f.email.trim() !== (c.email ?? "")) p.email = f.email.trim();
  if (f.phone.trim() !== (c.phone ?? "")) p.phone = f.phone.trim();
  for (const k of TEXT) if (f[k].trim() !== (c[k] ?? "")) p[k] = f[k].trim();
  for (const [k, api] of ADDRESS) if (addressText(f[k]) !== (c[api] ?? "")) p[api] = addressText(f[k]);
  if (f.group_id !== (c.group_id ?? "")) {
    if (f.group_id) p.group_id = f.group_id;
    else p.clear_group = true;
  }
  if (Number(f.terms.trim()) !== c.payment_terms_days) p.payment_terms_days = Number(f.terms.trim());
  if (f.active !== c.active) p.active = f.active;
  return Object.keys(p).length > 1 ? p : null;
}

export function customerMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return "Something went wrong. Try again.";
  switch (err.code) {
    case "VERSION_CONFLICT":
      return "Someone else changed this customer. The latest details are loaded — check them and save again.";
    case "DUPLICATE":
      return "Another customer already uses this reference, or a group already has this code.";
    case "INVALID_GROUP":
      return "That customer group no longer exists. Choose another.";
    case "FORBIDDEN":
      return "You don't have permission to change customers.";
    case "MFA_REQUIRED":
      return "Verify two-step authentication to change customers.";
    case "VALIDATION":
      return "Check the fields: name is required, email must be a valid address, terms are 0–365 days.";
    case "NOT_FOUND":
      return "This customer doesn't exist.";
    default:
      return err.message;
  }
}
