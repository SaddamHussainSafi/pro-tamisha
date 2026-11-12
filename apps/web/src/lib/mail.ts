// Email (Lark Mail) endpoints (apps/api/src/pro_trading/routes/mail.py) and the pure helpers the
// Email screens share. The browser never sees a Lark password: it only talks to our own API.
import { ApiError, apiRequest as request, renewCsrf } from "../api";
import { errorText } from "./errors";
import type { Tone } from "../ui/primitives";

export type Addr = { name: string; email: string };
export type MailStatus = "NOT_CONFIGURED" | "CONNECTED" | "ERROR" | "AUTH_FAILED" | "PAUSED";
export type MailAccount = {
  id: string;
  address: string;
  label: string;
  display_name: string;
  kind: "PERSONAL" | "SHARED";
  status: MailStatus | string;
  can_send: boolean;
  unread: number;
  last_sync_at: string | null;
  history_complete: boolean;
  signature_html: string;
};
export type FolderRole = "INBOX" | "STARRED" | "SENT" | "DRAFTS" | "ARCHIVE" | "JUNK" | "ALL" | "FLAGGED" | "OTHER";
export type MailFolder = { id: string; name: string; role: FolderRole | string; total: number; unread: number; virtual: boolean };
export type MailRow = {
  id: string;
  folder_id: string;
  thread_key: string;
  thread_count: number;
  from_name: string;
  from_email: string;
  to: Addr[];
  subject: string;
  snippet: string;
  received_at: string;
  unread: boolean;
  starred: boolean;
  attachment_count: number;
  removed_in_lark: boolean;
  highlight?: string | null;
};
export type MailPage = { items: MailRow[]; next_cursor: string | null };
export type MailAttachment = {
  id: string;
  filename: string;
  mime_type: string;
  byte_size: number;
  inline: boolean;
  previewable: boolean;
  downloadable: boolean;
  scan_status: string;
  scan_detail: string;
};
export type EntityType = "customer" | "vendor" | "sales_order" | "invoice" | "shipment" | "payment";
export type MailLink = { id: string; entity_type: EntityType; entity_id: string; label: string; url: string; created_by: string; created_at: string };
export type MailMessage = {
  id: string;
  account_id: string;
  folder_id: string;
  folder_role: string;
  message_id_header: string;
  from_name: string;
  from_email: string;
  to: Addr[];
  cc: Addr[];
  bcc: Addr[];
  reply_to: Addr[];
  subject: string;
  sent_at: string | null;
  received_at: string;
  body_html: string;
  body_text: string;
  body_truncated: boolean;
  has_remote_images: boolean;
  remote_images_loaded: boolean;
  unread: boolean;
  starred: boolean;
  removed_in_lark: boolean;
  attachments: MailAttachment[];
  thread: MailRow[];
  links: MailLink[];
  warnings: string[];
};
export type ComposeMode = "new" | "reply" | "reply_all" | "forward";
export type DraftIn = {
  account_id: string;
  mode: ComposeMode;
  source_message_id: string | null;
  to: Addr[];
  cc: Addr[];
  bcc: Addr[];
  subject: string;
  body_html: string;
  upload_ids: string[];
  forward_attachment_ids: string[];
};
export type MailDraft = {
  id: string;
  account_id: string;
  mode: ComposeMode;
  source_message_id: string | null;
  to: Addr[];
  cc: Addr[];
  bcc: Addr[];
  subject: string;
  body_html: string;
  upload_ids: string[];
  updated_at: string;
};
export type OutgoingStatus = "QUEUED" | "SENDING" | "SENT" | "FAILED" | "UNCERTAIN";
export type Outgoing = {
  id: string;
  account_id: string;
  status: OutgoingStatus | string;
  subject: string;
  to: Addr[];
  attempts: number;
  last_error: string | null;
  created_at: string;
  sent_at: string | null;
  seen_in_sent: boolean;
};
export type Upload = { id: string; filename: string; mime_type: string; byte_size: number };
export type LinkTarget = { entity_type: EntityType; entity_id: string; label: string; sub: string };
export type SearchFilters = { folder: string; date_from: string; date_to: string; has_attachments: boolean };

