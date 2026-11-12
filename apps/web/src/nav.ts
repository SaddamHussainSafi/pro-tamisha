import type { IconName } from "./ui/Icon";

// Sidebar groups (design: grouped "Open (Expanded)" sidebar). `module` = build module that enables it.
export type NavItem = {
  label: string;
  icon: IconName;
  to: string;
  module: string | null;
  /** Live count shown on the entry (see components/OrderConnectLive.tsx). */
  badge?: "order_connect" | "mail";
  /** Shown only to people holding this permission (e.g. Email needs mail.use). */
  perm?: string;
  children?: { label: string; to: string }[];
};
export type NavGroup = { title: string | null; items: NavItem[] };

export const NAV_GROUPS: NavGroup[] = [
  {
    title: null,
    items: [
      { label: "Overview", icon: "home", to: "/", module: null },
      // Lark Mail; separate from the Scan Box (the Gmail mailbox for scanned documents) under Warehouse.
      { label: "Email", icon: "mail", to: "/email", module: null, perm: "mail.use", badge: "mail" },
    ],
  },
  {
    title: "Sales",
    items: [
      { label: "Orders", icon: "cart", to: "/orders", module: null },
      { label: "Invoices", icon: "invoices", to: "/invoices", module: null },
      { label: "Payments", icon: "payments", to: "/payments", module: null },
      {
        label: "Online Orders",
        icon: "globe",
        to: "/online-orders",
        module: null,
        badge: "order_connect",
        children: [
          { label: "Overview", to: "/online-orders" },
          { label: "Form builder", to: "/online-orders/forms" },
          { label: "Requests", to: "/online-orders/requests" },
          { label: "Embed & share", to: "/online-orders/embed" },
          { label: "Settings", to: "/online-orders/settings" },
        ],
      },
    ],
  },
  {
    title: "Warehouse",
    items: [
      { label: "Packing", icon: "boxes", to: "/packing", module: null },
      {
        label: "Inventory",
        icon: "inventory",
        to: "/inventory",
        module: null,
        children: [
          { label: "Stock on hand", to: "/inventory" },
          { label: "Stock ledger", to: "/inventory?tab=movements" },
          { label: "Counts", to: "/inventory?tab=counts" },
        ],
      },
      { label: "Receiving", icon: "receiving", to: "/receiving", module: null },
      { label: "Scan Center", icon: "scan", to: "/scan", module: null },
      { label: "Scan Box", icon: "scanbox", to: "/gmail", module: null },
    ],
  },
  {
    title: "Master data",
    items: [
      {
        label: "Products",
        icon: "products",
        to: "/products",
        module: null,
        // child labels must not contain "Products": e2e clicks /Products/ without .first()
        children: [
          { label: "Product list", to: "/products" },
          { label: "Case sizes", to: "/products/case-sizes" },
          { label: "Prices", to: "/products/prices" },
        ],
      },
      { label: "Customers", icon: "users", to: "/customers", module: null },
      {
        label: "Suppliers",
        icon: "store",
        to: "/suppliers",
        module: null,
        children: [
          { label: "Supplier list", to: "/suppliers" },
          { label: "Supplier payments", to: "/suppliers/payments" },
        ],
      },
    ],
  },
  {
    title: "Analytics",
    items: [
      { label: "Reports", icon: "reports", to: "/reports", module: null },
      { label: "Documents", icon: "documents", to: "/documents", module: null },
      { label: "Activity", icon: "clock", to: "/activity", module: null },
    ],
  },
];

export const SETTINGS: NavItem = { label: "Settings", icon: "settings", to: "/settings", module: null };
/** Super Admin only (shown above Settings): deleted records, kept 30 days. */
export const TRASH: NavItem = { label: "Trash", icon: "trash", to: "/trash", module: null };
