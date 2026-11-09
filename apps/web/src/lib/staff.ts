import { ApiError, type RoleCode, type SecurityEvent, type StaffMember, type StaffStatus } from "../api";

// User management helpers. The server re-checks every rule; these only shape the screens.

/** The six roles (owner, 2026-11-09). Codes are stable; labels are what people see. */
export const ROLES: { code: RoleCode; label: string; hint: string }[] = [
  { code: "OWNER_ADMIN", label: "Super Admin", hint: "Full access, including users, roles, sessions and security settings." },
  { code: "MANAGER", label: "Admin", hint: "Day-to-day operations: products, prices, orders, stock, receiving and reports. No user management." },
  { code: "SALES_OPERATOR", label: "Sales", hint: "Sales orders, invoice drafts and customer records; records payments customers report." },
  { code: "WAREHOUSE", label: "Warehouse", hint: "Picking, receiving counts and stock counts." },
  { code: "FINANCE", label: "Accounting", hint: "Payments, receivables, supplier ledgers and financial reports." },
  { code: "VIEWER", label: "Viewer", hint: "Read-only: products, customers, orders and stock." },
];
export const roleLabel = (code: string) => ROLES.find((r) => r.code === code)?.label ?? code;
export const SUPER_ADMIN: RoleCode = "OWNER_ADMIN";

export const STATUS_LABEL: Record<StaffStatus, string> = { ACTIVE: "Active", INVITED: "Invited", SUSPENDED: "Suspended" };

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

export type StaffFilter = { q: string; role: RoleCode | "ALL"; status: StaffStatus | "ALL" };

/** Search by name or email, then filter by role and status. */
export function filterStaff(rows: StaffMember[], f: StaffFilter): StaffMember[] {
  const q = f.q.trim().toLowerCase();
  return rows.filter(
    (u) =>
      (!q || u.display_name.toLowerCase().includes(q) || u.email.toLowerCase().includes(q)) &&
      (f.role === "ALL" || u.roles.includes(f.role)) &&
      (f.status === "ALL" || u.status === f.status),
  );
}

/** Area of a permission code, for grouping the matrix and the permission summary. */
const AREAS: [prefix: string, area: string][] = [
  ["employee", "Administration"],
  ["company", "Administration"],
  ["feature_flag", "Administration"],
  ["audit", "Administration"],
  ["warehouse", "Warehouse"],
  ["catalog", "Products & customers"],
  ["price", "Prices"],
  ["sales_order", "Orders & picking"],
  ["pick", "Orders & picking"],
  ["invoice", "Invoices"],
  ["inventory", "Stock & receiving"],
  ["grn", "Stock & receiving"],
  ["scan", "Scans & documents"],
  ["document", "Scans & documents"],
  ["payment", "Payments & suppliers"],
  ["supplier", "Payments & suppliers"],
  ["report", "Reports"],
  ["order_connect", "Website orders"],
];
export function permissionArea(code: string): string {
  const hit = AREAS.find(([p]) => code === p || code.startsWith(p + ".") || code.startsWith(p + "_"));
  return hit?.[1] ?? "Other";
}

/** Permissions grouped by area, areas in a fixed order. */
export function groupPermissions<T extends { code: string }>(perms: T[]): { area: string; items: T[] }[] {
  const order = [...new Set(AREAS.map(([, a]) => a)), "Other"];
  const by = new Map<string, T[]>();
  for (const p of perms) {
    const a = permissionArea(p.code);
    by.set(a, [...(by.get(a) ?? []), p]);
  }
  return order.filter((a) => by.has(a)).map((area) => ({ area, items: by.get(area) ?? [] }));
}

/** "Chrome on macOS" from a user agent string (best effort, for session lists). */
export function deviceLabel(ua: string | null | undefined): string {
  if (!ua) return "Unknown device";
  const browser = /Edg\//.test(ua)
    ? "Edge"
    : /Firefox\//.test(ua)
      ? "Firefox"
      : /Chrome\//.test(ua)
        ? "Chrome"
        : /Safari\//.test(ua)
          ? "Safari"
          : /python|curl|testclient/i.test(ua)
            ? "Script"
            : "Browser";
  const os = /iPhone|iPad/.test(ua)
    ? "iOS"
    : /Android/.test(ua)
      ? "Android"
      : /Mac OS X|Macintosh/.test(ua)
        ? "macOS"
        : /Windows/.test(ua)
          ? "Windows"
          : /Linux/.test(ua)
            ? "Linux"
            : "";
  return os ? `${browser} on ${os}` : browser;
}

