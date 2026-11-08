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
      { label: "Payments", icon: "payments", to: "/payments", module: null },
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
      {
        label: "Products",
        icon: "products",
        to: "/products",
        module: null,
        // child labels must not contain "Products": e2e clicks /Products/ without .first()
        children: [
          { label: "Product list", to: "/products" },
          { label: "Prices", to: "/products/prices" },
        ],
      },
      { label: "Customers", icon: "users", to: "/customers", module: null },
      { label: "Suppliers", icon: "store", to: "/suppliers", module: null },
    ],
  },
  {
    title: "Analytics",
    items: [
      { label: "Reports", icon: "reports", to: "/reports", module: null },
      { label: "Documents", icon: "documents", to: "/documents", module: null },
    ],
  },
];

export const SETTINGS: NavItem = { label: "Settings", icon: "settings", to: "/settings", module: null };
