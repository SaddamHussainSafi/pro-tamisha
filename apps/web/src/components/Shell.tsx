import { Suspense, useEffect, useRef, useState } from "react";
import { Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth";
import { initials } from "../lib/activity";
import { TrashToastHost } from "../ui/DeleteAction";
import { ConnectionBanner } from "./ConnectionBanner";
import { ErrorBoundary } from "./ErrorBoundary";
import { SessionExpiredDialog } from "./SessionExpired";
import { Icon } from "../ui/Icon";
import { PageSkeleton } from "../ui/primitives";
import { NotificationBell } from "./notifications/NotificationBell";
import { NotificationProvider } from "./notifications/NotificationProvider";
import { QuickJump, SearchButton } from "./QuickJump";
import { Wordmark } from "./Wordmark";
import { Breadcrumbs } from "./Breadcrumbs";
import { SectionViews } from "./SectionViews";
import { SidebarPanel } from "./Sidebar";

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

export function Shell() {
  const { session } = useAuth();
  const loc = useLocation();
  const [drawer, setDrawer] = useState(false);
  // The drawer's contents exist only while it is open or sliding shut (no second copy of the menu in the page).
  const [drawerShown, setDrawerShown] = useState(false);
  const [autoHide, setAutoHide] = useState(recall);
  const [peek, setPeek] = useState(false);
  // `wide`: the full layout is drawn. It switches on at once when the rail opens and only
  // after the closing slide has finished, so nothing re-flows while it moves.
  const [wide, setWide] = useState(false);
  const hideTimer = useRef<number | undefined>(undefined);
  const narrowTimer = useRef<number | undefined>(undefined);
  const railOpenedAt = useRef(-1e9);
  const show = () => {
    window.clearTimeout(hideTimer.current);
    window.clearTimeout(narrowTimer.current);
    if (!peek) railOpenedAt.current = performance.now();
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
        // Auto-hide: the icon rail stays; pointing at it (or tabbing into it) slides it open and
        // pushes the page to the right, and pulls the page back when it closes. Never covers the page.
        <aside
          aria-label="Sections"
          className={`hidden shrink-0 overflow-hidden transition-[width] duration-300 ease-[var(--ease-calm)] lg:block ${peek ? "w-[276px]" : "w-[88px]"}`}
          onMouseEnter={show}
          onMouseLeave={hideSoon}
          onFocus={show}
          onBlur={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null)) hideSoon();
          }}
          onKeyDown={(e) => e.key === "Escape" && hideNow()}
        >
          {/* One panel for the rail and the open sidebar (no re-mount: keyboard focus and taps survive the slide). */}
          <div className={`h-full ${wide ? "w-[276px]" : "w-[88px]"}`}>
            <SidebarPanel collapsed={!wide} autoHide railOpenedAt={railOpenedAt} onExpand={show} onToggle={() => { remember(false); setAutoHide(false); hideNow(); }} />
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
        <header className="flex shrink-0 items-center gap-2 px-3 py-3 sm:gap-3 sm:px-6 sm:py-4">
          <button ref={menuBtn} className="icon-btn border-transparent lg:hidden" onClick={() => { setDrawerShown(true); setDrawer(true); }} aria-label="Open navigation" aria-expanded={drawer}>
            <Icon name="menu" />
          </button>
          <div className="shrink-0 rounded-full bg-card p-1.5 pr-5 lg:hidden"><Wordmark /></div>
          <Breadcrumbs place="header" />
          <div className="ml-auto flex min-w-0 items-center gap-3">
            <div className="hidden md:block"><QuickJump ref={jump} /></div>
            <div className="md:hidden"><SearchButton /></div>
            <NotificationBell />
            <span aria-hidden title={name} className="grid size-12 place-items-center rounded-full bg-card text-sm font-semibold text-brand-primary-hover lg:hidden">{name ? initials(name) : "?"}</span>
          </div>
        </header>
        <main ref={main} id="main" className="relative min-w-0 flex-1 overflow-y-auto overscroll-contain px-3 pb-6 pt-1 sm:px-6 sm:pb-8 sm:pt-2">
          {/* Keyed by path: each screen fades in once when you arrive (not on every data refresh). */}
          <ConnectionBanner />
          <Breadcrumbs place="page" />
          <SectionViews />
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
        onTransitionEnd={(e) => e.target === e.currentTarget && !drawer && setDrawerShown(false)}
        className={`fixed inset-y-0 left-0 z-40 w-[276px] max-w-[88vw] transition-[transform,visibility] duration-300 ease-[var(--ease-calm)] lg:hidden ${drawer ? "visible translate-x-0" : "invisible -translate-x-full"}`}
      >
        {drawerShown && <SidebarPanel collapsed={false} onClose={() => setDrawer(false)} />}
      </aside>
      <TrashToastHost />
      <SessionExpiredDialog />
    </div>
    </NotificationProvider>
  );
}
