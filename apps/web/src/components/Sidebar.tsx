import { useEffect, useId, useRef, useState, type RefObject } from "react";
import { Link, useLocation } from "react-router-dom";
import { has, hasCompanyWide, type Session } from "../api";
import { useAuth } from "../auth";
import { initials } from "../lib/activity";
import { roleLabel } from "../lib/staff";
import { canManageTrash } from "../lib/trash";
import { locate, NAV_FOOT, NAV_MAIN, type NavHit, type NavLeaf, type NavNode } from "../nav";
import { Icon } from "../ui/Icon";
import { MailBadge } from "./mail/MailBadge";
import { OrderConnectBadge } from "./OrderConnectLive";
import { Wordmark } from "./Wordmark";

/* The approved sidebar: logo on top, the menu scrolling in the middle, Settings and the user at
   the bottom. Sections open and close; the section of the current page is always opened. The
   same elements serve the wide sidebar and the icon rail, so focus is never lost between them. */

// Every top-level icon sits in its own 40 px box, 12 px from the row's edge: the same size and the
// same place on the minimized rail and in the open sidebar, so nothing jumps when it slides.
const ROW = "relative flex w-full items-center gap-2 rounded-ctl px-3 py-0.5 text-[15px] font-medium transition-colors duration-150";
const ICON_BOX = "grid size-10 shrink-0 place-items-center";
const ON = "bg-brand-accent/12 text-brand-primary-hover [&_svg]:text-brand-primary";
const PARENT = "text-brand-primary-hover [&_svg]:text-brand-primary hover:bg-brand-accent/8"; // the section of the current page
const OFF = "text-ink hover:bg-brand-accent/8";
const CHILD = "flex items-center gap-2 rounded-ctl px-3 py-2 text-sm transition-colors duration-150";

/* Which sections are open is remembered per browser. First visit: the menu open, Settings closed. */
const OPEN_KEY = "pt.nav.open";
const DEFAULT_OPEN = NAV_MAIN.filter((n) => n.children).map((n) => n.label);
function recallOpen(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(OPEN_KEY) ?? "null") as unknown;
    return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === "string") : DEFAULT_OPEN;
  } catch {
    return DEFAULT_OPEN;
  }
}
function rememberOpen(open: string[]) {
  try {
    localStorage.setItem(OPEN_KEY, JSON.stringify(open));
  } catch {
    /* storage unavailable: the choice just isn't remembered */
  }
}

/** Role-based visibility, as before: a permission, user management, or the Trash. */
const allowed = (s: Session, e: NavLeaf) =>
  (!e.perm || has(s, e.perm)) && (e.gate === "admin" ? hasCompanyWide(s, "employee.manage") : e.gate === "trash" ? canManageTrash(s) : true);

function Badge({ node, collapsed }: { node: NavNode; collapsed: boolean }) {
  return node.badge === "order_connect" ? <OrderConnectBadge collapsed={collapsed} /> : node.badge === "mail" ? <MailBadge collapsed={collapsed} /> : null;
}

function Leaf({ leaf, current }: { leaf: NavLeaf; current: boolean }) {
  if (leaf.to === null)
    return (
      <span role="link" aria-disabled="true" tabIndex={0} title={`${leaf.label}: not available yet`} className={`${CHILD} cursor-not-allowed text-ink-muted/70`}>
        <span className="min-w-0 flex-1 truncate">{leaf.label}</span>
        <span className="sr-only"> (not available yet)</span>
        <span aria-hidden className="rounded-full bg-subtle px-2 py-0.5 text-[11px] font-medium text-ink-muted">Soon</span>
      </span>
    );
  return (
    <Link to={leaf.to} aria-current={current ? "page" : undefined} className={`${CHILD} ${current ? "bg-brand-accent/12 font-semibold text-brand-primary-hover" : "text-ink-muted hover:bg-subtle hover:text-ink"}`}>
      <span className="min-w-0 flex-1 truncate">{leaf.label}</span>
    </Link>
  );
}

