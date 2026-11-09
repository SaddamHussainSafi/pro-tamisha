// Order Connect (website order forms): types, API calls and the pure helpers the pages share.
import { apiRequest as request } from "../api";
import type { Tone } from "../ui/primitives";

export type FieldKind = "TEXT" | "TEXTAREA" | "EMAIL" | "PHONE" | "DATE" | "SELECT" | "CHECKBOX" | "PRODUCTS";
export type FormField = { key: string; kind: FieldKind; label: string; help_text: string; required: boolean; options: string[] };
export type Theme = { accent: string; radius: "none" | "small" | "large" };
export type ProductScope = "ALL" | "CATEGORIES" | "PRODUCTS";
export type FormContent = {
  version_id: string;
  version_no: number;
  status: "DRAFT" | "PUBLISHED" | "RETIRED";
  revision: number;
  title: string;
  intro: string;
  success_message: string;
  theme: Theme;
  logo_id: string | null;
  logo_url: string | null;
  show_prices: boolean;
  product_scope: ProductScope;
  scope_ids: string[];
  fields: FormField[];
  updated_at: string | null;
  published_at: string | null;
};
export type OcForm = {
  id: string;
  public_id: string;
  name: string;
  accepting: boolean;
  standalone_link: boolean;
  default_warehouse_id: string | null;
  archived: boolean;
  published: boolean;
  published_version_no: number | null;
  published_at: string | null;
  unpublished_changes: boolean;
  submissions: number;
  open_submissions: number;
  version: number;
  created_at: string;
  updated_at: string;
  editing?: FormContent;
};
export type DraftIn = Pick<
  FormContent,
  "revision" | "title" | "intro" | "success_message" | "theme" | "logo_id" | "show_prices" | "product_scope" | "scope_ids" | "fields"
>;

export type SubStatus = "NEW" | "REVIEWING" | "APPROVED" | "CONVERTED" | "REJECTED" | "DUPLICATE" | "SPAM";
export type SubSummary = {
  id: string;
  reference: string;
  status: SubStatus;
  form_id: string;
  form_name: string;
  customer_name: string;
  business_name: string;
  email: string;
  phone: string;
  requested_date: string | null;
  item_count: number;
  possible_duplicate: boolean;
  spam_reasons: string[];
  sales_order_id: string | null;
  erased: boolean;
  unread: boolean;
  version: number;
  created_at: string;
};
export type SubItem = { line_no: number; product_id: string; sku: string; product_name: string; unit_code: string; qty: string };
export type SubEvent = { kind: string; from_status: string | null; to_status: string | null; note: string; actor_name: string | null; created_at: string };
export type SubDetail = SubSummary & {
  form_version_no: number;
  default_warehouse_id: string | null;
  delivery_address: string;
  notes: string;
  custom: { key: string; label: string; value: string | boolean }[];
  consent_at: string | null;
  source_host: string;
  possible_duplicate_of: { id: string; reference: string } | null;
  items: SubItem[];
  raw: Record<string, unknown>;
  events: SubEvent[];
  customer: { id: string; name: string } | null;
  sales_order: { id: string; order_no: string; status: string } | null;
  converted_by_name: string | null;
  converted_at: string | null;
  erased_at: string | null;
  allowed: SubStatus[];
};
export type CustomerMatch = {
  id: string;
  name: string;
  email: string | null;
  phone: string | null;
  external_ref: string | null;
  active: boolean;
  reasons: string[];
  score: number;
};
export type Overview = {
  by_status: Record<SubStatus, number>;
  open: number;
  new: number;
  unread_for_me: number;
  received_24h: number;
  received_30d: number;
  converted_30d: number;
  conversion_rate_30d: number | null;
  forms_total: number;
  forms_live: number;
  latest: SubSummary[];
};
export type Domain = { id: string; form_id: string | null; host: string; created_at: string };
export type EmbedInfo = {
  public_url: string;
  iframe: string;
  published: boolean;
  accepting: boolean;
  standalone_link: boolean;
  hosts: string[];
  frame_ancestors: string;
  https_only: boolean;
};
export type OcSettings = {
  notify_staff: boolean;
  ack_email: boolean;
  min_fill_seconds: number;
  ip_limit_per_hour: number;
  form_limit_per_hour: number;
  retention_days: number | null;
  consent_required: boolean;
  consent_text: string;
  version: number;
};
export type ConvertLine = { product_id: string; unit_code: string; qty: string; item_line_no?: number | null };
export type ConvertIn = {
  version: number;
  warehouse_id: string;
  customer_id?: string | null;
  new_customer?: { name: string; email?: string | null; phone?: string | null } | null;
  requested_for?: string | null;
  note?: string;
  lines: ConvertLine[];
};
export type PreviewConfig = Record<string, unknown> & { form: { title: string } };

