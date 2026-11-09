// Notification Center endpoints (apps/api/src/pro_trading/routes/notifications.py).
import { apiRequest as request } from "../api";

export type Severity = "info" | "success" | "warning" | "critical";
export type Category =
  | "website_order"
  | "sales_order"
  | "invoice"
  | "payment"
  | "inventory"
  | "receiving"
  | "scan"
  | "integration"
  | "security"
  | "system";

export type AppNotification = {
  id: string;
  seq: number;
  kind: string;
  category: Category;
  category_label: string;
  severity: Severity;
  title: string;
  body: string;
  source_module: string;
  link: string | null;
  target_type: string | null;
  target_id: string | null;
  created_at: string;
  read: boolean;
  read_at: string | null;
  archived: boolean;
};
/** Hints the server attaches to live events (quiet hours and preferences already applied). */
export type Alert = { banner: boolean; desktop: boolean; sound: boolean };
export type LiveNotification = AppNotification & { alert?: Alert };

export type NotificationPage = { items: AppNotification[]; next_before: number | null; unread: number };
export type NotificationSummary = { unread: number; critical_unread: number; latest_seq: number };
export type ChannelPrefs = { in_app: boolean; email: boolean; desktop: boolean };
export type NotificationPrefs = {
  in_app: boolean;
  desktop: boolean;
  email: boolean;
  sound: boolean;
  grouped: boolean;
  quiet_hours: boolean;
  quiet_start: string;
  quiet_end: string;
  categories: Record<Category, ChannelPrefs>;
  category_labels: Record<Category, string>;
  can_mute_security: boolean;
  version: number;
};
export type NotificationPrefsIn = Omit<NotificationPrefs, "category_labels" | "can_mute_security">;

export type ListParams = {
  status?: "unread" | "all" | "archived";
  category?: Category[];
  severity?: Severity[];
  q?: string;
  before?: number | null;
  limit?: number;
};

function qs(p: ListParams): string {
  const u = new URLSearchParams();
  if (p.status) u.set("status", p.status);
  p.category?.forEach((c) => u.append("category", c));
  p.severity?.forEach((s) => u.append("severity", s));
  if (p.q?.trim()) u.set("q", p.q.trim());
  if (p.before) u.set("before", String(p.before));
  if (p.limit) u.set("limit", String(p.limit));
  const s = u.toString();
  return s ? `?${s}` : "";
}

const N = "/notifications";
const id = (v: string) => encodeURIComponent(v);

export const notificationsApi = {
  list: (p: ListParams = {}) => request<NotificationPage>("GET", `${N}${qs(p)}`),
  summary: () => request<NotificationSummary>("GET", `${N}/summary`),
  read: (nid: string) => request<AppNotification>("POST", `${N}/${id(nid)}/read`),
  unread: (nid: string) => request<AppNotification>("POST", `${N}/${id(nid)}/unread`),
  archive: (nid: string) => request<AppNotification>("POST", `${N}/${id(nid)}/archive`),
  unarchive: (nid: string) => request<AppNotification>("POST", `${N}/${id(nid)}/unarchive`),
  archiveMany: (ids: string[]) => request<{ updated: number; unread: number }>("POST", `${N}/archive`, { ids }),
  readAll: (category?: Category) => request<{ updated: number; unread: number }>("POST", `${N}/read-all`, { category: category ?? null }),
  prefs: () => request<NotificationPrefs>("GET", `${N}/preferences`),
  savePrefs: (p: NotificationPrefsIn) => request<NotificationPrefs>("PUT", `${N}/preferences`, p, true),
  streamUrl: "/api/v1/notifications/stream",
};