function Entry({
  node,
  session,
  hit,
  collapsed,
  open,
  onToggle,
}: {
  node: NavNode;
  session: Session;
  hit: NavHit | null;
  collapsed: boolean;
  open: boolean;
  onToggle: () => void;
}) {
  const listId = useId();
  const here = hit?.node === node;
  const selfCurrent = here && hit?.leaf == null; // a plain link, or a section's own page (Scan Center)
  const label = <span className={collapsed ? "sr-only" : "min-w-0 flex-1 animate-fade truncate text-left"}>{node.label}</span>;
  // On the rail the entries below are out of sight, so the section itself carries the highlight.
  const tone = selfCurrent || (here && collapsed) ? ON : here ? PARENT : OFF;

  if (!node.children)
    return (
      <Link to={node.to ?? "/"} aria-current={selfCurrent ? "page" : undefined} title={collapsed ? node.label : undefined} className={`${ROW} ${tone}`}>
        <span className={ICON_BOX}><Icon name={node.icon} size={24} /></span>
        {label}
        <Badge node={node} collapsed={collapsed} />
      </Link>
    );

  const kids = node.children.filter((c) => allowed(session, c));
  const shown = open && !collapsed;
  const chevron = <Icon name="chevronDown" size={18} className={`transition-transform duration-200 ${open ? "rotate-180" : ""}`} />;
  return (
    <>
      {node.to ? (
        // The label is a page of its own (Scan Center); the chevron opens and closes the entries.
        <div className="flex items-center gap-1">
          <Link to={node.to} aria-current={selfCurrent ? "page" : undefined} title={collapsed ? (node.self ?? node.label) : undefined} className={`${ROW} min-w-0 flex-1 ${tone}`}>
            <span className={ICON_BOX}><Icon name={node.icon} size={24} /></span>
            {label}
            <Badge node={node} collapsed={collapsed} />
          </Link>
          <button
            type="button"
            className={`size-10 shrink-0 cursor-pointer place-items-center rounded-ctl text-ink-muted transition-colors duration-150 hover:bg-subtle hover:text-ink ${collapsed ? "hidden" : "grid"}`}
            aria-expanded={open}
            aria-controls={listId}
            aria-label={`${node.label} menu`}
            onClick={onToggle}
          >
            {chevron}
          </button>
        </div>
      ) : (
        <button type="button" className={`${ROW} cursor-pointer ${tone}`} aria-expanded={open} aria-controls={listId} aria-label={`${node.label} menu`} title={collapsed ? node.label : undefined} onClick={onToggle}>
          <span className={ICON_BOX}><Icon name={node.icon} size={24} /></span>
          {label}
          <Badge node={node} collapsed={collapsed} />
          <span className={collapsed ? "hidden" : "text-ink-muted"}>{chevron}</span>
        </button>
      )}
      {/* Opens and closes by height (grid rows 0fr → 1fr), so the entries below glide instead of jumping. */}
      <div id={listId} inert={!shown} className={`grid transition-[grid-template-rows,opacity,visibility] duration-300 ease-[var(--ease-calm)] ${shown ? "visible grid-rows-[1fr] opacity-100" : "invisible grid-rows-[0fr] opacity-0"}`}>
        <div className="min-h-0 overflow-hidden">
          <ul className="mb-1 ml-8 mt-0.5 space-y-0.5 border-l-2 border-line pl-2">
            {kids.map((c) => (
              <li key={c.label}>
                <Leaf leaf={c} current={here && hit?.leaf === c} />
              </li>
            ))}
          </ul>
        </div>
      </div>
    </>
  );
}

