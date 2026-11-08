import type { IconName } from "./ui/Icon";

// Sidebar groups (design: grouped "Open (Expanded)" sidebar). `module` = build module that enables it.
export type NavItem = {
  label: string;
  icon: IconName;
  to: string;
  module: string | null;
  children?: { label: string; to: string }[];
};
export type NavGroup = { title: string | null; items: NavItem[] };

export const NAV_GROUPS: NavGroup[] = [
  { title: null, items: [{ label: "Overview", icon: "home", to: "/", module: null }] },
  {
    title: "Sales",
    items: [
      { label: "Orders", icon: "cart", to: "/orders", module: null },
      { label: "Invoices", icon: "invoices", to: "/invoices", module: null },
      { label: "Payments", icon: "payments", to: "/payments", module: "Payments" },
    ],
  },
  {
    title: "Warehouse",
    items: [
      { label: "Picking", icon: "boxes", to: "/picking", module: null },
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
    ],
  },
  {
    title: "Master data",
    items: [
      { label: "Products", icon: "products", to: "/products", module: null },
      { label: "Customers", icon: "users", to: "/customers", module: "Customers" },
      { label: "Vendors", icon: "store", to: "/vendors", module: null },
      { label: "Pricing", icon: "pricing", to: "/pricing", module: null },
    ],
  },
  {
    title: "Analytics",
    items: [
      { label: "Reports", icon: "reports", to: "/reports", module: "Reports" },
      { label: "Documents", icon: "documents", to: "/documents", module: null },
    ],
  },
];

export const SETTINGS: NavItem = { label: "Settings", icon: "settings", to: "/settings/warehouses", module: null };

// Task 00b mock-only screens (sample data), shown for layout sign-off.
export const PREVIEW: NavItem[] = [
  { label: "Overview", icon: "overview", to: "/preview/overview", module: null },
  { label: "Orders", icon: "orders", to: "/preview/orders", module: null },
  { label: "Invoice editor", icon: "invoices", to: "/preview/invoice", module: null },
  { label: "Receiving", icon: "receiving", to: "/preview/receiving", module: null },
];
