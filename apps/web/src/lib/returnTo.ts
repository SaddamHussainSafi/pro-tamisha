// After sign-in (and two-step), go back to the page the user opened, not always to Overview.
// Only in-app paths are accepted: never another site ("//host", "https:"), never the sign-in pages.

export type ReturnState = { from?: string } | null;

export function returnTo(state: unknown): string {
  const from = (state as { from?: unknown } | null)?.from;
  if (typeof from !== "string" || !from.startsWith("/") || from.startsWith("//") || from.startsWith("/\\")) return "/";
  if (/^\/(login|mfa)(\/|\?|$)/.test(from)) return "/";
  return from;
}
