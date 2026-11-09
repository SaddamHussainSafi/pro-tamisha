import { useEffect, useId, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { badge, CATEGORY_ICON, safeLink, SEVERITY } from "../../lib/notifications";
import { notificationsApi } from "../../lib/notificationsApi";
import { Icon } from "../../ui/Icon";
import { NotificationPopover } from "./NotificationPopover";
import { useNotifications } from "./NotificationProvider";

/** Top-bar bell with the unread count; opens the notification panel. */
export function NotificationBell() {
  const { summary } = useNotifications();
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const loc = useLocation();
  const unread = summary?.unread ?? 0;
  const critical = summary?.critical_unread ?? 0;

  // Moving to another screen closes the panel (e.g. after opening a notification).
  useEffect(() => setOpen(false), [loc.pathname]);

  const label = unread ? `Notifications, ${unread} unread${critical ? `, ${critical} critical` : ""}` : "Notifications";
  return (
    <>
      <button
        ref={btn}
        type="button"
        className={`icon-btn relative ${open ? "border-brand-secondary" : ""}`}
        aria-label={label}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((o) => !o)}
        data-testid="notification-bell"
      >
        <Icon name="bell" />
        {unread > 0 && (
          <span
            aria-hidden
            className={`num absolute -right-1 -top-1 grid h-5 min-w-5 place-items-center rounded-full px-1.5 text-[11px] font-semibold leading-none text-white ${critical ? "bg-danger" : "bg-brand-primary"}`}
          >
            {badge(unread)}
          </span>
        )}
      </button>
      {open && <NotificationPopover id={panelId} anchor={btn} onClose={() => setOpen(false)} />}
      <LiveRegion />
      {!open && <Banner />}
    </>
  );
}

/** Screen readers hear new notifications without the banner taking focus. */
function LiveRegion() {
  const { subscribe } = useNotifications();
  const [text, setText] = useState("");
  useEffect(() => subscribe((e) => e.type === "notification" && setText(`New notification: ${e.item.title}`)), [subscribe]);
  return (
    // aria-live without role="status": the shell is on every screen, and screens (and their tests)
    // own the one status message per page.
    <div className="sr-only" aria-live="polite" aria-atomic="true">
      {text}
    </div>
  );
}

/** A new notification slides in at the top right for a few seconds (never during quiet hours). */
function Banner() {
  const { banner, dismissBanner, refreshSummary } = useNotifications();
  if (!banner) return null;
  const tone = SEVERITY[banner.severity];
  const to = safeLink(banner.link);
  const open = () => {
    void notificationsApi.read(banner.id).then(refreshSummary, () => undefined);
    dismissBanner();
  };
  return (
    <div
      key={banner.id}
      aria-hidden
      className="pt-slide-in fixed right-3 top-[4.5rem] z-30 hidden w-[23rem] gap-3 rounded-ctl border border-line bg-card p-4 shadow-[0_8px_24px_rgb(24_24_27/0.12)] sm:right-6 sm:top-[5rem] sm:flex"
      data-testid="notification-banner"
    >
      <span className={`grid size-10 shrink-0 place-items-center rounded-ctl ${tone.tile}`}>
        <Icon name={CATEGORY_ICON[banner.category] ?? "bell"} size={19} className={tone.icon} />
      </span>
      <div className="min-w-0 flex-1 pr-6">
        {to ? (
          <Link to={to} onClick={open} tabIndex={-1} className="block font-semibold leading-snug after:absolute after:inset-0 after:content-['']">
            {banner.title}
          </Link>
        ) : (
          <p className="font-semibold leading-snug">{banner.title}</p>
        )}
        {banner.body && <p className="mt-0.5 line-clamp-2 text-sm text-ink-muted">{banner.body}</p>}
        <p className="mt-1 text-xs text-ink-muted">{banner.source_module} · now</p>
      </div>
      <button type="button" tabIndex={-1} onClick={dismissBanner} className="absolute right-2 top-2 z-10 grid size-8 cursor-pointer place-items-center rounded-full text-ink-muted hover:bg-subtle hover:text-ink" aria-label="Close">
        <Icon name="x" size={15} />
      </button>
    </div>
  );
}
