import { Link } from "react-router-dom";
import { CATEGORY_ICON, fullTime, relativeTime, safeLink, SEVERITY } from "../../lib/notifications";
import type { AppNotification } from "../../lib/notificationsApi";
import { Icon } from "../../ui/Icon";

export type CardActions = {
  onOpen: (n: AppNotification) => void;
  onToggleRead: (n: AppNotification) => void;
  onArchive: (n: AppNotification) => void;
  onRestore?: (n: AppNotification) => void;
  /** Super Admin only (Notifications page): move the notification to the Trash. */
  onDelete?: (n: AppNotification) => void;
};

const ACTION = "grid size-9 cursor-pointer place-items-center rounded-full text-ink-muted transition-colors duration-150 hover:bg-subtle hover:text-ink";

/**
 * One notification as a calm tile: icon in its priority tone, title, a short message, where it
 * came from, when, and an unread dot. The whole tile opens the related record; read/unread and
 * dismiss are separate buttons (never nested inside the link).
 */
export function NotificationCard({ n, actions, now, className = "" }: { n: AppNotification; actions: CardActions; now?: Date; className?: string }) {
  const tone = SEVERITY[n.severity];
  const to = safeLink(n.link);
  const flagged = n.severity === "critical" || n.severity === "warning";
  const titleCls = `block text-[15px] leading-snug text-ink ${n.read ? "font-medium" : "font-semibold"} after:absolute after:inset-0 after:rounded-ctl after:content-[''] focus-visible:outline-none`;
  return (
    <article
      aria-label={`${n.read ? "" : "Unread. "}${tone.label}: ${n.title}`}
      data-testid="notification-card"
      data-unread={!n.read || undefined}
      className={`group relative flex gap-3.5 rounded-ctl border border-line bg-card p-4 transition-colors duration-150 hover:border-brand-gray/50 has-[a:focus-visible]:outline-2 has-[a:focus-visible]:outline-offset-2 has-[a:focus-visible]:outline-brand-primary has-[button[data-open]:focus-visible]:outline-2 has-[button[data-open]:focus-visible]:outline-offset-2 has-[button[data-open]:focus-visible]:outline-brand-primary ${className}`}
    >
      <span aria-hidden className={`grid size-10 shrink-0 place-items-center rounded-ctl ${tone.tile}`}>
        <Icon name={CATEGORY_ICON[n.category] ?? "bell"} size={19} className={tone.icon} />
      </span>
      <div className="min-w-0 flex-1">
        <div className={`flex items-start gap-2 ${actions.onDelete ? "pr-24" : "pr-16"}`}>
          {to ? (
            <Link to={to} className={titleCls} onClick={() => actions.onOpen(n)}>
              {n.title}
            </Link>
          ) : (
            <button type="button" data-open className={`${titleCls} cursor-pointer text-left`} onClick={() => actions.onOpen(n)}>
              {n.title}
            </button>
          )}
        </div>
        {n.body && <p className="mt-0.5 line-clamp-2 text-sm text-ink-muted">{n.body}</p>}
        <p className="mt-1.5 flex flex-wrap items-center gap-x-1.5 text-xs text-ink-muted">
          {flagged && <span className={`font-semibold ${n.severity === "critical" ? "text-danger" : "text-warning"}`}>{tone.label} ·</span>}
          <span>{n.source_module}</span>
          <span aria-hidden>·</span>
          <time dateTime={n.created_at} title={fullTime(n.created_at)}>
            {relativeTime(n.created_at, now)}
          </time>
        </p>
      </div>
      {/* Read dot + actions sit above the stretched link. */}
      <div className="absolute right-2.5 top-2.5 z-10 flex items-center gap-0.5">
        {actions.onDelete && (
          <button
            type="button"
            className={`${ACTION} hover:text-danger opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:focus-visible:opacity-100`}
            onClick={() => actions.onDelete?.(n)}
            aria-label={`Delete “${n.title}”`}
            title="Delete"
          >
            <Icon name="trash" size={16} />
          </button>
        )}
        {n.archived && actions.onRestore ? (
          <button type="button" className={ACTION} onClick={() => actions.onRestore?.(n)} aria-label={`Restore “${n.title}” to the inbox`} title="Restore">
            <Icon name="refresh" size={16} />
          </button>
        ) : (
          <>
            <button
              type="button"
              className={`${ACTION} opacity-100 sm:opacity-0 sm:group-hover:opacity-100 sm:focus-visible:opacity-100`}
              onClick={() => actions.onArchive(n)}
              aria-label={`Dismiss “${n.title}”`}
              title="Dismiss"
            >
              <Icon name="x" size={16} />
            </button>
            <button
              type="button"
              className={ACTION}
              onClick={() => actions.onToggleRead(n)}
              aria-label={n.read ? `Mark “${n.title}” as unread` : `Mark “${n.title}” as read`}
              title={n.read ? "Mark as unread" : "Mark as read"}
            >
              <span aria-hidden className={`size-2.5 rounded-full transition-colors duration-150 ${n.read ? "border border-brand-gray/70 bg-transparent" : "bg-brand-primary"}`} />
            </button>
          </>
        )}
      </div>
    </article>
  );
}

/** Same footprint as a card, for loading states. */
export function NotificationCardSkeleton() {
  return (
    <div aria-hidden className="flex gap-3.5 rounded-ctl border border-line bg-card p-4">
      <div className="size-10 shrink-0 animate-pulse rounded-ctl bg-line/80" />
      <div className="flex-1 space-y-2 pt-1">
        <div className="skeleton h-3.5 w-3/5" />
        <div className="skeleton h-3 w-4/5" />
        <div className="skeleton h-2.5 w-1/3" />
      </div>
    </div>
  );
}
