// Pure helpers for the Notification Center (unit-tested in notifications.test.ts).
import type { IconName } from "../ui/Icon";
import type { AppNotification, Category, Severity } from "./notificationsApi";

/* ---------- day groups: Today / Yesterday / Earlier ---------- */
export type DayKey = "today" | "yesterday" | "earlier";
export const DAY_LABEL: Record<DayKey, string> = { today: "Today", yesterday: "Yesterday", earlier: "Earlier" };

function startOfDay(d: Date) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

export function dayKey(iso: string, now = new Date()): DayKey {
  const t = new Date(iso);
  const today = startOfDay(now).getTime();
  if (t.getTime() >= today) return "today";
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  return t.getTime() >= yesterday.getTime() ? "yesterday" : "earlier";
}

export type DayGroup<T> = { key: DayKey; label: string; items: T[] };

/** Keeps the incoming (newest-first) order; empty groups are left out. */
export function groupByDay<T extends { created_at: string }>(items: T[], now = new Date()): DayGroup<T>[] {
  const out: DayGroup<T>[] = [];
  for (const it of items) {
    const key = dayKey(it.created_at, now);
    let g = out.find((x) => x.key === key);
    if (!g) {
      g = { key, label: DAY_LABEL[key], items: [] };
      out.push(g);
    }
    g.items.push(it);
  }
  const order: DayKey[] = ["today", "yesterday", "earlier"];
  return out.sort((a, b) => order.indexOf(a.key) - order.indexOf(b.key));
}

/* ---------- stacks: consecutive notifications from the same module ---------- */
export type Entry<T> = { type: "single"; key: string; item: T } | { type: "stack"; key: string; source: string; items: T[] };

/** With grouping on, 3+ consecutive notifications from one source collapse into a stack
 * (newest on top), like a desktop notification centre. Grouping off: every item stands alone. */
export function stack<T extends { id: string; source_module: string }>(items: T[], grouped: boolean, min = 3): Entry<T>[] {
  if (!grouped) return items.map((item) => ({ type: "single", key: item.id, item }));
  const out: Entry<T>[] = [];
  let run: T[] = [];
  const flush = () => {
    const head = run[0];
    if (head && run.length >= min) out.push({ type: "stack", key: `stack-${head.id}`, source: head.source_module, items: run });
    else run.forEach((item) => out.push({ type: "single", key: item.id, item }));
    run = [];
  };
  for (const it of items) {
    if (run[0] && run[0].source_module !== it.source_module) flush();
    run.push(it);
  }
  flush();
  return out;
}

/* ---------- time ---------- */
export function relativeTime(iso: string, now = new Date()): string {
  const t = new Date(iso);
  const s = Math.round((now.getTime() - t.getTime()) / 1000);
  if (s < 45) return "now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const key = dayKey(iso, now);
  const hm = t.toLocaleTimeString("en-CA", { hour: "numeric", minute: "2-digit" });
  if (key === "today") return `${Math.round(m / 60)}h ago`;
  if (key === "yesterday") return `Yesterday ${hm}`;
  const sameYear = t.getFullYear() === now.getFullYear();
  return t.toLocaleDateString("en-CA", { month: "short", day: "numeric", ...(sameYear ? {} : { year: "numeric" }) });
}

export function fullTime(iso: string): string {
  return new Date(iso).toLocaleString("en-CA", { dateStyle: "medium", timeStyle: "short" });
}

/* ---------- look ---------- */
export const CATEGORY_ICON: Record<Category, IconName> = {
  website_order: "store",
  sales_order: "cart",
  invoice: "invoices",
  payment: "payments",
  inventory: "inventory",
  receiving: "receiving",
  scan: "scan",
  integration: "refresh",
  security: "shield",
  system: "settings",
};

export const CATEGORY_LABEL: Record<Category, string> = {
  website_order: "Website orders",
  sales_order: "Sales & warehouse",
  invoice: "Invoices",
  payment: "Payments & balances",
  inventory: "Stock & shortages",
  receiving: "Vendor receiving",
  scan: "Scans & OCR",
  integration: "Drive & email",
  security: "Account & security",
  system: "Jobs & system",
};
export const CATEGORIES = Object.keys(CATEGORY_LABEL) as Category[];

/** Tone per priority: green success, amber warning, red critical, blue info. */
export const SEVERITY: Record<Severity, { label: string; icon: string; tile: string; dot: string }> = {
  info: { label: "Info", icon: "text-info", tile: "bg-info/10", dot: "bg-info" },
  success: { label: "Success", icon: "text-success", tile: "bg-success/12", dot: "bg-success" },
  warning: { label: "Warning", icon: "text-warning", tile: "bg-warning/12", dot: "bg-warning" },
  critical: { label: "Critical", icon: "text-danger", tile: "bg-danger/10", dot: "bg-danger" },
};
export const SEVERITIES = Object.keys(SEVERITY) as Severity[];

/** Badge text: exact up to 99. */
export function badge(n: number): string {
  return n > 99 ? "99+" : String(n);
}

/** In-app paths only (the API enforces this too); anything else is not followed. */
export function safeLink(link: string | null | undefined): string | null {
  return link && /^\/[A-Za-z0-9]/.test(link) ? link : null;
}

/** Prepend live items, newest first, without duplicates. */
export function merge(existing: AppNotification[], incoming: AppNotification[]): AppNotification[] {
  const seen = new Set(existing.map((n) => n.id));
  const fresh = incoming.filter((n) => !seen.has(n.id));
  return [...fresh, ...existing].sort((a, b) => b.seq - a.seq);
}

/* ---------- browser (desktop) notifications ---------- */
export type DesktopState = "unsupported" | "default" | "granted" | "denied";

export function desktopState(w: { Notification?: { permission: NotificationPermission } } = globalThis as never): DesktopState {
  const N = w.Notification;
  if (!N || typeof N.permission !== "string") return "unsupported";
  return N.permission as DesktopState;
}

/** A desktop notification is shown only when the person turned it on, the browser allowed it,
 * the server said this event may alert now (quiet hours), and the app is not in front. */
export function shouldShowDesktop(o: { prefOn: boolean; state: DesktopState; alert: boolean; visible: boolean }): boolean {
  return o.prefOn && o.alert && o.state === "granted" && !o.visible;
}
