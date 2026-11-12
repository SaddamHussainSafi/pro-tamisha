// One place that turns any failure into plain words for the people using Pro Trading:
// what happened, whether anything was saved, and what to do next.
// Catalog of every code: docs/operations/ERROR_CATALOG.md. Feature helpers (receivingMessage,
// paymentMessage, ...) handle their own codes first and fall back to `errorText` here.
import { ApiError } from "../api";

/** The next step the screen offers. */
export type ErrorAction =
  | "retry" // try the same thing again
  | "reload" // reload the page to see the latest (someone else changed it)
  | "signin" // sign in again (the screen stays where it is)
  | "fix" // correct what was typed or chosen
  | "admin" // ask an owner/admin for access
  | "wait" // wait a moment, then try again
  | "check"; // check whether it went through before trying again

export type ErrorInfo = {
  status: number;
  code: string;
  /** Short headline, e.g. "You're offline". */
  title: string;
  /** What happened, in plain words. */
  message: string;
  /** Whether anything was saved; null when nothing was being saved (loading a page). */
  saved: string | null;
  /** What to do next. */
  next: string;
  action: ErrorAction;
  /** Reference to quote to support (server request id), when there is one. */
  requestId: string | null;
  /** Inputs to highlight (validation errors): field name → problem. */
  fields: Record<string, string>;
};

const NOTHING_SAVED = "Nothing was saved.";
const MAYBE_SAVED = "We couldn't confirm whether it was saved — check the list before trying again, so it isn't entered twice.";
const ADMIN = "Ask an owner or admin to give you access.";

type Generic = { title: string; message: string; next: string; action: ErrorAction; saved?: "nothing" | "maybe" | "none" };