const B = "/order-connect";
const e = encodeURIComponent;

export const oc = {
  overview: () => request<Overview>("GET", `${B}/overview`),
  badge: () => request<{ unread: number }>("GET", `${B}/badge`),
  forms: (archived = false) => request<OcForm[]>("GET", `${B}/forms${archived ? "?archived=true" : ""}`),
  form: (id: string) => request<OcForm & { editing: FormContent; accents: { value: string; label: string }[] }>("GET", `${B}/forms/${e(id)}`),
  createForm: (name: string, default_warehouse_id: string | null) =>
    request<OcForm>("POST", `${B}/forms`, { name, default_warehouse_id }, true),
  patchForm: (id: string, body: Partial<Pick<OcForm, "name" | "accepting" | "standalone_link" | "default_warehouse_id">> & { version: number; archived?: boolean }) =>
    request<OcForm>("PATCH", `${B}/forms/${e(id)}`, body, true),
  saveDraft: (id: string, body: DraftIn) => request<OcForm & { editing: FormContent }>("PUT", `${B}/forms/${e(id)}/draft`, body, true),
  publish: (id: string, revision: number) => request<OcForm & { editing: FormContent }>("POST", `${B}/forms/${e(id)}/publish`, { revision }, true),
  previewConfig: (id: string) => request<PreviewConfig>("GET", `${B}/forms/${e(id)}/preview-config`),
  embed: (id: string) => request<EmbedInfo>("GET", `${B}/forms/${e(id)}/embed`),
  uploadLogo: (file: File) => request<{ id: string; url: string; width: number; height: number }>("POST", `${B}/logos`, { raw: file, type: file.type }),
  domains: (formId?: string) => request<Domain[]>("GET", `${B}/domains${formId ? `?form_id=${e(formId)}` : ""}`),
  addDomain: (host: string, form_id: string | null) => request<Domain>("POST", `${B}/domains`, { host, form_id }, true),
  removeDomain: (id: string) => request<undefined>("DELETE", `${B}/domains/${e(id)}`),
  settings: () => request<OcSettings>("GET", `${B}/settings`),
  saveSettings: (s: OcSettings) => request<OcSettings>("PUT", `${B}/settings`, s, true),
  submissions: (p: { status?: string; q?: string; form_id?: string; before?: string | null; limit?: number }) => {
    const u = new URLSearchParams();
    if (p.status) u.set("status", p.status);
    if (p.q?.trim()) u.set("q", p.q.trim());
    if (p.form_id) u.set("form_id", p.form_id);
    if (p.before) u.set("before", p.before);
    if (p.limit) u.set("limit", String(p.limit));
    const s = u.toString();
    return request<{ items: SubSummary[]; next_before: string | null }>("GET", `${B}/submissions${s ? `?${s}` : ""}`);
  },
  submission: (id: string) => request<SubDetail>("GET", `${B}/submissions/${e(id)}`),
  matches: (id: string) => request<CustomerMatch[]>("GET", `${B}/submissions/${e(id)}/matches`),
  markRead: (id: string) => request<undefined>("POST", `${B}/submissions/${e(id)}/read`),
  setStatus: (id: string, version: number, status: SubStatus, note = "") =>
    request<SubDetail>("POST", `${B}/submissions/${e(id)}/status`, { version, status, note }, true),
  addNote: (id: string, note: string) => request<SubDetail>("POST", `${B}/submissions/${e(id)}/notes`, { note }, true),
  convert: (id: string, body: ConvertIn) => request<SubDetail>("POST", `${B}/submissions/${e(id)}/convert`, body, true),
  erase: (id: string, version: number, reason: string) =>
    request<SubDetail>("POST", `${B}/submissions/${e(id)}/erase`, { version, reason }, true),
};

