import { Suspense, useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { has } from "../api";
import { useAuth } from "../auth";
import { roleLabel } from "../lib/staff";
import { NAV_GROUPS, SETTINGS, TRASH, type NavItem } from "../nav";
import { canManageTrash } from "../lib/trash";
import { TrashToastHost } from "../ui/DeleteAction";
import { ConnectionBanner } from "./ConnectionBanner";
import { ErrorBoundary } from "./ErrorBoundary";
import { SessionExpiredDialog } from "./SessionExpired";
import { Icon } from "../ui/Icon";
import { PageSkeleton } from "../ui/primitives";
import { NotificationBell } from "./notifications/NotificationBell";
import { OrderConnectBadge } from "./OrderConnectLive";
import { MailBadge } from "./mail/MailBadge";
import { NotificationProvider } from "./notifications/NotificationProvider";
import { QuickJump, SearchButton } from "./QuickJump";
import { Wordmark } from "./Wordmark";

/** "1" = the sidebar hides itself and slides in when the pointer reaches the left edge. */
const COLLAPSE_KEY = "pt.sidebar.collapsed";

function remember(value: boolean) {
  try {
    localStorage.setItem(COLLAPSE_KEY, value ? "1" : "0");
  } catch {
    /* storage unavailable: the choice just isn't remembered */
  }
}
function recall(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

function initials(name: string | undefined) {
  if (!name) return "?";
  const parts = name.replace(/[^\p{L}\p{N}\s]/gu, "").trim().split(/\s+/);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "")).toUpperCase();
}

const soon = (item: NavItem) => `${item.label} — available when the ${item.module} module ships`;
const ROW = "flex items-center gap-3 rounded-ctl px-3 py-2.5 text-[15px] font-medium transition-colors duration-150";
const ON = "bg-brand-accent/12 text-brand-primary-hover [&_svg]:text-brand-primary";
const OFF = "text-ink hover:bg-brand-accent/8";
const SQUARE = "relative grid size-12 place-items-center rounded-ctl transition-colors duration-150";

/* ------------------------------- items ------------------------------- */

function Item({ item, collapsed }: { item: NavItem; collapsed: boolean }) {
  const loc = useLocation();
  const inside = !!item.children && loc.pathname.startsWith(item.to);
  const [open, setOpen] = useState(inside);
  useEffect(() => {
    if (inside) setOpen(true);
  }, [inside]);

  if (item.module)
    return (
      <span role="link" aria-disabled="true" tabIndex={0} title={soon(item)} className={`${collapsed ? SQUARE : ROW} cursor-not-allowed text-ink-muted/60`}>
        <Icon name={item.icon} size={22} />
        {collapsed ? (
          <span className="sr-only">{soon(item)}</span>
        ) : (
          <>
            <span className="flex-1 truncate">{item.label}</span>
            <span className="sr-only"> — available when the {item.module} module ships</span>
            <span aria-hidden className="rounded-full bg-subtle px-2 py-0.5 text-[11px] font-medium text-ink-muted">Soon</span>
          </>
        )}
      </span>
    );

  if (collapsed)
    return (
      <NavLink to={item.to} end={item.to === "/"} title={item.label} className={({ isActive }) => `${SQUARE} ${isActive ? ON : OFF}`}>
        <Icon name={item.icon} size={22} />
        <span className="sr-only">{item.label}</span>
        {item.badge === "order_connect" && <OrderConnectBadge collapsed />}
        {item.badge === "mail" && <MailBadge collapsed />}
      </NavLink>
    );

  const tab = new URLSearchParams(loc.search).get("tab") ?? "stock";
  return (
    <>
      <div className="flex items-center gap-1">
        <NavLink to={item.to} end={item.to === "/"} className={({ isActive }) => `${ROW} relative min-w-0 flex-1 ${isActive ? ON : OFF}`}>
          <Icon name={item.icon} size={22} />
          <span className="truncate">{item.label}</span>
          {item.badge === "order_connect" && <OrderConnectBadge collapsed={false} />}
          {item.badge === "mail" && <MailBadge collapsed={false} />}
        </NavLink>
        {item.children && (
          <button
            type="button"
            className="grid size-10 shrink-0 cursor-pointer place-items-center rounded-ctl text-ink-muted transition-colors duration-150 hover:bg-subtle hover:text-ink"
            aria-expanded={open}
            aria-label={`${item.label} sections`}
            onClick={() => setOpen((o) => !o)}
          >
            <Icon name="chevronDown" size={18} className={`transition-transform duration-200 ${open ? "rotate-180" : ""}`} />
          </button>
        )}
      </div>
      {item.children && (
        // Opens and closes by height (grid rows 0fr → 1fr), so the items below glide instead of jumping.
        <div inert={!open} className={`grid transition-[grid-template-rows,opacity,visibility] duration-300 ease-[var(--ease-calm)] ${open ? "visible grid-rows-[1fr] opacity-100" : "invisible grid-rows-[0fr] opacity-0"}`}>
        <div className="min-h-0 overflow-hidden">
        <ul className="mb-1 ml-[1.35rem] mt-0.5 space-y-0.5 border-l-2 border-line pl-3">
          {item.children.map((c) => {
            const [path, query] = c.to.split("?");
            const want = new URLSearchParams(query ?? "").get("tab") ?? "stock";
            const active = loc.pathname === path && want === tab;
            return (
              <li key={c.to}>
                <Link
                  to={c.to}
                  aria-current={active ? "page" : undefined}
                  className={`block rounded-ctl px-3 py-2 text-sm transition-colors duration-150 ${active ? "bg-brand-accent/12 font-semibold text-brand-primary-hover" : "text-ink-muted hover:bg-subtle hover:text-ink"}`}
                >
                  {c.label}
                </Link>
              </li>
            );
          })}
        </ul>
        </div>
        </div>
      )}
    </>
  );
}

