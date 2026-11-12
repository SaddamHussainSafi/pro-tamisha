import { apiRequest, has, hasCompanyWide, type Session } from "../api";
import type { IconName } from "../ui/Icon";

/* ---------- API types (routes/search.py) ---------- */
export type SearchHit = {
  type: string;
  id: string;
  title: string;
  subtitle: string;
  url: string;
  status: string | null;
  date: string | null;
  amount: string | null;
  currency: string | null;
  match: string;
  snippet: string | null;
};
export type SearchGroup = { type: string; label: string; count: number; items: SearchHit[] };
export type SearchResponse = {
  q: string;
  terms: string[];
  filters: [string, string][];
  types: { type: string; label: string }[];
  groups: SearchGroup[];
  items: SearchHit[];
  total: number;
  total_capped: boolean;
  next_cursor: string | null;
  index: { degraded: boolean; pending: number; behind_seconds: number | null };
  notes: string[];
  took_ms: number;
};
export type SearchPreview = {
  type: string;
  id: string;
  title: string;
  subtitle: string;
  url: string;
  fields: { label: string; value: string }[];
  related: { type: string; id: string; title: string; subtitle: string; url: string }[];
  actions: { label: string; href: string; kind: "link" | "download" }[];
};
export type RecentItem = { type: string; id: string; title: string; subtitle: string; url: string };
export type SearchSort = "relevance" | "newest" | "oldest";

export type SearchParams = { q: string; type?: string; limit?: number; perGroup?: number; cursor?: string | null; sort?: SearchSort };

export function searchQueryString(p: SearchParams): string {
  const u = new URLSearchParams();
  u.set("q", p.q);
  if (p.type) u.set("type", p.type);
  if (p.limit) u.set("limit", String(p.limit));
  if (p.perGroup) u.set("per_group", String(p.perGroup));
  if (p.cursor) u.set("cursor", p.cursor);
  if (p.sort && p.sort !== "relevance") u.set("sort", p.sort);
  return u.toString();
}

/** One search request; pass an AbortSignal so a newer keystroke cancels the older request. */
export async function runSearch(p: SearchParams, signal?: AbortSignal): Promise<SearchResponse> {
  const res = await fetch(`/api/v1/search?${searchQueryString(p)}`, { headers: { Accept: "application/json" }, credentials: "same-origin", signal });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string } }).error ?? {};
    throw new SearchError(res.status, err.code ?? "HTTP_ERROR", err.message ?? res.statusText);
  }
  return data as SearchResponse;
}

export class SearchError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

export const searchApi = {
  preview: (type: string, id: string) => apiRequest<SearchPreview>("GET", `/search/preview/${encodeURIComponent(type)}/${encodeURIComponent(id)}`),
  recent: () => apiRequest<RecentItem[]>("GET", "/search/recent"),
  remember: (type: string, id: string) => apiRequest<undefined>("POST", "/search/recent", { type, id }),
  clearRecent: () => apiRequest<undefined>("DELETE", "/search/recent"),
};

/**
 * Latest-wins guard: every call to `next()` aborts the previous request and returns a token;
 * `isCurrent(token)` tells whether a response still belongs to the newest query, so a slow
 * reply to an older keystroke can never overwrite newer results.
 */
export function latestOnly() {
  let seq = 0;
  let ctl: AbortController | null = null;
  return {
    next(): { token: number; signal: AbortSignal } {
      ctl?.abort();
      ctl = new AbortController();
      seq += 1;
      return { token: seq, signal: ctl.signal };
    },
    isCurrent: (token: number) => token === seq,
    cancel() {
      ctl?.abort();
      seq += 1;
    },
  };
}

/* ---------- display ---------- */
export const TYPE_ICON: Record<string, IconName> = {
  invoice: "invoices",
  sales_order: "cart",
  pick: "boxes",
  lot: "layers",
  stock_count: "hash",
  product: "products",
  customer: "users",
  vendor: "store",
  shipment: "receiving",
  payment: "payments",
  supplier_payment: "coins",
  scan: "scan",
  document: "file",
  gmail: "mail",
  activity: "clock",
};
export const typeIcon = (t: string): IconName => TYPE_ICON[t] ?? "search";