/** Codes that mean the same thing on every screen. Their server text is replaced by these. */
const G = {
  OFFLINE: { title: "You're offline", message: "This computer isn't connected to the internet.", next: "Reconnect, then try again. What you typed on this page is still here.", action: "retry", saved: "maybe" },
  NETWORK: { title: "Can't reach Pro Trading", message: "Pro Trading didn't answer — the connection dropped or the server is restarting.", next: "Check the connection and try again in a moment.", action: "retry", saved: "maybe" },
  TIMEOUT: { title: "This is taking too long", message: "Pro Trading didn't answer in time.", next: "Try again in a moment.", action: "retry", saved: "maybe" },
  ABORTED: { title: "Stopped", message: "The request was stopped before it finished.", next: "Try again.", action: "retry", saved: "maybe" },
  SERVER_UNREACHABLE: { title: "Pro Trading server isn't answering", message: "The server is down or restarting.", next: "Wait a minute and try again. If it lasts, tell whoever looks after the server.", action: "wait", saved: "maybe" },
  INTERNAL: { title: "Something went wrong on the server", message: "The server hit an unexpected problem.", next: "Try again. If it happens again, give support the reference below.", action: "retry", saved: "maybe" },
  HTTP_ERROR: { title: "That didn't work", message: "Pro Trading couldn't complete this.", next: "Try again. If it happens again, give support the reference below.", action: "retry", saved: "maybe" },
  BAD_REQUEST: { title: "That didn't work", message: "Pro Trading couldn't read what was sent.", next: "Reload the page and try again.", action: "reload", saved: "nothing" },
  DB_UNAVAILABLE: { title: "The database isn't answering", message: "The database is not answering right now.", next: "Wait a minute and try again.", action: "wait", saved: "nothing" },
  SERVICE_UNAVAILABLE: { title: "A service isn't answering", message: "A part of Pro Trading isn't answering right now.", next: "Wait a minute and try again.", action: "wait", saved: "nothing" },
  BUSY_RETRY: { title: "Busy — try again", message: "Someone else was saving the same records at the same moment.", next: "Try again.", action: "retry", saved: "nothing" },
  CONFLICT: { title: "Already exists", message: "This clashes with a record that already exists.", next: "Reload the page to see the latest, then try again.", action: "reload", saved: "nothing" },
  UNAUTHENTICATED: { title: "Signed out", message: "You're signed out.", next: "Sign in again — you'll come back to this page.", action: "signin", saved: "nothing" },
  SESSION_INVALID: { title: "Your sign-in has ended", message: "Your sign-in ended (it timed out, or you signed out somewhere else).", next: "Sign in again — you'll come back to this page.", action: "signin", saved: "nothing" },
  CSRF_FAILED: { title: "Your sign-in has ended", message: "This tab's sign-in check is out of date.", next: "Sign in again — you'll come back to this page.", action: "signin", saved: "nothing" },
  MFA_REQUIRED: { title: "Two-step code needed", message: "Enter your two-step sign-in code first.", next: "Sign in again and enter the code from your authenticator app.", action: "signin", saved: "nothing" },
  FORBIDDEN: { title: "No permission", message: "Your account doesn't have permission for this.", next: ADMIN, action: "admin", saved: "nothing" },
  ORIGIN_REJECTED: { title: "Blocked for safety", message: "This request didn't come from the Pro Trading page, so it was blocked.", next: "Open Pro Trading directly (not from a link in another site) and try again.", action: "reload", saved: "nothing" },
  NOT_FOUND: { title: "Not found", message: "This record doesn't exist any more — it may have been deleted or moved to Trash.", next: "Go back to the list and open it again.", action: "reload", saved: "nothing" },
  METHOD_NOT_ALLOWED: { title: "Not available", message: "That action isn't available here.", next: "Reload the page; if it keeps happening, tell support.", action: "reload", saved: "nothing" },
  VERSION_CONFLICT: { title: "Changed by someone else", message: "Someone else changed this while you were working on it.", next: "Reload to see their change, then make yours again.", action: "reload", saved: "nothing" },
  VALIDATION: { title: "Check the highlighted fields", message: "Some details are missing or not valid.", next: "Fix the highlighted fields and try again.", action: "fix", saved: "nothing" },
  IDEMPOTENCY_IN_PROGRESS: { title: "Still working", message: "Your first click is still being processed.", next: "Wait a few seconds, then reload the page to see the result.", action: "check", saved: "maybe" },
  IDEMPOTENCY_KEY_REUSED: { title: "Already sent", message: "This was already sent with different details.", next: "Reload the page and check before trying again.", action: "check", saved: "maybe" },
  PAYLOAD_TOO_LARGE: { title: "Too large", message: "This is too large to send in one go.", next: "Use a smaller file, or split it into parts.", action: "fix", saved: "nothing" },
  FILE_TOO_LARGE: { title: "File too large", message: "The file is too large.", next: "Use a smaller file (scan at a lower resolution, or split the PDF).", action: "fix", saved: "nothing" },
  UNSUPPORTED_MEDIA_TYPE: { title: "Wrong file type", message: "That type of file isn't accepted here.", next: "Use a PDF, JPEG or PNG.", action: "fix", saved: "nothing" },
  RATE_LIMITED: { title: "Too many at once", message: "Too many requests in a short time.", next: "Wait a moment and try again.", action: "wait", saved: "nothing" },
  TOO_MANY_REQUESTS: { title: "Too many at once", message: "Too many requests in a short time.", next: "Wait a moment and try again.", action: "wait", saved: "nothing" },
  TOO_MANY_ATTEMPTS: { title: "Too many attempts", message: "Too many attempts in a short time.", next: "Wait a few minutes and try again.", action: "wait", saved: "nothing" },
  ACCOUNT_LOCKED: { title: "Account locked for now", message: "Too many failed sign-ins, so this account is locked for a while.", next: "Wait 15 minutes, then try again.", action: "wait", saved: "nothing" },
  CLIENT: { title: "This screen had a problem", message: "Something unexpected went wrong on this screen.", next: "Reload the page to check whether your last change was saved, then try again.", action: "reload", saved: "maybe" },
} satisfies Record<string, Generic>;
const GENERIC: Record<string, Generic | undefined> = G;
/** Codes with a shared meaning on every screen (for tests and the catalog). */
export const GENERIC_CODES = Object.keys(G);

