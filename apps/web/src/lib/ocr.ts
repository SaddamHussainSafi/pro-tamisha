import { ApiError, type OcrEngine, type OcrField, type OcrFieldStatus, type OcrLineDetail, type OcrResolution, type OcrStatus } from "../api";
import { errorText } from "./errors";
import type { Tone } from "../ui/primitives";

export const OCR_STATUS: Record<OcrStatus, { tone: Tone; label: string }> = {
  QUEUED: { tone: "info", label: "Reading…" },
  NEEDS_REVIEW: { tone: "warning", label: "Needs review" },
  FAILED: { tone: "danger", label: "Couldn't read" },
  ACCEPTED: { tone: "success", label: "Order drafted" },
  REJECTED: { tone: "neutral", label: "Rejected" },
  SUPERSEDED: { tone: "neutral", label: "Re-read" },
};

export const FIELD_STATUS: Record<OcrFieldStatus, { tone: Tone; label: string; hint: string }> = {
  OK: { tone: "success", label: "Clear", hint: "Read clearly. Check it against the scan." },
  LOW_CONFIDENCE: { tone: "warning", label: "Unclear", hint: "The handwriting was hard to read. Enter what the sheet says." },
  UNREADABLE: { tone: "danger", label: "Unreadable", hint: "No quantity could be read. Enter it from the sheet, or remove the line." },
  UNMAPPED: { tone: "danger", label: "Unknown product", hint: "No product matches what was written. Pick the product, or remove the line." },
};

export const RESOLUTION: Record<OcrResolution, { tone: Tone; label: string }> = {
  NONE: { tone: "warning", label: "To review" },
  CONFIRMED: { tone: "success", label: "Confirmed" },
  CORRECTED: { tone: "info", label: "Corrected" },
  REMOVED: { tone: "neutral", label: "Removed" },
};

const WARNING: Record<string, string> = {
  NO_TEXT: "No text was found (a photo or a blank page). Add the lines by hand from the scan.",
  NO_TEMPLATE: "This doesn't look like our order sheet, so lines were guessed from the text. Check every line.",
  CUSTOMER_NOT_FOUND: "The customer reference on the sheet didn't match a customer. Choose the customer.",
  NO_LINES: "No order lines were found. Add them by hand, or reject the scan.",
  OCR_DISABLED: "Automatic reading is switched off. Add the lines by hand from the scan.",
  SUPPLIER_NOT_FOUND: "The supplier isn't one of yours yet. Choose the supplier before sending to Receiving.",
  PACKET_TOTAL_MISMATCH: "The total printed on the invoice or CFIA form doesn't match the lines. Check the amounts.",
  PACKET_INVOICE_MISMATCH: "The vendor invoice and the CFIA Confirmation of Sale don't agree. Check the lines.",
};
Object.assign(WARNING, {
  CUSTOMER_IS_SUPPLIER: "Only our own name is on the sheet, and we are the seller. Choose the customer.",
  CUSTOMER_NOT_READ: "No customer name could be read. Choose the customer.",
  OWN_DOCUMENT: "This is one of our own invoices or packing lists, so the order may already exist. Check before making a new one.",
  SLOW_READING: "This reading took longer than usual.",
});
const WHY: Record<string, string> = {
  GEMINI_CONSENT_NEEDED: "sending scans to Google has not been agreed yet",
  GEMINI_NOT_CONFIGURED: "the online reader is not set up on the server",
  GEMINI_DAILY_CAP: "today's free allowance is used up",
  GEMINI_QUOTA: "Google's daily allowance is used up",
  GEMINI_RATE_LIMIT: "Google asked to slow down",
  GEMINI_CIRCUIT_OPEN: "the online reader is paused after repeated failures",
  GEMINI_UNAVAILABLE: "Google could not be reached",
  GEMINI_BAD_JSON: "the online reader's answer was not valid",
  GEMINI_NO_ANSWER: "the online reader gave no answer",
  GEMINI_REJECTED: "Google refused the request",
  GEMINI_TOO_LARGE: "the page is too large to send",
};
const DOC: Record<string, string> = {
  CUSTOMER_ORDER: "a customer order",
  VENDOR_RECEIVING: "a supplier document",
  AIRWAY_BILL: "an airway bill",
  SALES_INVOICE: "one of our own invoices or packing lists",
  UNKNOWN_DOCUMENT: "a document the reader doesn't know",
};
const HEADER_NAME: Record<string, string> = { document_no: "document number", awb_no: "airway bill number", document_date: "date", due_date: "due date", supplier: "supplier name" };
const pagesOf = (a: string) => a.replace(/^p/, "page ");
const WITH_ARG: Record<string, (arg: string) => string> = {
  GEMINI_FALLBACK: (a) => {
    const [why = "", where] = a.split(":");
    return `The online reader wasn't used${where ? ` for ${pagesOf(where)}` : ""} (${WHY[why] ?? why}). The built-in reader was used instead, so check every line.`;
  },
  CUSTOMER_UNSURE: (a) => `The customer name reads “${a}”, but not clearly. Choose the customer.`,
  CUSTOMER_NOT_FOUND: (a) => `No customer is called “${a}”. Choose the customer.`,
  MIXED_ORDERS: (a) => `This file holds more than one order or store (${a.replace(/;/g, "; ").replace(/=/g, ": order ")}). Lines are listed by page: remove the ones that belong to another order.`,
  SAME_ORDER_NO: (a) => {
    const [no = "", scan = ""] = a.split(":");
    return `Order number ${no} is also on scan ${scan}. This may be a second copy of the same sheet: check before drafting another order.`;
  },
  ORDER_REF_MISSING: (a) => `No order number was found on ${pagesOf(a)}. Check it belongs with the rest.`,
  ORDER_REF_UNSURE: (a) => `The order number on ${pagesOf(a)} was read two ways. Check it.`,
  DOC_TYPE_UNSURE: (a) => `The reader was not sure what kind of document ${pagesOf(a)} is.`,
  DOC_TYPE_MISMATCH: (a) => `This looks like ${DOC[a] ?? a}, not what it was scanned as. Check before going on.`,
  TOTAL_DIFFERS: (a) => {
    const m = /written=([\d./]+),lines=(\d+),sum=([\d.]+)(?::p(\d+))?/.exec(a);
    if (!m) return "A number written on the sheet doesn't match the lines read.";
    const written = (m[1] ?? "").split("/").join(", ");
    return `Written in the margin of ${m[4] ? `page ${m[4]}` : "the sheet"}: ${written}. That doesn't match the ${m[2]} lines read (they add up to ${m[3]}). Look for a missed or extra line.`;
  },
  RECHECK_SKIPPED: () => "Unclear lines were not read a second time (today's allowance is nearly used up).",
  HEADER_DIFFERS: (a) => `A second check read the ${HEADER_NAME[a] ?? a.replace(/_/g, " ")} differently. Check it against the scan.`,
  LINE_COUNT_DIFFERS: (a) => {
    const m = /local=(\d+),\w+=(\d+)/.exec(a);
    return m ? `${m[2]} ${m[2] === "1" ? "line was" : "lines were"} read, but a second check counted ${m[1]}. Check against the scan that no line is missing or extra.` : "A second check counted a different number of lines. Check that no line is missing or extra.";
  },
  STAGE_A_SKIPPED: () => "The kind of document was not double-checked.",
};
export const warningText = (code: string) => {
  if (WARNING[code]) return WARNING[code];
  const i = code.indexOf(":");
  const fn = i > 0 ? WITH_ARG[code.slice(0, i)] : undefined;
  return fn ? fn(code.slice(i + 1)) : code;
};