const REASONS: Record<string, string> = {
  bad_password: "wrong password",
  unknown_email: "unknown email",
  inactive: "account suspended",
  locked: "account locked",
  locked_now: "too many tries, account locked",
  invitation_pending: "invitation not accepted yet",
  ip_throttled: "too many tries from this network",
  per_account_limit: "too many requests",
  bad_link: "invalid or expired link",
};

const ACTIONS: Record<string, string> = {
  "auth.login": "Signed in",
  "auth.dev_login": "Signed in (development)",
  "auth.logout": "Signed out",
  "auth.mfa_verify": "Two-step code",
  "auth.mfa_locked": "Two-step locked after wrong codes",
  "auth.mfa_enabled": "Turned two-step sign-in on",
  "auth.mfa_disabled": "Turned two-step sign-in off",
  "auth.mfa_recovery_used": "Used a recovery code",
  "auth.mfa_recovery_regenerated": "Made new recovery codes",
  "auth.reauth": "Password check",
  "auth.password_reset_requested": "Asked for a password reset link",
  "auth.password_reset": "Set a new password from a reset link",
  "auth.invitation_accepted": "Accepted the invitation",
  "auth.session_revoked": "Signed out a session",
  "auth.sessions_revoked": "Signed out everywhere",
  "auth.link_throttled": "Too many link attempts",
  "employee.password_change": "Changed password",
  "employee.create": "Invited a user",
  "employee.invite": "Resent an invitation",
  "employee.update": "Changed a user",
  "employee.password_reset": "Set a temporary password",
  "employee.mfa_reset": "Turned off someone's two-step sign-in",
  "feature_flag.set": "Changed a setting",
  "permission.denied": "Refused: no permission",
};

/** Plain words for a security event, e.g. "Signed in — failed: wrong password". */
export function eventLabel(e: Pick<SecurityEvent, "action" | "success" | "reason">): string {
  const base = ACTIONS[e.action] ?? e.action;
  if (e.success) return base;
  const why = e.reason ? REASONS[e.reason] ?? e.reason.replace(/_/g, " ") : null;
  return why ? `${base} — failed: ${why}` : `${base} — failed`;
}

/** The token from a link like /reset-password#token=… (the fragment never reaches a server). */
export function tokenFromHash(hash: string): string | null {
  const m = /(?:^#|&)token=([A-Za-z0-9_-]{20,128})(?:&|$)/.exec(hash);
  return m?.[1] ?? null;
}

export const when = (iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "Never";

export function staffMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return "Something went wrong. Check your connection and try again.";
  switch (err.code) {
    case "EMAIL_TAKEN":
      return "Someone already signs in with this email.";
    case "SELF_CHANGE":
      return "You can't do that to your own account here. Use My account, or ask another Super Admin.";
    case "LAST_OWNER":
      return "Keep at least one active Super Admin.";
    case "WEAK_PASSWORD":
      return "Choose a less guessable password (at least 12 characters, not your email).";
    case "WRONG_PASSWORD":
      return "Your password is not right.";
    case "SAME_PASSWORD":
      return "The new password must be different.";
    case "FORBIDDEN":
      return "Only a Super Admin can do this.";
    case "MFA_REQUIRED":
      return "Enter your two-step sign-in code first.";
    case "MFA_INVALID":
      return "That code is wrong or was already used.";
    case "TOO_MANY_ATTEMPTS":
      return "Too many attempts. Wait a few minutes and try again.";
    case "ACCOUNT_LOCKED":
      return "Too many failed attempts. The account is locked for a few minutes.";
    case "TOKEN_INVALID":
      return "This link is invalid, already used or expired. Ask for a new one.";
    case "ALREADY_ACCEPTED":
      return "They already set a password.";
    case "SUSPENDED":
      return "Reactivate them first.";
    case "TWO_STEP_REQUIRED":
      return "Your company requires two-step sign-in, so it can't be turned off.";
    case "CURRENT_SESSION":
      return "That is this session. Use Sign out instead.";
    case "PASSWORD_REQUIRED":
      return "Enter your password.";
    case "VALIDATION":
      return "Check the fields: a name, a valid email and a role.";
    default:
      return err.message;
  }
}