/* ------------------------------------------------------------- admin */
export type MailUserGrant = { employee_id: string; name: string; email: string; can_send: boolean; active: boolean };
export type AdminAccount = {
  id: string;
  address: string;
  label: string;
  display_name: string;
  kind: "PERSONAL" | "SHARED";
  status: MailStatus | string;
  has_password: boolean;
  password_set_at: string | null;
  imap_host: string;
  imap_port: number;
  smtp_host: string;
  smtp_port: number;
  username: string;
  imap_ok_at: string | null;
  smtp_ok_at: string | null;
  imap_error: string | null;
  smtp_error: string | null;
  sync_enabled: boolean;
  sync_interval_seconds: number;
  last_sync_at: string | null;
  last_sync_attempt_at: string | null;
  next_sync_at: string | null;
  syncing: boolean;
  failure_count: number;
  last_error_code: string | null;
  last_error: string | null;
  history_complete: boolean;
  message_count: number;
  unread_count: number;
  capabilities: string[];
  idle_supported: boolean;
  users: MailUserGrant[];
  health: string;
  signature_html: string;
};
export type TestResult = { imap_ok: boolean; smtp_ok: boolean; imap_error: string | null; smtp_error: string | null; folders: Record<string, string>[]; idle_supported: boolean; saved: boolean };
export type SyncRun = { id: string; trigger: string; started_at: string; finished_at: string | null; status: string; new_messages: number; backfilled: number; folders: number; error_code: string | null; error: string | null };
export type LogEntry = { at: string; action: string; success: boolean; by: string; detail: Record<string, unknown> | null };
export type CompanyUser = { id: string; name: string; email: string };

const qs = (o: Record<string, string | number | boolean | null | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(o)) if (v !== undefined && v !== null && v !== "" && v !== false) p.set(k, String(v));
  return p.toString();
};

/**
 * POST /mail/send with a caller-chosen Idempotency-Key: one key per compose session, so a double
 * click or a retry after a dropped connection answers with the same queued message instead of
 * sending twice. (api.ts makes a new key per call, which is right everywhere else.)
 */
async function sendOnce(body: DraftIn & { draft_id: string | null }, key: string): Promise<Outgoing> {
  const go = () => {
    const headers: Record<string, string> = { Accept: "application/json", "Content-Type": "application/json", "Idempotency-Key": key };
    const csrf = sessionStorage.getItem("pt.csrf");
    if (csrf) headers["X-CSRF-Token"] = csrf;
    return fetch("/api/v1/mail/send", { method: "POST", headers, credentials: "same-origin", body: JSON.stringify(body) }).catch(() => {
      throw new ApiError(0, "NETWORK", "No answer from Pro Trading", {}, "POST");
    });
  };
  let res = await go();
  let data = (await res.json().catch(() => ({}))) as { error?: { code?: string; message?: string } & Record<string, unknown> };
  if (res.status === 403 && data.error?.code === "CSRF_FAILED" && (await renewCsrf())) {
    res = await go();
    data = (await res.json().catch(() => ({}))) as typeof data;
  }
  if (!res.ok) {
    const { code = "HTTP_ERROR", message = res.statusText, ...extra } = data.error ?? {};
    throw new ApiError(res.status, code, message, extra, "POST");
  }
  return data as unknown as Outgoing;
}

