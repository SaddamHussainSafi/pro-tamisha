// Gmail Inbox endpoints (apps/api/src/pro_trading/routes/gmail.py) and the small pure helpers
// the inbox screens share. Nothing here ever holds a Google token: the browser only talks to
// our own API.
import { ApiError, apiRequest as request, type ScanJobType } from "../api";
import type { Tone } from "../ui/primitives";

export type AccountStatus = "CONNECTED" | "ERROR" | "REAUTH_REQUIRED" | "DISCONNECTED";
export type ProcessingState = "NOT_PROCESSED" | "RECEIVED" | "PROCESSING" | "NEEDS_REVIEW" | "PROCESSED" | "FAILED" | "REJECTED";
export type DateRange = "all" | "today" | "yesterday" | "7d" | "30d" | "custom";

export type GmailAccount = {
  id: string;
  email_address: string;
  status: AccountStatus;
  connected_at: string;
  connected_by_name: string | null;
  last_sync_at: string | null;
  last_error_code: string | null;
  last_error: string | null;
  last_error_at: string | null;
  watch_expires_at: string | null;
  last_push_at: string | null;
  backfill_done: boolean;
  version: number;
};
export type GmailStatus = {
  configured: boolean;
  push_configured: boolean;
  poll_seconds: number;
  scope: string;
  redirect_uri: string;
  account: GmailAccount | null;
  message_count: number;
  unread_count: number;
  can_manage: boolean;
  can_read_body: boolean;
  can_preview: boolean;
  can_download: boolean;
  can_process: boolean;
  can_reprocess: boolean;
};
export type GmailAttachment = {
  id: string;
  message_id: string;
  filename: string;
  mime_type: string;
  byte_size: number;
  inline: boolean;
  eligible: boolean;
  unsupported_reason: string | null;
  page_count: number | null;
  sha256: string | null;
  state: ProcessingState;
};
export type GmailMessage = {
  id: string;
  from_name: string;
  from_email: string;
  to_addr: string;
  subject: string;
  snippet: string;
  received_at: string;
  read: boolean;
  labels: string[];
  attachment_count: number;
  pdf_count: number;
  removed: boolean;
  state: ProcessingState;
  attachments: GmailAttachment[];
};
export type GmailMessageDetail = GmailMessage & { thread_id: string; body_visible: boolean; body_text: string; body_html: string; body_truncated: boolean };
export type GmailPage = { items: GmailMessage[]; next_cursor: string | null; total: number };
export type HistoryEntry = {
  job_id: string | null;
  attempt_no: number | null;
  source: "gmail" | "scan";
  same_attachment: boolean;
  reprocess: boolean;
  document_category: string;
  processed_at: string;
  processed_by_name: string | null;
  scan_job_id: string;
  scan_job_no: string;
  extraction_id: string | null;
  status: ProcessingState;
  error_code: string | null;
  shipment_id: string | null;
  sales_order_id: string | null;
  order_no: string | null;
  order_status: string | null;
};
export type GmailAttachmentDetail = GmailAttachment & { message_subject: string; message_from: string; received_at: string; history: HistoryEntry[] };
export type ProcessOut = { job_id: string; attempt_no: number; attachment_id: string; scan_job_id: string; scan_job_no: string; extraction_id: string; extraction_status: string; review_path: string };
export type GmailCategory = Exclude<ScanJobType, "OTHER">;

/** What the filter row holds. `show` is one of the document chips (only one at a time). */
export type Filters = { q: string; range: DateRange; from: string; to: string; show: "all" | "attachments" | "pdf" | "processed" | "not_processed" };
export const NO_FILTERS: Filters = { q: "", range: "all", from: "", to: "", show: "all" };

/** Query string for GET /gmail/messages from the filter row. */
export function listQuery(f: Filters, cursor?: string | null, limit = 30): string {
  const p = new URLSearchParams();
  if (f.q.trim()) p.set("q", f.q.trim());
  if (f.range !== "all") p.set("range", f.range);
  if (f.range === "custom") {
    if (f.from) p.set("date_from", f.from);
    if (f.to) p.set("date_to", f.to);
  }
  if (f.show === "attachments") p.set("has_attachments", "true");
  if (f.show === "pdf") p.set("pdf", "true");
  if (f.show === "processed") p.set("processed", "true");
  if (f.show === "not_processed") p.set("processed", "false");
  if (cursor) p.set("cursor", cursor);
  p.set("limit", String(limit));
  return p.toString();
}

