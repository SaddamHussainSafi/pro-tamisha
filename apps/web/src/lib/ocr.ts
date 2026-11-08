import { ApiError, type OcrField, type OcrFieldStatus, type OcrResolution, type OcrStatus } from "../api";
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
};
export const warningText = (code: string) => WARNING[code] ?? code;

/** Only clear readings may be confirmed as read; anything unclear needs a person to enter it. */
export const canConfirm = (f: Pick<OcrField, "status" | "read_product_id" | "read_qty">) =>
  f.status === "OK" && !!f.read_product_id && !!f.read_qty;

/** A reason is required when overriding a clear reading (it counts against accuracy). */
export const needsReason = (f: Pick<OcrField, "status" | "kind">, action: "CORRECT" | "REMOVE") =>
  f.status === "OK" && !(action === "REMOVE" && f.kind === "MANUAL");

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
  if (!(err instanceof ApiError)) return "Something went wrong. Nothing was saved — try again.";
  switch (err.code) {
    case "FIELDS_UNRESOLVED": {
      const ls = (err.extra.open_lines as number[] | undefined) ?? [];
      return `Review every line first${ls.length ? ` (still open: ${ls.join(", ")})` : ""}.`;
    }
    case "NEEDS_CORRECTION":
      return "This line wasn't read clearly. Enter what the sheet says, or remove it.";
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
      return err.message || "Something went wrong. Nothing was saved — try again.";
  }
}