export const mail = {
  accounts: () => request<MailAccount[]>("GET", "/mail/accounts"),
  folders: (acc: string) => request<MailFolder[]>("GET", `/mail/accounts/${acc}/folders`),
  messages: (acc: string, q: { folder: string; cursor?: string | null; limit?: number; unread?: boolean; has_attachments?: boolean }) =>
    request<MailPage>("GET", `/mail/accounts/${acc}/messages?${qs({ ...q, limit: q.limit ?? 50 })}`),
  message: (id: string, images = false) => request<MailMessage>("GET", `/mail/messages/${id}?images=${images}`),
  setState: (id: string, s: { read?: boolean; starred?: boolean }) => request<undefined>("POST", `/mail/messages/${id}/state`, s),
  search: (acc: string, q: string, f: SearchFilters, cursor?: string | null) =>
    request<MailPage>("GET", `/mail/search?${qs({ account_id: acc, q, folder: f.folder, date_from: f.date_from, date_to: f.date_to, has_attachments: f.has_attachments, cursor, limit: 30 })}`),
  drafts: (acc: string) => request<MailDraft[]>("GET", `/mail/accounts/${acc}/drafts`),
  createDraft: (b: DraftIn) => request<MailDraft>("POST", "/mail/drafts", b),
  saveDraft: (id: string, b: DraftIn) => request<MailDraft>("PUT", `/mail/drafts/${id}`, b),
  discardDraft: (id: string) => request<undefined>("POST", `/mail/drafts/${id}/discard`),
  upload: (file: File) => request<Upload>("POST", `/mail/uploads?${qs({ filename: file.name })}`, { raw: file, type: file.type || "application/octet-stream" }),
  send: sendOnce,
  outgoing: (acc: string) => request<Outgoing[]>("GET", `/mail/accounts/${acc}/outgoing`),
  retry: (id: string, confirm_may_duplicate: boolean) => request<Outgoing>("POST", `/mail/outgoing/${id}/retry`, { confirm_may_duplicate }),
  linkTargets: (type: EntityType, q: string) => request<LinkTarget[]>("GET", `/mail/link-targets?${qs({ type, q })}`),
  link: (id: string, b: { entity_type: EntityType; entity_id: string; note?: string }) => request<MailLink>("POST", `/mail/messages/${id}/links`, b),
  unlink: (id: string) => request<undefined>("POST", `/mail/links/${id}/unlink`),
  attachmentUrl: (id: string, inline = false) => `/api/v1/mail/attachments/${id}${inline ? "?inline=true" : ""}`,
};

export const mailAdmin = {
  accounts: () => request<AdminAccount[]>("GET", "/mail/admin/accounts"),
  create: (b: { address: string; display_name: string; label: string; kind: "PERSONAL" | "SHARED"; user_ids: string[] }) => request<AdminAccount>("POST", "/mail/admin/accounts", b),
  patch: (id: string, b: Partial<Pick<AdminAccount, "display_name" | "label" | "kind" | "sync_interval_seconds" | "signature_html" | "username">>) =>
    request<AdminAccount>("PATCH", `/mail/admin/accounts/${id}`, b),
  /** Typed password is tested and NOT saved; no password tests the saved one. */
  test: (id: string, password?: string) => request<TestResult>("POST", `/mail/admin/accounts/${id}/test`, password ? { password } : {}),
  /** Tested first; saved only when Lark accepted it. */
  credentials: (id: string, password: string) => request<TestResult>("POST", `/mail/admin/accounts/${id}/credentials`, { password }),
  sync: (id: string) => request<AdminAccount>("POST", `/mail/admin/accounts/${id}/sync`),
  pause: (id: string) => request<AdminAccount>("POST", `/mail/admin/accounts/${id}/pause`),
  resume: (id: string) => request<AdminAccount>("POST", `/mail/admin/accounts/${id}/resume`),
  reconnect: (id: string) => request<TestResult>("POST", `/mail/admin/accounts/${id}/reconnect`),
  access: (id: string, grants: { employee_id: string; can_send: boolean }[]) => request<AdminAccount>("PUT", `/mail/admin/accounts/${id}/access`, { grants }),
  runs: (id: string) => request<SyncRun[]>("GET", `/mail/admin/accounts/${id}/runs`),
  log: (id: string) => request<LogEntry[]>("GET", `/mail/admin/accounts/${id}/log`),
  users: () => request<CompanyUser[]>("GET", "/mail/admin/users"),
};

/* ------------------------------------------------------------- wording */