/* ------------------------------- panel ------------------------------- */

function SidebarPanel({ collapsed, autoHide = false, onToggle, onClose }: { collapsed: boolean; autoHide?: boolean; onToggle?: () => void; onClose?: () => void }) {
  const { session, logout } = useAuth();
  const name = session?.employee.display_name;
  const role = session?.roles?.length ? session.roles.map(roleLabel).join(", ") : session?.employee.email;
  return (
    <div className="flex h-full min-h-0 flex-col border-r border-line bg-card">
      <div className={`flex shrink-0 items-center gap-2 p-4 ${collapsed ? "flex-col" : ""}`}>
        <Link to="/" className={`rounded-full ${collapsed ? "" : "mr-auto"}`}>
          <Wordmark compact={collapsed} />
        </Link>
        {onToggle && (
          <button
            type="button"
            className="grid size-10 shrink-0 cursor-pointer place-items-center rounded-ctl bg-subtle text-ink transition-colors duration-150 hover:bg-line"
            onClick={onToggle}
            aria-label={autoHide ? "Keep sidebar open" : "Auto-hide sidebar"}
            title={autoHide ? "Keep the sidebar open" : "Hide the sidebar; it slides in when you point at the left edge"}
            aria-pressed={autoHide}
          >
            <Icon name={autoHide ? "chevron" : "chevronLeft"} size={18} />
          </button>
        )}
        {onClose && (
          <button type="button" className="icon-btn size-10" onClick={onClose} aria-label="Close navigation" data-autofocus>
            <Icon name="x" />
          </button>
        )}
      </div>

      <nav aria-label="Primary" className="min-h-0 flex-1 overflow-y-auto px-3 pb-2">
        {NAV_GROUPS.map((g, gi) => (
          <div key={g.title ?? "top"} className={gi > 0 ? "mt-2 border-t border-line pt-2" : ""}>
            {g.title && !collapsed && <div className="px-3 pb-1 pt-2 text-xs font-medium text-ink-muted">{g.title}</div>}
            <ul className={`space-y-0.5 ${collapsed ? "flex flex-col items-center" : ""}`}>
              {g.items.filter((item) => !item.perm || (!!session && has(session, item.perm))).map((item) => <li key={item.to}><Item item={item} collapsed={collapsed} /></li>)}
            </ul>
          </div>
        ))}
      </nav>

      <div className={`shrink-0 space-y-1 border-t border-line p-3 ${collapsed ? "flex flex-col items-center" : ""}`}>
        {session && canManageTrash(session) && <Item item={TRASH} collapsed={collapsed} />}
        <Item item={SETTINGS} collapsed={collapsed} />
        <div className={`mt-2 flex items-center gap-3 rounded-ctl ${collapsed ? "flex-col" : "border border-line p-2"}`} title={collapsed ? name : undefined}>
          <span aria-hidden className="grid size-11 shrink-0 place-items-center rounded-full bg-brand-accent/15 text-sm font-semibold text-brand-primary-hover">{initials(name)}</span>
          {!collapsed && (
            <div className="min-w-0 flex-1 leading-tight">
              <div className="truncate text-[15px] font-semibold" data-testid="user-name">{name}</div>
              <div className="truncate text-sm text-ink-muted">{role}</div>
            </div>
          )}
          <button type="button" className="grid size-10 shrink-0 cursor-pointer place-items-center rounded-ctl text-ink transition-colors duration-150 hover:bg-subtle" onClick={() => void logout()} aria-label="Sign out" title="Sign out">
            <Icon name="logout" />
          </button>
        </div>
      </div>
    </div>
  );
}

