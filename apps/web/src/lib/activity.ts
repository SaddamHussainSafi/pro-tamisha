// Activity feed (apps/api/src/pro_trading/activity.py): who did what, newest first.
import { apiRequest as request } from "../api";
import type { IconName } from "../ui/Icon";

export type ActivityTone = "success" | "info" | "price" | "stock" | "danger" | "neutral";
export type ActivityItem = {
  id: string;
  at: string;
  action: string;
  actor: { id: string | null; name: string };
  verb: string;
  object: string | null;
  extra: string | null;
  link: string | null;
  tag: string;
  tone: ActivityTone;
  icon: string;
};
export type ActivityPage = { items: ActivityItem[]; total: number; next_before: string | null; can_see_others: boolean };
export type ActivityQuery = { mine?: boolean; actor?: string | null; before?: string | null; limit?: number };

export const activityApi = {
  list(q: ActivityQuery = {}) {
    const u = new URLSearchParams();
    if (q.mine) u.set("mine", "true");
    if (q.actor) u.set("actor_id", q.actor);
    if (q.before) u.set("before", q.before);
    u.set("limit", String(q.limit ?? 30));
    return request<ActivityPage>("GET", `/activity?${u}`);
  },
};

/* ---------- look: dot (timeline), chip (tag) ---------- */
// Orange and purple come from the chart series. Chip text is ink (or a token colour that keeps
// 4.5:1 on its tint); a dot in the tone colour carries the colour cue.
export const TONE: Record<ActivityTone, { dot: string; chip: string }> = {
  success: { dot: "bg-success", chip: "bg-success/12 text-ink" },
  info: { dot: "bg-info", chip: "bg-info/10 text-info" },
  price: { dot: "bg-series-4", chip: "bg-series-4/14 text-ink" },
  stock: { dot: "bg-series-3", chip: "bg-series-3/14 text-ink" },
  danger: { dot: "bg-danger", chip: "bg-danger/10 text-danger" },
  neutral: { dot: "bg-brand-gray", chip: "bg-subtle text-ink-muted" },
};

const ICONS = new Set<string>([
  "plus", "products", "pricing", "users", "store", "cart", "check", "x", "boxes", "invoices", "file",
  "payments", "coins", "inventory", "receiving", "scan", "user", "refresh",
]);
/** The server names an icon; anything unknown falls back to a neutral one. */
export function activityIcon(name: string): IconName {
  if (name === "trash") return "x";
  return (ICONS.has(name) ? name : "clock") as IconName;
}

/* ---------- time ---------- */
function sameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** "Today" / "Yesterday" / "Oct 30" (+ year when not this year) and "10:24 AM". */
export function whenParts(iso: string, now = new Date()): { day: string; time: string } {
  const t = new Date(iso);
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  const day = sameDay(t, now)
    ? "Today"
    : sameDay(t, yesterday)
      ? "Yesterday"
      : t.toLocaleDateString("en-US", { month: "short", day: "numeric", ...(t.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }) });
  const time = t.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" });
  return { day, time };
}

export function initials(name: string): string {
  const parts = name.replace(/[^\p{L}\p{N}\s]/gu, "").trim().split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "")).toUpperCase() || "?";
}

/** "Sam Sales created an invoice — INV-2026-000041 (CAD 1,250.00)" for screen readers and titles. */
export function sentence(a: ActivityItem): string {
  return `${a.actor.name} ${a.verb}${a.object ? ` — ${a.object}` : ""}${a.extra ? ` (${a.extra})` : ""}`;
}

/** Append an older page without duplicates (a row can shift pages while new ones arrive). */
export function appendPage(cur: ActivityItem[], next: ActivityItem[]): ActivityItem[] {
  const seen = new Set(cur.map((a) => a.id));
  return [...cur, ...next.filter((a) => !seen.has(a.id))];
}