/** The mailbox's state as the API reports it. Never "Connected" unless the API says CONNECTED. */
export function mailboxState(a: Pick<MailAccount, "status" | "last_sync_at">): { tone: Tone; label: string; detail: string } {
  switch (a.status) {
    case "CONNECTED":
      return { tone: "success", label: "Connected", detail: "" };
    case "PAUSED":
      return { tone: "neutral", label: "Paused", detail: "Syncing is paused, so new mail does not arrive. What was already imported is shown." };
    case "AUTH_FAILED":
      return { tone: "danger", label: "Password rejected", detail: "Lark refused the saved app password, so new mail does not arrive. A Super Admin enters a new one in Settings > Email Services." };
    case "ERROR":
      return { tone: "warning", label: "Not syncing", detail: "The last check of this mailbox failed. It keeps trying; what was already imported is shown." };
    case "NOT_CONFIGURED":
      return { tone: "neutral", label: "Not connected yet", detail: "Not connected yet — a Super Admin enters the Lark app password in Settings > Email Services." };
    default:
      return { tone: "neutral", label: a.status, detail: "" };
  }
}

/** Admin health word from the API (`health`), never derived on our own. */
export const HEALTH: Record<string, { tone: Tone; label: string }> = {
  healthy: { tone: "success", label: "Healthy" },
  stale: { tone: "warning", label: "Not synced lately" },
  failing: { tone: "warning", label: "Failing" },
  password_rejected: { tone: "danger", label: "Password rejected" },
  paused: { tone: "neutral", label: "Paused" },
  not_configured: { tone: "neutral", label: "Not configured" },
};

export const OUTGOING: Record<string, { tone: Tone; label: string; detail: string }> = {
  QUEUED: { tone: "info", label: "Queued", detail: "Waiting to be handed to Lark." },
  SENDING: { tone: "info", label: "Sending", detail: "Being handed to Lark now." },
  SENT: { tone: "success", label: "Sent", detail: "Lark accepted it." },
  FAILED: { tone: "danger", label: "Failed", detail: "Lark did not accept it. Nothing was sent." },
  UNCERTAIN: { tone: "warning", label: "May have been sent", detail: "The connection dropped while sending. It may have been sent — check Sent before sending again." },
};

export const FOLDER_ICON: Record<string, "inbox" | "check" | "upload" | "file" | "archive" | "alert" | "layers" | "documents"> = {
  INBOX: "inbox",
  STARRED: "check",
  SENT: "upload",
  DRAFTS: "file",
  ARCHIVE: "archive",
  JUNK: "alert",
  ALL: "layers",
  FLAGGED: "check",
  OTHER: "documents",
};

export const ENTITY_LABEL: Record<EntityType, string> = {
  customer: "Customer",
  vendor: "Supplier",
  sales_order: "Order",
  invoice: "Invoice",
  shipment: "Receiving",
  payment: "Payment",
};

export const senderOf = (m: Pick<MailRow, "from_name" | "from_email">) => m.from_name || m.from_email || "Unknown sender";
export const addrText = (a: Addr) => (a.name ? `${a.name} <${a.email}>` : a.email);

const EMAIL = /^[^\s@<>(),;:"[\]]+@[^\s@<>(),;:"[\]]+\.[^\s@<>(),;:"[\]]{2,}$/;
export const validEmail = (s: string) => EMAIL.test(s.trim());

/** "Maria <maria@x.com>, bob@y.com; c@z.com" → addresses (invalid ones kept, flagged by validEmail). */
export function parseAddrs(raw: string): Addr[] {
  return raw
    .split(/[,;\n]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s) => {
      const m = /^"?([^"<]*?)"?\s*<([^>]+)>$/.exec(s);
      return m ? { name: (m[1] ?? "").trim(), email: (m[2] ?? "").trim() } : { name: "", email: s };
    });
}

/** Search words for highlighting (same split as the server's _words). */
export function words(q: string): string[] {
  return (q.toLowerCase().match(/[\p{L}\p{N}_@.-]+/gu) ?? []).slice(0, 8);
}