/* ---------------------------------------------------------------- statuses */

export const STATUS: Record<SubStatus, { label: string; tone: Tone }> = {
  NEW: { label: "New", tone: "info" },
  REVIEWING: { label: "Reviewing", tone: "warning" },
  APPROVED: { label: "Approved", tone: "success" },
  CONVERTED: { label: "Converted", tone: "success" },
  REJECTED: { label: "Rejected", tone: "neutral" },
  DUPLICATE: { label: "Duplicate", tone: "neutral" },
  SPAM: { label: "Spam", tone: "danger" },
};
/** Button wording for a move from the current status. */
export const ACTION_LABEL: Record<SubStatus, string> = {
  NEW: "New",
  REVIEWING: "Start review",
  APPROVED: "Approve",
  CONVERTED: "Convert",
  REJECTED: "Reject",
  DUPLICATE: "Mark duplicate",
  SPAM: "Mark spam",
};
export function actionLabel(from: SubStatus, to: SubStatus): string {
  if (to === "REVIEWING" && from !== "NEW") return from === "APPROVED" ? "Undo approval" : "Reopen";
  return ACTION_LABEL[to];
}
/** Which permission a status move needs (mirrors the server rule). */
export const movePermission = (from: SubStatus, to: SubStatus) =>
  from === "APPROVED" || to === "APPROVED" ? "order_connect.approve" : "order_connect.review";

export const INBOX_FILTERS = [
  { value: "OPEN", label: "Open" },
  { value: "NEW", label: "New" },
  { value: "REVIEWING", label: "Reviewing" },
  { value: "APPROVED", label: "Approved" },
  { value: "CONVERTED", label: "Converted" },
  { value: "REJECTED", label: "Rejected" },
  { value: "DUPLICATE", label: "Duplicate" },
  { value: "SPAM", label: "Spam" },
  { value: "ALL", label: "All" },
] as const;
export type InboxFilter = (typeof INBOX_FILTERS)[number]["value"];

export const sender = (s: Pick<SubSummary, "business_name" | "customer_name" | "erased">) =>
  s.erased ? "Personal data erased" : s.business_name || s.customer_name || "No name given";

/* ---------------------------------------------------------------- builder */

export const SYSTEM_FIELDS: Record<string, { kind: FieldKind; label: string; hint: string }> = {
  customer_name: { kind: "TEXT", label: "Your name", hint: "Contact person" },
  business_name: { kind: "TEXT", label: "Business or store name", hint: "Used to match a customer" },
  email: { kind: "EMAIL", label: "Email", hint: "Used to match a customer" },
  phone: { kind: "PHONE", label: "Phone", hint: "Used to match a customer" },
  delivery_address: { kind: "TEXTAREA", label: "Delivery address", hint: "Copied to the draft order" },
  delivery_date: { kind: "DATE", label: "Requested delivery date", hint: "Today or later" },
  products: { kind: "PRODUCTS", label: "Products", hint: "Product picker with units and quantities" },
  notes: { kind: "TEXTAREA", label: "Notes", hint: "Copied to the draft order" },
};
export const CUSTOM_KINDS: { kind: FieldKind; label: string }[] = [
  { kind: "TEXT", label: "Short text" },
  { kind: "TEXTAREA", label: "Long text" },
  { kind: "SELECT", label: "Choice list" },
  { kind: "CHECKBOX", label: "Checkbox" },
];
export const KIND_LABEL: Record<FieldKind, string> = {
  TEXT: "Short text",
  TEXTAREA: "Long text",
  EMAIL: "Email",
  PHONE: "Phone",
  DATE: "Date",
  SELECT: "Choice list",
  CHECKBOX: "Checkbox",
  PRODUCTS: "Products",
};