const FLAG: Record<string, (arg: string) => string> = {
  NO_INK_SEEN: () => "No writing was found in this row's box: the number may belong to another row.",
  POSSIBLE_SHIFT: () => "The number may belong to the row above or below.",
  NEIGHBOUR_UNREAD: () => "There is writing in the next row that was not read: check which row this number belongs to.",
  UNSURE: () => "Hard to read.",
  FRACTION: () => "A fraction or decimal: check it.",
  LOCAL_READ_DIFFERS: (a) => `A second check read ${a}.`,
  RECHECK_DIFFERS: (a) => `A second look read ${a === "-" ? "nothing" : a}.`,
  GEMINI_SAW_NOTHING: () => "Found only by the second check.",
  DUPLICATE_ROW: () => "This row was read twice.",
  PRODUCT_GUESSED: () => "The product is a near match: check it.",
  UNIT_IN_QTY: () => "A unit was written with the quantity.",
  PKG_NUMBER: () => "A second number is written in the package box: check which one is the quantity.",
  AMOUNT_DIFFERS: () => "Quantity × price doesn't match the amount.",
};
/** What the handwriting reader saw on a line besides the quantity, in words. */
export function lineNotes(d: OcrLineDetail | null | undefined): string[] {
  if (!d) return [];
  const out: string[] = [];
  if (d.crossed_out) out.push(`Crossed out: ${d.crossed_out}`);
  if (d.no_mark) out.push("“NO” is written on this row");
  if (d.pkg) out.push(`Package: ${d.pkg}`);
  if (d.weight) out.push(`Weight: ${d.weight}`);
  if (d.case_size) out.push(`Case size: ${d.case_size}`);
  if (d.notes) out.push(`Notes: ${d.notes}`);
  if (d.supplied) out.push(`Supplied: ${d.supplied}`);
  if (d.unavailable) out.push(`Short: ${d.unavailable}`);
  if (d.checkmark) out.push("Ticked");
  for (const f of d.flags ?? []) {
    const i = f.indexOf(":");
    const fn = FLAG[i > 0 ? f.slice(0, i) : f];
    if (fn) out.push(fn(i > 0 ? f.slice(i + 1) : ""));
  }
  return out;
}