export const gmail = {
  status: () => request<GmailStatus>("GET", "/gmail/status"),
  start: () => request<{ authorization_url: string }>("POST", "/gmail/oauth/start"),
  complete: (code: string, state: string) => request<GmailAccount>("POST", "/gmail/oauth/complete", { code, state }, true),
  disconnect: (version: number) => request<GmailAccount>("POST", "/gmail/disconnect", { version }, true),
  sync: () => request<{ queued: boolean }>("POST", "/gmail/sync"),
  messages: (f: Filters, cursor?: string | null, limit?: number) => request<GmailPage>("GET", `/gmail/messages?${listQuery(f, cursor, limit)}`),
  message: (id: string) => request<GmailMessageDetail>("GET", `/gmail/messages/${id}`),
  markRead: (id: string, read: boolean) => request<GmailMessage>("POST", `/gmail/messages/${id}/read`, { read }),
  attachment: (id: string) => request<GmailAttachmentDetail>("GET", `/gmail/attachments/${id}`),
  process: (id: string, b: { warehouse_id: string; category: GmailCategory; reprocess?: boolean; confirm_duplicate?: boolean }) =>
    request<ProcessOut>("POST", `/gmail/attachments/${id}/process`, b, true),
  pageUrl: (id: string, page: number) => `/api/v1/gmail/attachments/${id}/pages/${page}`,
  downloadUrl: (id: string) => `/api/v1/gmail/attachments/${id}/content`,
};

/* ------------------------------------------------------------------ wording */

export const RANGES: { value: DateRange; label: string }[] = [
  { value: "all", label: "All emails" },
  { value: "today", label: "Today" },
  { value: "yesterday", label: "Yesterday" },
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "custom", label: "Custom range" },
];
export const SHOWS: { value: Filters["show"]; label: string }[] = [
  { value: "attachments", label: "Has attachments" },
  { value: "pdf", label: "PDF documents" },
  { value: "processed", label: "Processed" },
  { value: "not_processed", label: "Not processed" },
];

export const STATE: Record<ProcessingState, { tone: Tone; label: string }> = {
  NOT_PROCESSED: { tone: "neutral", label: "Not processed" },
  RECEIVED: { tone: "neutral", label: "Kept, not read" },
  PROCESSING: { tone: "info", label: "Reading" },
  NEEDS_REVIEW: { tone: "warning", label: "Needs review" },
  PROCESSED: { tone: "success", label: "Processed" },
  FAILED: { tone: "danger", label: "Couldn't read" },
  REJECTED: { tone: "neutral", label: "Reading rejected" },
};

export const CATEGORIES: { value: GmailCategory; label: string }[] = [
  { value: "PICK_SHEET", label: "Picking sheet" },
  { value: "SALES_ORDER", label: "Sales order" },
  { value: "VENDOR_DOCUMENT", label: "Supplier invoice" },
  { value: "PURCHASE_ORDER", label: "Purchase order" },
  { value: "DELIVERY_NOTE", label: "Delivery note" },
  { value: "PACKING_LIST", label: "Packing list" },
  { value: "RECEIPT", label: "Receipt" },
];

/** The connection in one word plus a sentence, for the page header. */
export function connection(s: GmailStatus | null): { tone: Tone; label: string; detail: string } {
  if (!s) return { tone: "neutral", label: "Checking", detail: "" };
  const a = s.account;
  if (!a)
    return s.configured
      ? { tone: "neutral", label: "Not connected", detail: "No mailbox is connected yet." }
      : { tone: "neutral", label: "Not set up", detail: "Gmail is not set up on this server yet." };
  if (a.status === "REAUTH_REQUIRED") return { tone: "danger", label: "Disconnected", detail: "Google no longer accepts the saved sign-in. Connect the mailbox again." };
  if (a.status === "ERROR") return { tone: "warning", label: "Not syncing", detail: a.last_error ? `${a.last_error}. It keeps trying.` : "The last checks failed. It keeps trying." };
  return { tone: "success", label: "Connected", detail: s.push_configured && a.watch_expires_at ? "New mail arrives live." : `Checked every ${every(s.poll_seconds)}.` };
}
const every = (sec: number) => (sec % 60 === 0 ? (sec === 60 ? "minute" : `${sec / 60} minutes`) : `${sec} seconds`);

