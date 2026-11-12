import { dec, toFixed } from "./decimal";
import { ApiError } from "../api";
import { errorText } from "./errors";

/** Plain-language messages for pricing API errors. */
export function pricingMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return errorText(err);
  switch (err.code) {
    case "VERSION_CONFLICT":
      return "Someone else changed this price book. The latest version has been loaded — review it and retry.";
    case "INVALID_STATE":
      return "This price book is published and can't be changed. Start a new draft instead.";
    case "DUPLICATE_UPLOAD":
      return "This exact price list was already uploaded. Open the existing version instead.";
    case "DUPLICATE":
      return "That version code is already used, or another price book starts at that exact time.";
    case "NO_CHANGES":
      return "These prices are identical to the latest published price book — nothing to publish.";
    case "EMPTY_VERSION":
      return "Add at least one price before activating.";
    case "EFFECTIVE_IN_PAST":
      return "The start time can't be in the past. Issued invoices keep the price they were issued with.";
    case "CSV_INVALID":
      return `The file has ${Number(err.extra.error_count ?? 0)} problem(s). Nothing was saved — fix the rows below and try again.`;
    case "NO_PRICE":
      return "No price is set for that product and unit.";
    case "FORBIDDEN":
      return "You don't have permission for this action.";
    case "MFA_REQUIRED":
      return "Verify two-step authentication to perform this action.";
    case "VALIDATION":
      return "Check the highlighted fields.";
    default:
      return errorText(err);
  }
}

export const scopeLabel = (scope: string, target: string | null) =>
  scope === "STANDARD" ? "Standard" : scope === "GROUP" ? `Group · ${target ?? "?"}` : `Customer · ${target ?? "?"}`;

/** Rates are 4 dp on the server; show at least 2, trailing zeros trimmed. */
export function rateText(s: string): string {
  const f = toFixed(dec(s), 4).replace(/(\.\d\d\d?)0+$/, "$1").replace(/(\.\d\d)0$/, "$1");
  return `CAD ${f}`;
}