/** Text split into plain and matched parts, for <mark>. */
export function highlightParts(text: string, ws: string[]): { text: string; hit: boolean }[] {
  const list = ws.filter(Boolean).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!list.length || !text) return [{ text, hit: false }];
  const re = new RegExp(`(${list.join("|")})`, "gi");
  return text
    .split(re)
    .filter((p) => p !== "")
    .map((p) => ({ text: p, hit: list.some((w) => new RegExp(`^${w}$`, "i").test(p)) }));
}

const escapeHtml = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

/** "Re: x" / "Fwd: x" without stacking prefixes. */
export function replySubject(subject: string, mode: ComposeMode): string {
  const base = subject.replace(/^((re|fwd?|aw|sv)\s*:\s*)+/i, "");
  if (mode === "forward") return `Fwd: ${base}`;
  if (mode === "reply" || mode === "reply_all") return `Re: ${base}`;
  return subject;
}

/** Recipients for a reply: the sender (or Reply-To); reply all adds To + Cc minus my own address. */
export function replyRecipients(m: Pick<MailMessage, "from_name" | "from_email" | "reply_to" | "to" | "cc">, mode: ComposeMode, me: string): { to: Addr[]; cc: Addr[] } {
  if (mode === "forward" || mode === "new") return { to: [], cc: [] };
  const mine = me.toLowerCase();
  const first = m.reply_to.length ? m.reply_to : [{ name: m.from_name, email: m.from_email }];
  const seen = new Set<string>();
  const uniq = (list: Addr[]) =>
    list.filter((a) => {
      const k = a.email.toLowerCase();
      if (!k || k === mine || seen.has(k)) return false;
      seen.add(k);
      return true;
    });
  const to = uniq(first);
  if (mode === "reply") return { to: to.length ? to : first.slice(0, 1), cc: [] };
  return { to: [...to, ...uniq(m.to)], cc: uniq(m.cc) };
}