/** Server texts that are framework defaults, not written for people: never shown as-is. */
const TECHNICAL = /traceback|exception|sqlalchemy|psycopg|undefined|null|NaN|\bat \w+\.\w+ \(|^[A-Z_]+$|^(Bad Request|Not Found|Internal Server Error|Bad Gateway|Service Unavailable|Gateway Timeout|Forbidden|Unauthorized|Method Not Allowed|Unprocessable (Entity|Content)|Conflict)$|must be a UUID|Idempotency-Key|CSRF/i;

/** True when a server message is fine to show to people as written. */
export function isPlain(message: string | undefined | null): message is string {
  return !!message && message.trim().length > 0 && message.length < 400 && !TECHNICAL.test(message.trim());
}

const WRITE = /^(POST|PUT|PATCH|DELETE)$/i;

function byStatus(status: number): Generic {
  if (status === 0) return G.NETWORK;
  if (status === 401) return G.UNAUTHENTICATED;
  if (status === 403) return G.FORBIDDEN;
  if (status === 404) return G.NOT_FOUND;
  if (status === 409) return { title: "Can't do that now", message: "This can't be done in its current state.", next: "Reload the page to see the latest, then try again.", action: "reload", saved: "nothing" };
  if (status === 413) return G.PAYLOAD_TOO_LARGE;
  if (status === 415) return G.UNSUPPORTED_MEDIA_TYPE;
  if (status === 422 || status === 400) return { ...G.VALIDATION, title: "Check what was entered" };
  if (status === 423) return G.ACCOUNT_LOCKED;
  if (status === 429) return G.RATE_LIMITED;
  if (status === 502 || status === 504) return G.SERVER_UNREACHABLE;
  if (status === 503) return G.SERVICE_UNAVAILABLE;
  if (status >= 500) return G.INTERNAL;
  return G.HTTP_ERROR;
}

function savedText(kind: Generic["saved"], isWrite: boolean): string | null {
  if (!isWrite) return null; // loading a page saves nothing; don't talk about saving
  if (kind === "maybe") return MAYBE_SAVED;
  if (kind === "nothing") return NOTHING_SAVED;
  return null;
}

function fieldsOf(extra: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {};
  const list = Array.isArray(extra.fields) ? extra.fields : [];
  for (const f of list as { field?: unknown; loc?: unknown; msg?: unknown }[]) {
    const name = typeof f.field === "string" ? f.field : Array.isArray(f.loc) ? String(f.loc[f.loc.length - 1] ?? "") : "";
    if (name && typeof f.msg === "string") out[name] ??= f.msg.replace(/^Value error, /, "");
  }
  return out;
}

/** Everything a screen needs to show about a failure. Accepts anything (ApiError, Error, string). */
export function describeError(err: unknown): ErrorInfo {
  if (!(err instanceof ApiError)) {
    const offline = typeof navigator !== "undefined" && navigator.onLine === false;
    const g = offline ? G.OFFLINE : err instanceof TypeError && /fetch|network|load failed/i.test(err.message) ? G.NETWORK : G.CLIENT;
    return { status: 0, code: offline ? "OFFLINE" : g === G.NETWORK ? "NETWORK" : "CLIENT", title: g.title, message: g.message, saved: null, next: g.next, action: g.action, requestId: null, fields: {} };
  }
  const isWrite = WRITE.test(err.method ?? "");
  const known = GENERIC[err.code];
  const base = known ?? byStatus(err.status);
  // Feature codes (LOT_NOT_SALEABLE, NO_PRICE...) carry a message written for people: keep it.
  const message = known ? base.message : isPlain(err.message) ? err.message : base.message;
  const requestId = err.requestId;
  return {
    status: err.status,
    code: err.code,
    title: base.title,
    message,
    saved: savedText(base.saved, isWrite),
    next: known || !isPlain(err.message) ? base.next : err.status >= 500 ? base.next : "",
    action: base.action,
    requestId,
    fields: fieldsOf(err.extra),
  };
}

/** Short support reference: the first 8 characters of the request id. */
export const shortRef = (id: string | null | undefined) => (id ? id.replace(/-/g, "").slice(0, 8) : "");

/** One plain paragraph: what happened, whether anything was saved, what to do next, and (for
 * server problems) the support reference. Used where a screen shows a single line of text. */
export function errorText(err: unknown): string {
  const i = describeError(err);
  const ref = (i.status >= 500 || i.code === "HTTP_ERROR") && i.requestId ? `Reference: ${shortRef(i.requestId)}.` : "";
  const parts = [i.message, i.saved, i.next, ref].filter((s): s is string => !!s).map((s) => s.trim());
  if (parts.length === 1) return parts[0] ?? "";
  return parts.map((s) => (/[.!?]$/.test(s) ? s : `${s}.`)).join(" ");
}

/** True when the error means the user must sign in again (handled by the session dialog). */
export const needsSignIn = (err: unknown) => describeError(err).action === "signin";

const CHUNK = /dynamically imported module|Importing a module script failed|error loading dynamically imported module|ChunkLoadError|Loading chunk [\w-]+ failed/i;
/** A screen's code file failed to download: an update was installed, or the connection dropped. */
export const isChunkError = (e: unknown) => e instanceof Error && (e.name === "ChunkLoadError" || CHUNK.test(e.message));

/**
 * After a version conflict: the latest saved values, with the fields this person changed put
 * back on top, so nothing they typed is lost. They review and save again (the save is still
 * checked against the new version, so nobody's change is overwritten without being seen).
 */
export function keepEdits<T extends Record<string, unknown>>(original: T, edited: T, latest: T): T {
  const out: Record<string, unknown> = { ...latest };
  for (const k of Object.keys(edited)) {
    if (JSON.stringify(edited[k]) !== JSON.stringify(original[k])) out[k] = edited[k];
  }
  return out as T;
}