export function Shell() {
  const { session } = useAuth();
  const loc = useLocation();
  const [drawer, setDrawer] = useState(false);
  const [autoHide, setAutoHide] = useState(recall);
  const [peek, setPeek] = useState(false);
  // `wide`: the full layout is drawn. It switches on at once when the rail opens and only
  // after the closing slide has finished, so nothing re-flows while it moves.
  const [wide, setWide] = useState(false);
  const hideTimer = useRef<number | undefined>(undefined);
  const narrowTimer = useRef<number | undefined>(undefined);
  const show = () => {
    window.clearTimeout(hideTimer.current);
    window.clearTimeout(narrowTimer.current);
    setWide(true);
    setPeek(true);
  };
  const hideNow = () => {
    window.clearTimeout(hideTimer.current);
    window.clearTimeout(narrowTimer.current);
    setPeek(false);
    narrowTimer.current = window.setTimeout(() => setWide(false), 320);
  };
  const hideSoon = () => {
    window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(hideNow, 250);
  };
  useEffect(
    () => () => {
      window.clearTimeout(hideTimer.current);
      window.clearTimeout(narrowTimer.current);
    },
    [],
  );
  const jump = useRef<HTMLInputElement>(null);
  const main = useRef<HTMLElement>(null);
  const menuBtn = useRef<HTMLButtonElement>(null);
  const drawerEl = useRef<HTMLElement>(null);

  // A new screen starts at the top (the page scrolls inside <main>, which the router doesn't reset).
  useEffect(() => {
    setDrawer(false);
    setPeek(false);
    setWide(false);
    main.current?.scrollTo({ top: 0 });
  }, [loc.pathname]);
  // Drawer: focus moves into it when it opens and back to the menu button when it closes.
  const wasOpen = useRef(false);
  useEffect(() => {
    if (drawer) drawerEl.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    else if (wasOpen.current) menuBtn.current?.focus();
    wasOpen.current = drawer;
  }, [drawer]);
  useEffect(() => {
    // "/" focuses the screen's own search (or the Go-to box); Ctrl/⌘ K always opens Go-to; Esc closes the drawer.
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable;
      if (e.key === "Escape") setDrawer(false);
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        jump.current?.focus();
      } else if (e.key === "/" && !typing) {
        const own = document.querySelector<HTMLInputElement>("#main [data-page-search]");
        const target = own && own.offsetParent !== null ? own : jump.current;
        if (target) {
          e.preventDefault();
          target.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const name = session?.employee.display_name;

  return (
    <NotificationProvider>
    {/* The window itself must never scroll: only <main> does. `relative` + `overflow-clip`
        keep anything absolutely placed (e.g. a screen-reader-only label far down a long page)
        from stretching the document, which let a scroll drag the whole frame off-screen. */}
    <div className="relative flex h-full overflow-clip">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-full focus:bg-card focus:px-4 focus:py-2">Skip to content</a>

      {autoHide ? (
        // Auto-hide: the icon rail stays; pointing at it (or tabbing into it) widens it over the page.
        <aside
          aria-label="Sections"
          className="relative hidden w-[88px] shrink-0 lg:block"
          onMouseEnter={show}
          onMouseLeave={hideSoon}
          onFocus={show}
          onBlur={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null)) hideSoon();
          }}
          onKeyDown={(e) => e.key === "Escape" && hideNow()}
        >
          {/* Revealed with clip-path (no re-layout while it moves); the clip also limits where it can be pointed at. */}
          <div
            className="absolute inset-y-0 left-0 z-40 w-[276px] shadow-2xl transition-[clip-path] duration-300 ease-[var(--ease-calm)]"
            style={{ clipPath: peek ? "inset(0 -48px 0 0)" : "inset(0 188px 0 0)" }}
          >
            <div key={wide ? "wide" : "rail"} className={`h-full animate-fade ${wide ? "w-[276px]" : "w-[88px]"}`}>
              <SidebarPanel collapsed={!wide} autoHide onToggle={() => { remember(false); setAutoHide(false); hideNow(); }} />
            </div>
          </div>
        </aside>
      ) : (
        <aside aria-label="Sections" className="hidden w-[276px] shrink-0 lg:flex">
          <div className="w-full">
            <SidebarPanel collapsed={false} onToggle={() => { remember(true); setAutoHide(true); hideNow(); }} />
          </div>
        </aside>
      )}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex shrink-0 items-center gap-2 px-3 pt-3 sm:gap-3 sm:px-6 sm:pt-4">
          <button ref={menuBtn} className="icon-btn border-transparent lg:hidden" onClick={() => setDrawer(true)} aria-label="Open navigation" aria-expanded={drawer}>
            <Icon name="menu" />
          </button>
          <div className="shrink-0 rounded-full bg-card p-1.5 pr-5 lg:hidden"><Wordmark /></div>
          <div className="ml-auto flex min-w-0 items-center gap-3">
            <div className="hidden md:block"><QuickJump ref={jump} /></div>
            <div className="md:hidden"><SearchButton /></div>
            <NotificationBell />
            <span aria-hidden title={name} className="grid size-12 place-items-center rounded-full bg-card text-sm font-semibold text-brand-primary-hover lg:hidden">{initials(name)}</span>
          </div>
        </header>
        <main ref={main} id="main" className="relative min-w-0 flex-1 overflow-y-auto overscroll-contain px-3 pb-6 pt-4 sm:px-6 sm:pb-8 sm:pt-5">
          {/* Keyed by path: each screen fades in once when you arrive (not on every data refresh). */}
          <ConnectionBanner />
          <div key={loc.pathname} className="animate-enter">
            <ErrorBoundary scope="page" resetKey={loc.pathname}>
              <Suspense fallback={<PageSkeleton />}>
                <Outlet />
              </Suspense>
            </ErrorBoundary>
          </div>
        </main>
      </div>

      {/* Phone drawer: always mounted so it slides out as smoothly as it slides in. */}
      <div className={`fixed inset-0 z-30 bg-ink/40 transition-opacity duration-300 ease-[var(--ease-calm)] lg:hidden ${drawer ? "opacity-100" : "pointer-events-none opacity-0"}`} onClick={() => setDrawer(false)} aria-hidden />
      <aside
        ref={drawerEl}
        aria-label="Navigation"
        inert={!drawer}
        className={`fixed inset-y-0 left-0 z-40 w-[276px] max-w-[88vw] transition-[transform,visibility] duration-300 ease-[var(--ease-calm)] lg:hidden ${drawer ? "visible translate-x-0" : "invisible -translate-x-full"}`}
      >
        <SidebarPanel collapsed={false} onClose={() => setDrawer(false)} />
      </aside>
      <TrashToastHost />
      <SessionExpiredDialog />
    </div>
    </NotificationProvider>
  );
}
