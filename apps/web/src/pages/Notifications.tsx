import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { NotificationCardSkeleton, type CardActions } from "../components/notifications/NotificationCard";
import { NotificationFilters, type InboxFilter } from "../components/notifications/NotificationFilters";
import { NotificationGroup } from "../components/notifications/NotificationGroup";
import { useNotifications } from "../components/notifications/NotificationProvider";
import { useInbox } from "../components/notifications/useInbox";
import { groupByDay } from "../lib/notifications";
import type { Category, Severity } from "../lib/notificationsApi";
import { useDebounced } from "../lib/useDebounced";
import { deleteMode, useTrashChanges } from "../lib/trash";
import type { AppNotification } from "../lib/notificationsApi";
import { useAuth } from "../auth";
import { DeleteAction } from "../ui/DeleteAction";
import { Icon } from "../ui/Icon";
import { ErrorState } from "../ui/primitives";

/** The Activity page's "Notifications" tab: your own alerts — filter, search, open, read/unread,
 * dismiss and restore; loads older pages on scroll. */
export function NotificationsView() {
  const { summary, live, prefs, refreshSummary } = useNotifications();
  const { session } = useAuth();
  const canDelete = !!session && deleteMode(session, "notification") === "trash";
  const [trashing, setTrashing] = useState<AppNotification | null>(null);
  const [params, setParams] = useSearchParams();
  // Filters live in state (the router applies URL changes as low-priority transitions, so reading
  // them back from the URL lags behind fast input); the URL mirrors them for links and reloads.
  const [filter, setFilter] = useState<InboxFilter>(() => ({
    status: (["unread", "all", "archived"].includes(params.get("status") ?? "") ? params.get("status") : "all") as InboxFilter["status"],
    category: (params.get("category") ?? "") as Category | "",
    severity: (params.get("priority") ?? "") as Severity | "",
    q: params.get("q") ?? "",
  }));
  const debounced = useDebounced(filter.q.trim(), 300);
  useEffect(() => {
    const p = new URLSearchParams({ view: "notifications" });
    if (filter.status !== "all") p.set("status", filter.status);
    if (filter.category) p.set("category", filter.category);
    if (filter.severity) p.set("priority", filter.severity);
    if (debounced) p.set("q", debounced);
    setParams(p, { replace: true });
  }, [filter.status, filter.category, filter.severity, debounced, setParams]);

  const listParams = useMemo(
    () => ({
      status: filter.status,
      category: filter.category ? [filter.category] : undefined,
      severity: filter.severity ? [filter.severity] : undefined,
      q: debounced || undefined,
      limit: 25,
    }),
    [filter.status, filter.category, filter.severity, debounced],
  );
  const inbox = useInbox(listParams);
  useTrashChanges(() => {
    inbox.load();
    refreshSummary();
  });
  const groups = groupByDay(inbox.items);
  const unread = summary?.unread ?? 0;

  // Infinite scroll, with the "Load older" button as the keyboard/no-observer fallback.
  const sentinel = useRef<HTMLDivElement>(null);
  const loadMore = inbox.loadMore;
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !inbox.hasMore || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && loadMore(), { rootMargin: "240px" });
    io.observe(el);
    return () => io.disconnect();
  }, [inbox.hasMore, loadMore]);

  const actions: CardActions = {
    onOpen: (n) => !n.read && void inbox.act(n, "read"),
    onToggleRead: (n) => void inbox.act(n, n.read ? "unread" : "read"),
    onArchive: (n) => void inbox.act(n, "archive"),
    onRestore: (n) => void inbox.act(n, "unarchive"),
    onDelete: canDelete ? (n) => setTrashing(n) : undefined,
  };
  const filtered = filter.status !== "all" || filter.category || filter.severity || debounced;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
        <p className="flex flex-wrap items-center gap-x-3 text-base text-ink-muted">
          <span>{unread ? `${unread} unread` : "You're all caught up"}</span>
          <span className="inline-flex items-center gap-1.5 text-sm" role="status">
            <span aria-hidden className={`size-2 rounded-full ${live ? "bg-success" : "bg-brand-gray"}`} />
            {live ? "Live" : "Connecting…"}
          </span>
        </p>
        <div className="ml-auto flex flex-wrap gap-2.5">
          <Link to="/settings/notifications" className="btn-ghost">
            <Icon name="settings" size={18} />
            Preferences
          </Link>
          <button type="button" className="btn-primary" onClick={() => void inbox.readAll()} disabled={!unread}>
            <Icon name="checkAll" size={18} />
            Mark all as read
          </button>
        </div>
      </div>
      <NotificationFilters value={filter} onChange={setFilter} />
      {/* On phones the notification tiles sit straight on the page (no card around them), so the
          text gets the full width; from sm up they share one card like other list screens. */}
      <div className="sm:card sm:p-6">
        {inbox.state === "loading" && inbox.items.length === 0 ? (
          <div className="space-y-2" role="status" aria-label="Loading notifications">
            {[0, 1, 2, 3].map((i) => <NotificationCardSkeleton key={i} />)}
          </div>
        ) : inbox.state === "error" ? (
          <ErrorState title="Couldn't load notifications" detail="Check your connection and try again." onRetry={inbox.load} />
        ) : groups.length === 0 ? (
          <div className="grid place-items-center gap-2 px-6 py-12 text-center">
            <span aria-hidden className="mb-1 grid size-12 place-items-center rounded-full bg-subtle text-ink-muted">
              <Icon name={filtered ? "search" : "bell"} size={22} />
            </span>
            <div className="font-medium">{filtered ? "Nothing matches these filters" : "No notifications yet"}</div>
            <p className="max-w-sm text-sm text-ink-muted">
              {filtered ? "Try another category or priority, or clear the search." : "Orders, invoices, payments, receiving, scans and account alerts for your areas will appear here."}
            </p>
            {filtered && (
              <button type="button" className="btn-ghost mt-2" onClick={() => setFilter({ status: "all", category: "", severity: "", q: "" })}>
                Show all notifications
              </button>
            )}
          </div>
        ) : (
          <div className="space-y-6">
            {groups.map((g) => (
              <NotificationGroup key={g.key} group={g} grouped={(prefs?.grouped ?? true) && filter.status !== "archived"} actions={actions} onClearStack={(items) => void inbox.archiveMany(items)} />
            ))}
            {inbox.hasMore && (
              <div ref={sentinel} className="flex justify-center pt-2">
                <button type="button" className="btn-ghost" onClick={loadMore} disabled={inbox.loadingMore}>
                  {inbox.loadingMore ? "Loading…" : "Load older"}
                </button>
              </div>
            )}
          </div>
        )}
      </div>
      {trashing && (
        <DeleteAction kind="notification" id={trashing.id} label={trashing.title} open onClose={() => setTrashing(null)} />
      )}
    </div>
  );
}
