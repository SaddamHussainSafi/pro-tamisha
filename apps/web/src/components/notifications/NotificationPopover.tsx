import { useEffect, useId, useRef, useState, type RefObject } from "react";
import { Link, useNavigate } from "react-router-dom";
import { groupByDay } from "../../lib/notifications";
import type { AppNotification } from "../../lib/notificationsApi";
import { Icon } from "../../ui/Icon";
import { ErrorState } from "../../ui/primitives";
import { type CardActions, NotificationCardSkeleton } from "./NotificationCard";
import { NotificationGroup } from "./NotificationGroup";
import { useNotifications } from "./NotificationProvider";
import { useInbox } from "./useInbox";

const SEG = "cursor-pointer rounded-full px-3.5 py-1.5 text-[13px] font-medium transition-colors duration-150";

/**
 * The bell's floating panel: ~400px, right-aligned under the top bar (a full-width sheet on
 * phones). Escape or a click/focus outside closes it and focus returns to the bell.
 */
export function NotificationPopover({ id, onClose, anchor }: { id: string; onClose: () => void; anchor: RefObject<HTMLElement | null> }) {
  const { summary, prefs } = useNotifications();
  const navigate = useNavigate();
  const [tab, setTab] = useState<"unread" | "all">(() => ((summary?.unread ?? 0) > 0 ? "unread" : "all"));
  const inbox = useInbox({ status: tab, limit: 30 });
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const [closing, setClosing] = useState(false);
  const [now, setNow] = useState(() => new Date());

  const close = (returnFocus = true) => {
    if (closing) return;
    setClosing(true);
    setTimeout(() => {
      onClose();
      if (returnFocus) anchor.current?.focus();
    }, 150);
  };
  const closeRef = useRef(close);
  closeRef.current = close;

  useEffect(() => {
    panel.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    const t = setInterval(() => setNow(new Date()), 30000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        closeRef.current();
      }
    };
    const onDown = (e: PointerEvent) => {
      const t = e.target as Node;
      if (panel.current?.contains(t) || anchor.current?.contains(t)) return;
      closeRef.current(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onDown);
    };
  }, [anchor]);

  const actions: CardActions = {
    onOpen: (n) => {
      if (!n.read) void inbox.act(n, "read");
      if (n.link) close(false);
    },
    onToggleRead: (n) => void inbox.act(n, n.read ? "unread" : "read"),
    onArchive: (n) => void inbox.act(n, "archive"),
  };
  const groups = groupByDay(inbox.items, now);
  const unread = summary?.unread ?? 0;

  return (
    <div
      ref={panel}
      id={id}
      role="dialog"
      aria-modal="false"
      aria-labelledby={titleId}
      onBlur={(e) => {
        const to = e.relatedTarget as Node | null;
        if (to && !panel.current?.contains(to) && !anchor.current?.contains(to)) close(false);
      }}
      className={`fixed inset-x-2 top-[4.25rem] bottom-2 z-40 flex flex-col overflow-hidden rounded-card border border-line bg-canvas text-ink shadow-[0_8px_24px_rgb(24_24_27/0.12)] sm:inset-x-auto sm:bottom-auto sm:right-6 sm:top-[5rem] sm:max-h-[min(42rem,calc(100dvh-6.5rem))] sm:w-[25rem] ${closing ? "pt-pop-out" : "animate-pop"}`}
      style={{ transformOrigin: "top right" }}
    >
      <div className="flex items-center gap-2 border-b border-line bg-card px-5 pb-3 pt-4">
        <h2 id={titleId} className="text-lg font-semibold tracking-tight">
          Notifications
        </h2>
        <span className="hidden whitespace-nowrap text-sm text-ink-muted sm:inline" aria-live="polite">
          {unread ? `${unread} unread` : ""}
        </span>
        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            className="grid size-10 cursor-pointer place-items-center rounded-full text-ink transition-colors duration-150 hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-40"
            onClick={() => void inbox.readAll()}
            disabled={!unread}
            aria-label="Mark all as read"
            title="Mark all as read"
          >
            <Icon name="checkAll" size={19} />
          </button>
          <Link to="/settings/notifications" onClick={() => close(false)} className="grid size-10 place-items-center rounded-full text-ink transition-colors duration-150 hover:bg-subtle" aria-label="Notification settings" title="Notification settings">
            <Icon name="settings" size={19} />
          </Link>
          <button type="button" className="grid size-10 cursor-pointer place-items-center rounded-full text-ink transition-colors duration-150 hover:bg-subtle sm:hidden" onClick={() => close()} aria-label="Close notifications">
            <Icon name="x" size={19} />
          </button>
        </div>
      </div>
      <div className="flex gap-1 border-b border-line bg-card px-5 pb-3" role="group" aria-label="Show">
        {(["unread", "all"] as const).map((t, i) => (
          <button
            key={t}
            type="button"
            data-autofocus={i === 0 ? true : undefined}
            aria-pressed={tab === t}
            onClick={() => setTab(t)}
            className={`${SEG} ${tab === t ? "bg-brand-secondary text-white" : "text-ink hover:bg-subtle"}`}
          >
            {t === "unread" ? "Unread" : "All"}
          </button>
        ))}
      </div>

      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto overscroll-contain px-3 py-4" aria-busy={inbox.state === "loading"}>
        {inbox.state === "loading" && inbox.items.length === 0 ? (
          <div className="space-y-2" role="status" aria-label="Loading notifications">
            {[0, 1, 2].map((i) => <NotificationCardSkeleton key={i} />)}
          </div>
        ) : inbox.state === "error" ? (
          <ErrorState title="Couldn't load notifications" detail="Check your connection and try again." onRetry={inbox.load} />
        ) : groups.length === 0 ? (
          <Empty tab={tab} onAll={() => setTab("all")} />
        ) : (
          groups.map((g) => (
            <NotificationGroup key={g.key} group={g} grouped={prefs?.grouped ?? true} actions={actions} onClearStack={(items: AppNotification[]) => void inbox.archiveMany(items)} now={now} />
          ))
        )}
        {inbox.hasMore && inbox.state === "ready" && (
          <div className="flex justify-center">
            <button type="button" className="btn-ghost px-4 py-2" onClick={inbox.loadMore} disabled={inbox.loadingMore}>
              {inbox.loadingMore ? "Loading…" : "Show older"}
            </button>
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 gap-1 border-t border-line bg-card px-3 py-2.5">
        {(
          [
            ["All notifications", "/activity?view=notifications"],
            ["Activity", "/activity"],
          ] as const
        ).map(([label, to]) => (
          <button
            key={to}
            type="button"
            className="flex cursor-pointer items-center justify-center gap-1.5 rounded-full px-3 py-2 text-sm font-medium text-ink transition-colors duration-150 hover:bg-subtle"
            onClick={() => {
              close(false);
              navigate(to);
            }}
          >
            {label}
            <Icon name="chevron" size={15} />
          </button>
        ))}
      </div>
    </div>
  );
}

function Empty({ tab, onAll }: { tab: "unread" | "all"; onAll: () => void }) {
  return (
    <div className="grid place-items-center gap-2 px-6 py-10 text-center">
      <span aria-hidden className="mb-1 grid size-12 place-items-center rounded-full bg-card text-ink-muted">
        <Icon name="bell" size={22} />
      </span>
      <div className="font-medium">{tab === "unread" ? "You're all caught up" : "No notifications yet"}</div>
      <p className="max-w-xs text-sm text-ink-muted">
        {tab === "unread" ? "New orders, payments and alerts will show up here." : "When something needs you, it will show up here."}
      </p>
      {tab === "unread" && (
        <button type="button" className="mt-1 cursor-pointer rounded-full px-3 py-1.5 text-sm font-medium text-brand-primary-hover hover:bg-card" onClick={onAll}>
          See earlier notifications
        </button>
      )}
    </div>
  );
}
