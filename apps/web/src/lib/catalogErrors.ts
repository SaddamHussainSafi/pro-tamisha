import { ApiError } from "../api";
import { errorText } from "./errors";

/** Plain-language messages for catalog API errors (409 conflicts, unknown conversions, etc.). */
export function catalogMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return errorText(err);
  switch (err.code) {
    case "VERSION_CONFLICT":
      return "Someone else changed this product. The latest version has been loaded — review it and retry.";
    case "DUPLICATE":
      return "That SKU, profile code or alias is already used.";
    case "UNKNOWN_CONVERSION":
      return `No known weight for group ${Number(err.extra.group ?? 0) + 1}. Enter a case weight or pick a profile with a weight.`;
    case "INVALID_QUANTITY":
      return `Group ${Number(err.extra.group ?? 0) + 1}: boxes and pieces must be whole numbers.`;
    case "INVALID_PROFILE":
    case "INVALID_CATEGORY":
      return err.message;
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
