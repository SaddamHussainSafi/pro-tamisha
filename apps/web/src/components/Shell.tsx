import { Suspense, useEffect, useRef, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { api, has, type Warehouse } from "../api";
import { useAuth } from "../auth";
import { roleLabel } from "../lib/staff";
import { NAV_GROUPS, SETTINGS, type NavItem } from "../nav";
import { Icon } from "../ui/Icon";
import { PageSkeleton } from "../ui/primitives";
import { NotificationBell } from "./notifications/NotificationBell";
import { NotificationProvider } from "./notifications/NotificationProvider";
import { QuickJump } from "./QuickJump";
import { Wordmark } from "./Wordmark";

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
      </NavLink>
    );

  const tab = new URLSearchParams(loc.search).get("tab") ?? "stock";
  return (
    <>
      <div className="flex items-center gap-1">
        <NavLink to={item.to} end={item.to === "/"} className={({ isActive }) => `${ROW} min-w-0 flex-1 ${isActive ? ON : OFF}`}>
          <Icon name={item.icon} size={22} />
          <span className="truncate">{item.label}</span>
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
      {item.children && open && (
        <ul className="mb-1 ml-[1.35rem] mt-0.5 animate-fade space-y-0.5 border-l-2 border-line pl-3">
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
      )}
    </>
  );
}

/* ------------------------------- panel ------------------------------- */

function SidebarPanel({ collapsed, onToggle, onClose }: { collapsed: boolean; onToggle?: () => void; onClose?: () => void }) {
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
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-expanded={!collapsed}
          >
            <Icon name={collapsed ? "chevron" : "chevronLeft"} size={18} />
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
              {g.items.map((item) => <li key={item.to}><Item item={item} collapsed={collapsed} /></li>)}
            </ul>
          </div>
        ))}
      </nav>

      <div className={`shrink-0 space-y-1 border-t border-line p-3 ${collapsed ? "flex flex-col items-center" : ""}`}>
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
  const [collapsed, setCollapsed] = useState(recall);
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const jump = useRef<HTMLInputElement>(null);
  const main = useRef<HTMLElement>(null);
  const menuBtn = useRef<HTMLButtonElement>(null);
  const drawerEl = useRef<HTMLElement>(null);

  useEffect(() => {
    api.warehouses().then(setWarehouses).catch(() => setWarehouses([]));
  }, []);
  // A new screen starts at the top (the page scrolls inside <main>, which the router doesn't reset).
  useEffect(() => {
    setDrawer(false);
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

  // No warehouse picker: the scanner hint follows the first warehouse the user can see.
  const active = warehouses[0];
  const name = session?.employee.display_name;

  return (
    <NotificationProvider>
    <div className="flex h-full">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded-full focus:bg-card focus:px-4 focus:py-2">Skip to content</a>

      <aside aria-label="Sections" className={`hidden shrink-0 transition-[width] duration-200 ease-out lg:flex ${collapsed ? "w-[88px]" : "w-[276px]"}`}>
        <div className="w-full">
          <SidebarPanel collapsed={collapsed} onToggle={() => setCollapsed((c) => { remember(!c); return !c; })} />
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex shrink-0 items-center gap-2 px-3 pt-3 sm:gap-3 sm:px-6 sm:pt-4">
          <button ref={menuBtn} className="icon-btn border-transparent lg:hidden" onClick={() => setDrawer(true)} aria-label="Open navigation" aria-expanded={drawer}>
            <Icon name="menu" />
          </button>
          <div className="shrink-0 rounded-full bg-card p-1.5 pr-5 lg:hidden"><Wordmark /></div>
          <div className="ml-auto flex min-w-0 items-center gap-3">
            <ScannerIndicator warehouseId={active?.id} />
            <div className="hidden md:block"><QuickJump ref={jump} /></div>
            <NotificationBell />
            <span aria-hidden title={name} className="grid size-12 place-items-center rounded-full bg-card text-sm font-semibold text-brand-primary-hover lg:hidden">{initials(name)}</span>
          </div>
        </header>
        <main ref={main} id="main" className="min-w-0 flex-1 overflow-y-auto px-3 pb-6 pt-4 sm:px-6 sm:pb-8 sm:pt-5">
          {/* Keyed by path: each screen fades in once when you arrive (not on every data refresh). */}
          <div key={loc.pathname} className="animate-enter">
            <Suspense fallback={<PageSkeleton />}>
              <Outlet />
            </Suspense>
          </div>
        </main>
      </div>

      {drawer && (
        <>
          <div className="fixed inset-0 z-30 animate-fade bg-ink/40 lg:hidden" onClick={() => setDrawer(false)} aria-hidden />
          <aside ref={drawerEl} aria-label="Navigation" className="fixed inset-y-0 left-0 z-40 w-[276px] max-w-[88vw] animate-drawer lg:hidden">
            <SidebarPanel collapsed={false} onClose={() => setDrawer(false)} />
          </aside>
        </>
      )}
    </div>
    </NotificationProvider>
  );
}

/** Header hint from the API's device heartbeats (no localhost probing from every page). */
function ScannerIndicator({ warehouseId }: { warehouseId: string | undefined }) {
  const { session } = useAuth();
  const allowed = !!session && (has(session, "scan.create") || has(session, "scan.device.manage"));
  const [state, setState] = useState<"none" | "online" | "offline" | null>(null);
  useEffect(() => {
    if (!allowed || !warehouseId) return;
    let live = true;
    const load = () =>
      api.scanDevices({ warehouse_id: warehouseId }).then(
        (d) => {
          const active = d.filter((x) => x.status === "ACTIVE");
          if (live) setState(active.length === 0 ? "none" : active.some((x) => x.online && x.scanner_state === "READY") ? "online" : "offline");
        },
        () => live && setState(null),
      );
    void load();
    const t = setInterval(() => void load(), 60000);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, [allowed, warehouseId]);
  if (!allowed || state === null) return null;
  const text = state === "online" ? "Scanner: ready" : state === "offline" ? "Scanner: offline" : "Scanner: not set up";
  return (
    <NavLink to="/scan" className="hidden items-center gap-2 rounded-full px-3 py-2.5 text-sm text-ink-muted transition-colors duration-150 hover:bg-card hover:text-ink xl:flex">
      <span className={`size-2 rounded-full ${state === "online" ? "bg-success" : "bg-brand-gray"}`} aria-hidden />
      {text}
    </NavLink>
  );
}