export const isSystem = (key: string) => key in SYSTEM_FIELDS;
/** System fields not on the form yet, as [key, definition] pairs. */
export const missingSystemFields = (fields: FormField[]) => Object.entries(SYSTEM_FIELDS).filter(([k]) => !fields.some((f) => f.key === k));

/** A custom field key that is not used yet: custom_<slug of label>[_n]. */
export function customKey(label: string, fields: FormField[]): string {
  const slug =
    label
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 24) || "field";
  const taken = new Set(fields.map((f) => f.key));
  let key = `custom_${slug}`;
  for (let n = 2; taken.has(key); n++) key = `custom_${slug}_${n}`;
  return key;
}

export function moveField<T>(list: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= list.length || to >= list.length) return list;
  const next = list.slice();
  next.splice(to, 0, ...next.splice(from, 1));
  return next;
}

/** Publish rules (same as the server): products, a name and a contact, all required. */
export function publishProblems(fields: FormField[]): string[] {
  const req = new Set(fields.filter((f) => f.required).map((f) => f.key));
  const out: string[] = [];
  if (!req.has("products")) out.push("a required Products field");
  if (!req.has("customer_name") && !req.has("business_name")) out.push("a required name or business name");
  if (!req.has("email") && !req.has("phone")) out.push("a required email or phone");
  return out;
}

export function fieldProblems(f: FormField): string | null {
  if (!f.label.trim()) return "Give the field a label";
  if (f.kind === "SELECT") {
    const opts = f.options.map((o) => o.trim());
    if (opts.length < 2) return "A choice list needs at least two options";
    if (opts.some((o) => !o)) return "Remove empty options";
    if (new Set(opts).size !== opts.length) return "Options must be different";
  }
  return null;
}

export const draftOf = (c: FormContent): DraftIn => ({
  revision: c.status === "DRAFT" ? c.revision : 0,
  title: c.title,
  intro: c.intro,
  success_message: c.success_message,
  theme: c.theme,
  logo_id: c.logo_id,
  show_prices: c.show_prices,
  product_scope: c.product_scope,
  scope_ids: c.scope_ids,
  fields: c.fields,
});

/* ---------------------------------------------------------------- embedding */

/** Same host rules as the server (shown before sending, so mistakes are explained inline). */
export function normalizeHost(raw: string): { host: string } | { error: string } {
  let h = raw.trim().toLowerCase().replace(/^https?:\/\//, "");
  h = h.split(/[/?#]/)[0] ?? "";
  if (!h) return { error: "Enter a website address" };
  if (h.includes("@") || h.includes(":")) return { error: "Enter just the website name, like shop.example.com" };
  const core = h.startsWith("*.") ? h.slice(2) : h;
  const labels = core.split(".");
  if (labels.length < 2 || labels.some((l) => !/^[a-z0-9¡-￿]([a-z0-9¡-￿-]{0,61}[a-z0-9¡-￿])?$/.test(l)))
    return { error: "That website address is not valid" };
  if (/^\d+$/.test(labels[labels.length - 1] ?? "")) return { error: "Use a website name, not an IP address" };
  return { host: h };
}

export const wordpressSteps = [
  "In WordPress, open the page where customers should order and click Edit.",
  "Add a block (+), search for “Custom HTML” and insert it.",
  "Paste the iframe code from this page into the block.",
  "Click Preview to check it, then Update. No plugin is needed.",
];

export function relTime(iso: string, now = new Date()): string {
  const s = Math.round((now.getTime() - new Date(iso).getTime()) / 1000);
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  if (d < 7) return `${d} d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export const pct = (v: number | null) => (v === null ? "—" : `${Math.round(v * 100)}%`);

export const qtyText = (qty: string, unit: string) => {
  const n = Number(qty);
  const names: Record<string, [string, string]> = { BOX: ["box", "boxes"], PIECE: ["piece", "pieces"], LB: ["lb", "lb"], KG: ["kg", "kg"] };
  const [one, many] = names[unit] ?? [unit, unit];
  return `${qty} ${n === 1 ? one : many}`;
};

/** Live refresh for Order Connect screens: a window event other screens (and the sidebar) hear. */
export const OC_CHANGED = "oc:changed";
export const announceChange = () => window.dispatchEvent(new Event(OC_CHANGED));
