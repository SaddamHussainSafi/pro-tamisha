import { ApiError, type RoleCode } from "../api";

// Settings › Users helpers. The server re-checks every rule.

export const ROLES: { code: RoleCode; label: string; hint: string }[] = [
  { code: "OWNER_ADMIN", label: "Owner / Admin", hint: "Everything, including users and settings. Two-step sign-in required." },
  { code: "MANAGER", label: "Manager", hint: "Sales, prices, stock, receiving and reports. Two-step sign-in required." },
  { code: "SALES_OPERATOR", label: "Sales", hint: "Orders, invoices drafts, customers, recording reported payments." },
  { code: "WAREHOUSE", label: "Warehouse", hint: "Picking, counts and receiving counts." },
  { code: "FINANCE", label: "Finance", hint: "Payments, receivables and reports. Two-step sign-in required." },
];
export const roleLabel = (code: string) => ROLES.find((r) => r.code === code)?.label ?? code;

/** Same rule as the server: 12–128 characters, not the email, not one repeated character. */
export function passwordProblem(pw: string, email = ""): string | null {
  if (pw.length < 12) return "Use at least 12 characters.";
  if (pw.length > 128) return "Use at most 128 characters.";
  if (email && pw.trim().toLowerCase() === email.trim().toLowerCase()) return "Don't use the email as the password.";
  if (new Set(pw).size < 4) return "Choose a less guessable password.";
  return null;
}

const ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789-";

/** Random temporary password (16 characters, no look-alike letters), from the browser's CSPRNG. */
export function tempPassword(rand: (n: number) => Uint32Array = (n) => crypto.getRandomValues(new Uint32Array(n))): string {
  return Array.from(rand(16), (x) => ALPHABET[x % ALPHABET.length]).join("");
}

export function staffMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return "Something went wrong. Try again.";
  switch (err.code) {
    case "EMAIL_TAKEN":
      return "Someone already signs in with this email.";
    case "SELF_CHANGE":
      return "You can't do that to your own account here. Use My account, or ask another Owner / Admin.";
    case "LAST_OWNER":
      return "Keep at least one active Owner / Admin.";
    case "WEAK_PASSWORD":
      return "Choose a less guessable password.";
    case "WRONG_PASSWORD":
      return "Your current password is not right.";
    case "SAME_PASSWORD":
      return "The new password must be different.";
    case "FORBIDDEN":
      return "Only an Owner / Admin can manage users.";
    case "MFA_REQUIRED":
      return "Verify two-step sign-in first.";
    case "VALIDATION":
      return "Check the fields: a name, a valid email and a password of at least 12 characters.";
    default:
      return err.message;
  }
}