/** "2:14 PM" today, "Yesterday", "Nov 3", or "Nov 3, 2025" for another year. */
export function whenShort(iso: string, now = new Date()): string {
  const d = new Date(iso);
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 86_400_000);
  if (diff === 0) return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (diff === 1) return "Yesterday";
  return d.toLocaleDateString(undefined, d.getFullYear() === now.getFullYear() ? { month: "short", day: "numeric" } : { month: "short", day: "numeric", year: "numeric" });
}
export const whenFull = (iso: string) => new Date(iso).toLocaleString(undefined, { year: "numeric", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });

/** "3 minutes ago" for the last-synchronized line. */
export function ago(iso: string | null, now = Date.now()): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`;
  if (s < 86_400) return `${Math.round(s / 3600)} h ago`;
  return whenFull(iso);
}

export const senderOf = (m: Pick<GmailMessage, "from_name" | "from_email">) => m.from_name || m.from_email || "Unknown sender";

/** Files a person would pick: real attachments first, signature pictures last. */
export const pickable = (m: Pick<GmailMessage, "attachments">) => [...m.attachments].sort((a, b) => Number(a.inline) - Number(b.inline));

/** Server refusal in plain words. */
export function gmailMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return err instanceof Error ? err.message : "Something went wrong";
  const known: Record<string, string> = {
    GMAIL_NOT_CONFIGURED: "Gmail is not set up on this server yet.",
    GMAIL_NOT_CONNECTED: "No mailbox is connected.",
    GMAIL_REAUTH_REQUIRED: "The mailbox must be connected again first.",
    GMAIL_UNAVAILABLE: "Gmail did not answer. Try again in a moment.",
    GMAIL_STATE_INVALID: "This connection attempt expired. Start again from Gmail Inbox.",
    GMAIL_SCOPE_MISSING: "Read-only access to the mailbox was not granted. Start again and allow it.",
    ATTACHMENT_GONE: "This file is no longer on the email in Gmail.",
    UNSUPPORTED_FILE: "Only PDF, PNG, JPEG or TIFF files can be opened or read.",
    FILE_TOO_LARGE: "The file is larger than the upload limit.",
    UNREADABLE: "The file could not be opened (damaged or password-protected).",
    TOO_MANY_REQUESTS: "Too many requests. Wait a moment.",
    FORBIDDEN: "Your account can't do this.",
  };
  return known[err.code] ?? err.message;
}

/** The earlier uses the server attaches to an ALREADY_PROCESSED / DRAFT_EXISTS refusal. */
export function previousOf(err: unknown): HistoryEntry[] {
  if (!(err instanceof ApiError) || !Array.isArray(err.extra.previous)) return [];
  return err.extra.previous as HistoryEntry[];
}

/** Colours for the email frame (a separate document, so the app's theme tokens are passed in). */
export type BodyColors = { ink: string; muted: string; line: string; link: string };
const SYSTEM: BodyColors = { ink: "CanvasText", muted: "GrayText", line: "GrayText", link: "LinkText" };

/** The app's theme colours, read from the page (falls back to the browser's own colours). */
export function themeColors(): BodyColors {
  if (typeof document === "undefined") return SYSTEM;
  const css = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return { ink: v("--color-ink", SYSTEM.ink), muted: v("--color-ink-muted", SYSTEM.muted), line: v("--color-line", SYSTEM.line), link: v("--color-brand-primary-hover", SYSTEM.link) };
}

/** A sandboxed document for an email's (already server-cleaned) HTML: no scripts, no network. */
export function bodyDocument(html: string, c: BodyColors = SYSTEM): string {
  const csp = "default-src 'none'; style-src 'unsafe-inline'";
  const css = `body{margin:0;padding:12px;font:14px/1.55 system-ui,sans-serif;color:${c.ink};word-break:break-word}a{color:${c.link}}table{border-collapse:collapse;max-width:100%}td,th{padding:2px 6px;vertical-align:top}blockquote{margin:8px 0;padding-left:10px;border-left:3px solid ${c.line};color:${c.muted}}pre{white-space:pre-wrap}`;
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${csp}"><base target="_blank"><style>${css}</style></head><body>${html}</body></html>`;
}