/** Remove accents and lower-case, keeping one character per input character (so indexes line up). */
function foldChar(c: string): string {
  return c.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().slice(0, 1) || c.toLowerCase();
}

/**
 * Split `text` into plain / matched segments for the given search terms (accent- and
 * case-insensitive). Identifier terms also match with their dashes / spaces ("TT2026-000123").
 */
export function highlight(text: string, terms: string[]): { text: string; hit: boolean }[] {
  if (!text) return [];
  const folded = Array.from(text).map(foldChar).join("");
  const chars = Array.from(text);
  const marks = new Array<boolean>(chars.length).fill(false);
  const uniq = [...new Set(terms.map((t) => Array.from(t).map(foldChar).join("").trim()).filter((t) => t.length > 0))];
  for (const t of uniq) {
    let from = 0;
    for (;;) {
      const at = folded.indexOf(t, from);
      if (at < 0) break;
      for (let i = at; i < at + t.length && i < marks.length; i++) marks[i] = true;
      from = at + Math.max(1, t.length);
    }
  }
  const out: { text: string; hit: boolean }[] = [];
  chars.forEach((c, i) => {
    const last = out[out.length - 1];
    if (last && last.hit === !!marks[i]) last.text += c;
    else out.push({ text: c, hit: !!marks[i] });
  });
  return out;
}

export function formatMoney(amount: string | null, currency: string | null): string | null {
  if (amount === null) return null;
  const [whole = "0", frac = "00"] = amount.split(".");
  const neg = whole.startsWith("-");
  const digits = (neg ? whole.slice(1) : whole).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${currency ? currency + " " : ""}${neg ? "-" : ""}${digits}.${frac.padEnd(2, "0").slice(0, 2)}`;
}

export const STATUS_WORDS = (s: string | null) => (s ? s.replace(/_/g, " ").toLowerCase() : "");

/* ---------- quick actions (only screens that exist and that the user may use) ---------- */
export type QuickAction = { label: string; to: string; icon: IconName; hint: string };
export function quickActions(s: Session | null): QuickAction[] {
  if (!s) return [];
  const any = (...p: string[]) => p.some((x) => has(s, x));
  const out: QuickAction[] = [];
  if (any("invoice.issue", "invoice.prepare")) out.push({ label: "New invoice", to: "/invoices/new", icon: "invoices", hint: "Invoices" });
  if (any("sales_order.manage")) out.push({ label: "New order", to: "/orders/new", icon: "cart", hint: "Orders" });
  if (hasCompanyWide(s, "catalog.manage")) {
    out.push({ label: "Add product", to: "/products", icon: "products", hint: "Products" });
    out.push({ label: "Add customer", to: "/customers", icon: "users", hint: "Customers" });
  }
  if (any("inventory.read")) out.push({ label: "Inventory", to: "/inventory", icon: "inventory", hint: "Stock on hand" });
  if (any("scan.create", "scan.review", "scan.device.manage")) out.push({ label: "Scan Center", to: "/scan", icon: "scan", hint: "Scan or upload" });
  if (any("report.read")) out.push({ label: "Reports", to: "/reports", icon: "reports", hint: "Analytics" });
  return out;
}

/** Filter keywords shown as hints under the box (they work as typed). */
export const SYNTAX_HINTS = ["invoice:", "batch:", "sku:", "customer:", "supplier:", "status:", "date:2026-11", "amount:>1000"];

/** `/search?q=…` link for "See all". */
export const searchPageUrl = (q: string, type?: string) => `/search?${new URLSearchParams(type ? { q, type } : { q }).toString()}`;

/** Same-origin app path only (never follow anything else from a response). */
export const isAppPath = (u: string) => u.startsWith("/") && !u.startsWith("//");

/** Opens universal search from anywhere (e.g. the phone header's search button). */
export const OPEN_SEARCH_EVENT = "pt:open-search";
export const openSearch = () => window.dispatchEvent(new Event(OPEN_SEARCH_EVENT));