/** One line saying how long the reading took. No reader or model names (owner, 2026-11-12).
 * Empty for readings made before engines were recorded. */
export function engineText(e: OcrEngine | undefined): string {
  if (!e?.provider) return "";
  if (e.provider === "disabled") return "Not read: reading is turned off.";
  return typeof e.seconds === "number" ? `Read in ${Math.round(e.seconds)} s.` : "";
}

/** Only clear readings may be confirmed as read; anything unclear needs a person to enter it. */
export const canConfirm = (f: Pick<OcrField, "status" | "read_product_id" | "read_qty">) =>
  f.status === "OK" && !!f.read_product_id && !!f.read_qty;

/** A reason is required only to remove a clearly read line. Corrections need none (owner,
 * 2026-11-11): the correction itself is kept in the line's history. */
export const needsReason = (f: Pick<OcrField, "status" | "kind">, action: "CORRECT" | "REMOVE") =>
  action === "REMOVE" && f.status === "OK" && f.kind !== "MANUAL";

/** Lines the reviewer can tick and confirm together: clear readings still waiting. */
export const selectable = (f: Pick<OcrField, "status" | "read_product_id" | "read_qty" | "resolution">) => f.resolution === "NONE" && canConfirm(f);

/** Confidence "0.620" → 62 (integer percent, string maths only — no floats). */
export function confidencePct(c: string | null): number | undefined {
  if (c === null) return undefined;
  const m = /^(\d+)(?:\.(\d+))?$/.exec(c);
  if (!m) return undefined;
  const frac = ((m[2] ?? "") + "00").slice(0, 2);
  return Number(m[1]) * 100 + Number(frac);
}

/** "15.0000" → "15", "12.5000" → "12.5" (for editing in whole-unit inputs). */
export const trimQty = (q: string) => (q.includes(".") ? q.replace(/0+$/, "").replace(/\.$/, "") : q);

/** Lines still waiting for a decision, in sheet order. */
export const openLines = (fields: OcrField[]) => fields.filter((f) => f.resolution === "NONE").map((f) => f.seq);

/** "0.9512" → "95.1%" without floating point. */
export function pctText(ratio: string | null): string {
  if (ratio === null) return "—";
  const m = /^(\d+)(?:\.(\d{0,4}))?$/.exec(ratio);
  if (!m) return ratio;
  const d = ((m[2] ?? "") + "0000").slice(0, 4);
  const whole = Number(m[1]) * 100 + Number(d.slice(0, 2));
  return `${whole}.${d.slice(2, 3)}%`;
}

export function ocrMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return errorText(err);
  switch (err.code) {
    case "FIELDS_UNRESOLVED": {
      const ls = (err.extra.open_lines as number[] | undefined) ?? [];
      return `Review every line first${ls.length ? ` (still open: ${ls.join(", ")})` : ""}.`;
    }
    case "NEEDS_CORRECTION":
      return `${typeof err.extra.line === "number" ? `Line ${err.extra.line}` : "This line"} wasn't read clearly. Enter what the sheet says, or remove it.`;
    case "REASON_REQUIRED":
      return "Say why the reading is wrong (at least 3 characters).";
    case "INCOMPLETE_LINE":
      return "Choose a product, unit and quantity.";
    case "NO_LINES":
      return "There are no lines to put on an order. Add a line, or reject the scan.";
    case "VERSION_CONFLICT":
      return "Someone else changed this reading. It has been reloaded — check it and try again.";
    case "INVALID_STATE":
      return `${err.message}. Reload to see the latest.`;
    case "EXTRACTION_FINAL":
      return "This scan already has a finished review and can't be read again.";
    case "SCAN_NOT_RECEIVED":
      return "The scan hasn't been received yet.";
    case "FORBIDDEN":
      return "You don't have permission to do that here.";
    default:
      return errorText(err);
  }
}

/** A scanned line as written on the sheet, in words: “BREADFRUIT” · ticked (the quantity is already in its column). */
export function writtenText(raw: string): string {
  const parts = raw.split("|").map((p) => p.trim()).filter(Boolean);
  const [name, ...rest] = parts;
  const extra = rest.filter((p) => !/^q\s*:/i.test(p)).map((p) => (/^check ?mark$/i.test(p) ? "ticked" : p.toLowerCase() === "no" ? "“NO” written" : p));
  return [name ? `“${name}”` : "", ...extra].filter(Boolean).join(" · ");
}