export function SidebarPanel({
  collapsed,
  autoHide = false,
  onToggle,
  onClose,
  railOpenedAt,
  onExpand,
}: {
  collapsed: boolean;
  autoHide?: boolean;
  onToggle?: () => void;
  onClose?: () => void;
  /** When the icon rail last slid open (a tap on a rail icon opens its section rather than closing it). */
  railOpenedAt?: RefObject<number>;
  /** Slide the icon rail open (asked for when a section icon is used on the rail). */
  onExpand?: () => void;
}) {
  const { session, logout } = useAuth();
  const loc = useLocation();
  const hit = locate(loc.pathname, loc.search);
  const [open, setOpen] = useState(recallOpen);
  const section = hit?.node.children ? hit.node.label : null;
  // The section of the current page is opened on arrival, after a refresh and on a direct link.
  useEffect(() => {
    if (section) setOpen((o) => (o.includes(section) ? o : [...o, section]));
  }, [section]);
  // …and the current entry is brought into view when the menu is longer than the window.
  const panel = useRef<HTMLDivElement>(null);
  const where = `${hit?.node.label ?? ""}/${hit?.leaf?.label ?? ""}`;
  useEffect(() => {
    const t = window.setTimeout(() => panel.current?.querySelector("[aria-current=page]")?.scrollIntoView({ block: "nearest" }), 320);
    return () => window.clearTimeout(t);
  }, [where]);
  if (!session) return null;

  const toggle = (label: string) => {
    // On the icon rail a section's entries are out of sight: its icon slides the sidebar open and
    // shows them (a tap, a click or Enter), and never closes the section in the same move.
    const fromRail = collapsed || performance.now() - (railOpenedAt?.current ?? -1e9) < 600;
    if (collapsed) onExpand?.();
    const next = open.includes(label) ? (fromRail ? open : open.filter((x) => x !== label)) : [...open, label];
    rememberOpen(next);
    setOpen(next);
  };
  const entries = (nodes: NavNode[]) =>
    nodes
      .filter((n) => allowed(session, n) && (!n.children || n.children.some((c) => allowed(session, c))))
      .map((n) => (
        <li key={n.label}>
          <Entry node={n} session={session} hit={hit} collapsed={collapsed} open={open.includes(n.label)} onToggle={() => toggle(n.label)} />
        </li>
      ));

  const name = session.employee.display_name;
  const role = session.roles?.length ? session.roles.map(roleLabel).join(", ") : session.employee.email;
  return (
    <div ref={panel} className="flex h-full min-h-0 flex-col border-r border-line bg-card">
      <div className={`flex shrink-0 items-center gap-2 p-4 ${collapsed ? "flex-col" : ""}`}>
        <Link to="/" className={`rounded-full ${collapsed ? "" : "mr-auto"}`}>
          <Wordmark compact={collapsed} />
        </Link>
        {/* Not on the minimized rail: it appears with the rest when the sidebar slides open. */}
        {onToggle && !collapsed && (
          <button
            type="button"
            className="grid size-10 shrink-0 cursor-pointer place-items-center rounded-ctl bg-subtle text-ink transition-colors duration-150 hover:bg-line"
            onClick={onToggle}
            aria-label={autoHide ? "Keep sidebar open" : "Auto-hide sidebar"}
            title={autoHide ? "Keep the sidebar open" : "Shrink the sidebar to icons; it slides open when you point at it"}
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

      <nav aria-label="Primary" className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-3 pb-2">
        <ul className="space-y-0.5">{entries(NAV_MAIN)}</ul>
      </nav>

      <div className="max-h-[60%] shrink-0 overflow-y-auto overscroll-contain border-t border-line p-3">
        <nav aria-label="Settings and account">
          <ul className="space-y-0.5">{entries(NAV_FOOT)}</ul>
        </nav>
        <div className={`mt-2 flex items-center gap-2 rounded-ctl ${collapsed ? "flex-col" : "border border-line p-2"}`}>
          <Link to="/settings" aria-label={`Your profile: ${name}`} title="Your profile" className={`flex min-w-0 items-center gap-3 rounded-ctl ${collapsed ? "" : "flex-1"}`}>
            <span aria-hidden className="grid size-11 shrink-0 place-items-center rounded-full bg-brand-accent/15 text-sm font-semibold text-brand-primary-hover">{name ? initials(name) : "?"}</span>
            <span className={collapsed ? "sr-only" : "min-w-0 flex-1 leading-tight"}>
              <span className="block truncate text-[15px] font-semibold text-ink" data-testid="user-name">{name}</span>
              <span className="block truncate text-sm text-ink-muted">{role}</span>
            </span>
          </Link>
          <button type="button" className="grid size-10 shrink-0 cursor-pointer place-items-center rounded-ctl text-ink transition-colors duration-150 hover:bg-subtle" onClick={() => void logout()} aria-label="Sign out" title="Sign out">
            <Icon name="logout" size={24} />
          </button>
        </div>
      </div>
    </div>
  );
}
