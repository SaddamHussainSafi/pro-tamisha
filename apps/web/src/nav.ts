import type { IconName } from "./ui/Icon";

// The approved menu (owner, 2026-11-13). This file is the one place that says where every screen
// sits: the sidebar, the highlighted entry, the breadcrumbs and the ⌘K screen list all read it.

export type NavLeaf = {
  label: string;
  /** Address (a `?key=value` part must match too). null = no page yet: shown as "Soon". */
  to: string | null;
  /** Other addresses that belong to this destination (highlight and breadcrumbs). */
  owns?: string[];
  /** Shown only with this permission. */
  perm?: string;
  /** Shown only to: people who manage users / people who may open the Trash. */
  gate?: "admin" | "trash";
  /** Part of the approved menu, but not shown yet (owner, 2026-11-13: hide until its page exists). */
  hidden?: boolean;
  /** Sub-screens offered as a row of links above the page. */
  views?: { label: string; to: string }[];
};

export type NavNode = NavLeaf & {
  icon: IconName;
  /** A section: the row opens and closes these. With `to` set, the label is a link of its own. */
  children?: NavLeaf[];
  /** Breadcrumb name of a section's own page (Scan › Scan Center). */
  self?: string;
  /** Live count on the row (see OrderConnectLive / MailBadge). */
  badge?: "order_connect" | "mail";
};

export const NAV_MAIN: NavNode[] = [
  { label: "Overview", icon: "home", to: "/" },
  {
    label: "Sales",
    icon: "cart",
    to: null,
    badge: "order_connect",
    children: [
      // Online orders are a tab of Sales Orders; their settings sit under Settings.
      { label: "Sales Orders", to: "/orders", owns: ["/online-orders"] },
      { label: "Sales Invoices", to: "/invoices" },
      { label: "Payments", to: "/payments", owns: ["/receivables"] },
      { label: "Customers", to: "/customers" },
    ],
  },
  {
    label: "Purchases",
    icon: "receiving",
    to: null,
    children: [
      { label: "Purchase Orders", to: "/purchases" },
      // No list of supplier invoices exists yet (each purchase carries its invoice number): hidden
      // until that page is built. Give it an address and drop `hidden` to show it.
      { label: "Purchase Invoices", to: null, hidden: true },
      {
        label: "Suppliers",
        to: "/suppliers",
        views: [
          { label: "Suppliers", to: "/suppliers" },
          { label: "Supplier payments", to: "/suppliers/payments" },
        ],
      },
    ],
  },
  {
    label: "Warehouse",
    icon: "inventory",
    to: null,
    children: [
      { label: "Packing", to: "/packing" },
      // The warehouse's part of a purchase: shipments whose pieces still have to be checked.
      { label: "Receiving", to: "/purchases?view=verify" },
      { label: "Inventory", to: "/inventory" },
      {
        label: "Products",
        to: "/products",
        views: [
          { label: "Products", to: "/products" },
          { label: "Price book", to: "/products/prices" },
        ],
      },
      { label: "Product Packages", to: "/products/case-sizes" },
    ],
  },
  {
    label: "Scan",
    icon: "scan",
    to: "/scan",
    self: "Scan Center",
    children: [
      { label: "Scan Box", to: "/gmail" },
      { label: "Scan History", to: "/scan?tab=history" },
    ],
  },
  { label: "Documents", icon: "documents", to: null, children: [{ label: "Files", to: "/documents" }] },
  // Lark Mail: the business mailbox. The scanning mailbox is Scan › Scan Box.
  { label: "Email", icon: "mail", to: "/email", perm: "mail.use", badge: "mail" },
  { label: "Dashboard", icon: "reports", to: "/reports" },
];

/** Pinned to the bottom of the sidebar, above the user. */
export const NAV_FOOT: NavNode[] = [
  {
    label: "Settings",
    icon: "settings",
    to: null,
    children: [
      { label: "General", to: "/settings" },
      { label: "Users & Roles", to: "/settings/users", owns: ["/settings/roles"], gate: "admin" },
      { label: "Integrations", to: "/settings/email-services", perm: "mail.manage" },
      { label: "Online Order Settings", to: "/online-orders/settings" },
      { label: "Activity Log", to: "/activity" },
      { label: "ERP Trash", to: "/trash", gate: "trash" },
    ],
  },
];

export const NAV_ALL: NavNode[] = [...NAV_MAIN, ...NAV_FOOT];

/* ------------------------------------------------------------------ where am I */

export type NavHit = {
  /** The top-level entry: a plain link, or the section the page belongs to. */
  node: NavNode;
  /** The entry inside the section; null when the section's own page is open (Scan Center). */
  leaf: NavLeaf | null;
  /** The address prefix that matched. */
  base: string;
  /** Matched through `owns` (the page lives at another address than the entry's own). */
  viaOwns: boolean;
  /** Matched through the `?key=value` part of the entry's address. */
  byQuery: boolean;
};

const under = (path: string, prefix: string) => (prefix === "/" ? path === "/" : path === prefix || path.startsWith(`${prefix}/`));

function score(target: NavLeaf, pathname: string, params: URLSearchParams): { n: number; base: string; viaOwns: boolean; byQuery: boolean } | null {
  let best: { n: number; base: string; viaOwns: boolean; byQuery: boolean } | null = null;
  if (target.to !== null) {
    const [path = "", query] = target.to.split("?");
    if (query) {
      // An address with a query names one view of a page: the path and every pair must match.
      const want = [...new URLSearchParams(query)];
      if (pathname === path && want.every(([k, v]) => params.get(k) === v)) best = { n: path.length + 0.5, base: path, viaOwns: false, byQuery: true };
    } else if (under(pathname, path)) best = { n: path.length, base: path, viaOwns: false, byQuery: false };
  }
  for (const p of target.owns ?? []) if (under(pathname, p) && p.length > (best?.n ?? -1)) best = { n: p.length, base: p, viaOwns: true, byQuery: false };
  return best;
}

/** The menu entry a page belongs to: the longest matching address wins. */
export function locate(pathname: string, search = ""): NavHit | null {
  const params = new URLSearchParams(search);
  let hit: (NavHit & { n: number }) | null = null;
  for (const node of NAV_ALL) {
    const own = score(node, pathname, params);
    if (own && own.n > (hit?.n ?? -1)) hit = { node, leaf: null, ...own };
    for (const leaf of node.children ?? []) {
      const s = score(leaf, pathname, params);
      if (s && s.n > (hit?.n ?? -1)) hit = { node, leaf, ...s };
    }
  }
  if (!hit) return null;
  return { node: hit.node, leaf: hit.leaf, base: hit.base, viaOwns: hit.viaOwns, byQuery: hit.byQuery };
}

/** Every screen of the menu, for the ⌘K "Screens" list. */
export function navScreens(): { label: string; group: string; to: string; icon: IconName }[] {
  return NAV_ALL.flatMap((node) => {
    const group = node.children ? node.label : "General";
    const own = node.to ? [{ label: node.self ?? node.label, group, to: node.to, icon: node.icon }] : [];
    const kids = (node.children ?? []).flatMap((c) => [
      ...(c.to ? [{ label: `${node.label} › ${c.label}`, group, to: c.to, icon: node.icon }] : []),
      ...(c.views ?? []).filter((v) => v.to !== c.to).map((v) => ({ label: `${node.label} › ${v.label}`, group, to: v.to, icon: node.icon })),
    ]);
    return [...own, ...kids];
  });
}