/** The quoted original under a reply or forward (the server already cleaned body_html). */
export function quoteOriginal(m: Pick<MailMessage, "from_name" | "from_email" | "received_at" | "subject" | "to" | "cc" | "body_html" | "body_text">, mode: ComposeMode): string {
  const when = new Date(m.received_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  const from = escapeHtml(addrText({ name: m.from_name, email: m.from_email }));
  const body = m.body_html || `<p>${escapeHtml(m.body_text).replace(/\n/g, "<br>")}</p>`;
  if (mode === "forward") {
    const to = escapeHtml(m.to.map(addrText).join(", "));
    const cc = m.cc.length ? `<br>Cc: ${escapeHtml(m.cc.map(addrText).join(", "))}` : "";
    return `<p>---------- Forwarded message ----------<br>From: ${from}<br>Date: ${escapeHtml(when)}<br>Subject: ${escapeHtml(m.subject)}<br>To: ${to}${cc}</p>${body}`;
  }
  return `<p>On ${escapeHtml(when)}, ${from} wrote:</p><blockquote>${body}</blockquote>`;
}

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
export function ago(iso: string | null, now = Date.now()): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 45) return "just now";
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`;
  if (s < 86_400) return `${Math.round(s / 3600)} h ago`;
  return whenFull(iso);
}

/** The app's theme colours for the email frame (a separate document), read from the page. */
export function frameColors(): { ink: string; muted: string; line: string; link: string } {
  const fallback = { ink: "CanvasText", muted: "GrayText", line: "GrayText", link: "LinkText" };
  if (typeof document === "undefined") return fallback;
  const css = getComputedStyle(document.documentElement);
  const v = (name: string, f: string) => css.getPropertyValue(name).trim() || f;
  return { ink: v("--color-ink", fallback.ink), muted: v("--color-ink-muted", fallback.muted), line: v("--color-line", fallback.line), link: v("--color-brand-primary-hover", fallback.link) };
}

/** Sandboxed document for an email's (server-cleaned) HTML. Remote images only when asked for. */
export function mailDocument(html: string, images: boolean, c = frameColors()): string {
  const csp = `default-src 'none'; style-src 'unsafe-inline'; img-src data:${images ? " https: http:" : ""}`;
  const css = `body{margin:0;padding:4px 2px;font:15px/1.55 Inter,system-ui,sans-serif;color:${c.ink};word-break:break-word}a{color:${c.link}}img{max-width:100%;height:auto}table{border-collapse:collapse;max-width:100%}td,th{padding:2px 6px;vertical-align:top}blockquote{margin:8px 0;padding-left:10px;border-left:3px solid ${c.line};color:${c.muted}}pre{white-space:pre-wrap}`;
  return `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="${csp}"><base target="_blank"><style>${css}</style></head><body>${html}</body></html>`;
}

/** Server refusal in plain words. */
export function mailError(err: unknown): string {
  if (!(err instanceof ApiError)) return errorText(err);
  const known: Record<string, string> = {
    NOT_FOUND: "This was not found, or it is in a mailbox you were not given.",
    SENDING_NOT_READY: "This mailbox can't send yet: Lark has not confirmed its sign-in. A Super Admin checks it in Settings > Email Services.",
    NO_RECIPIENT: "Add at least one recipient.",
    TOO_MANY_RECIPIENTS: "Too many recipients (at most 100).",
    FILE_TOO_LARGE: "Files can be at most 25 MB.",
    TOO_LARGE: "Attachments can be at most 25 MB in total.",
    FILE_TYPE_BLOCKED: "Program and script files can't be sent from here.",
    FILE_INFECTED: err.message,
    EMPTY_FILE: "The file is empty.",
    ATTACHMENT_BLOCKED: err.message,
    MAY_DUPLICATE: "It may already have been sent. Check Sent first, then confirm to send it again.",
    NOT_RETRYABLE: "Only failed or uncertain messages can be sent again.",
    ALREADY_SENT: "This draft was already sent.",
    RATE_LIMITED: err.message,
    BAD_QUERY: "Those search words could not be understood. Try other words.",
    NO_PASSWORD: "Type the Lark app password first.",
    SECRET_UNAVAILABLE: "The server can't encrypt or read mailbox passwords right now (its encryption key is missing). Nothing was saved.",
    WRONG_DOMAIN: err.message,
    EXISTS: "This mailbox is already set up.",
    PERSONAL_ONE_USER: "A personal mailbox belongs to one person. Make it Shared to give it to more people.",
    NOT_CONNECTED: "Connect the mailbox first (or resume it).",
    NOT_PAUSED: "This mailbox is not paused.",
    FORBIDDEN: "Your account can't do this.",
  };
  return known[err.code] ?? errorText(err);
}

/**
 * Second line of defence before HTML goes into the compose editor (it lives in the app's own
 * page, unlike the sandboxed reading frame). The server already cleaned it; this parses it in an
 * inert document and drops anything active: scripts, frames, forms, event handlers, javascript:
 * links and remote images (data-remote-src stays unloaded).
 */
export function scrubHtml(html: string): string {
  if (typeof DOMParser === "undefined") return "";
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
  doc.querySelectorAll("script,style,iframe,frame,object,embed,form,input,button,textarea,select,link,meta,base,svg,math").forEach((n) => n.remove());
  doc.body.querySelectorAll("*").forEach((el) => {
    for (const a of Array.from(el.attributes)) {
      const n = a.name.toLowerCase();
      const v = a.value.trim().toLowerCase();
      if (n.startsWith("on") || n === "srcset" || n === "contenteditable") el.removeAttribute(a.name);
      else if ((n === "href" || n === "src" || n === "action" || n === "formaction") && !/^(https?:|mailto:|tel:|#|data:image\/(png|jpeg|gif);)/.test(v)) el.removeAttribute(a.name);
    }
    // Pictures: only ones already inside the email (data:), never fetched from the internet.
    if (el.tagName === "IMG" && !/^data:image\//i.test(el.getAttribute("src") ?? "")) el.remove();
  });
  return doc.body.innerHTML;
}

/** Plain text of an HTML body (for "is it empty?" checks). */
export function htmlText(html: string): string {
  if (typeof DOMParser === "undefined") return html.replace(/<[^>]*>/g, "");
  return new DOMParser().parseFromString(html, "text/html").body.textContent ?? "";
}
