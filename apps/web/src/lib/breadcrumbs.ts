// Global breadcrumbs: where this screen sits, worked out from the address alone.
// Static parts are named here; a record id is named by the page's own heading (see Breadcrumbs).

export type Crumb = {
  /** null = a record: the page heading names it (the shell fills it in). */
  label: string | null;
  /** Generic name used until the heading is there, or for a record in the middle of the trail. */
  fallback?: string;
  to: string;
};

/** Address patterns (":id" = a record) and their names. */
const NAMES: Record<string, string> = {
  orders: "Orders",
  "orders/new": "New order",
  invoices: "Invoices",
  "invoices/new": "New invoice",
  payments: "Payments",
  receivables: "Receivables",
  "online-orders": "Online Orders",
  "online-orders/forms": "Form builder",
  "online-orders/requests": "Requests",
  "online-orders/embed": "Embed & share",
  "online-orders/settings": "Settings",
  packing: "Packing",
  "packing/:id/invoice": "Issue invoice",
  inventory: "Inventory",
  "inventory/lots": "Batches",
  receiving: "Receiving",
  scan: "Scan Center",
  "scan/review": "To review",
  gmail: "Scan Box",
  products: "Products",
  "products/case-sizes": "Case sizes",
  "products/prices": "Prices",
  customers: "Customers",
  suppliers: "Suppliers",
  "suppliers/payments": "Supplier payments",
  reports: "Reports",
  documents: "Documents",
  activity: "Activity",
  search: "Search",
  trash: "Trash",
  email: "Email",
  settings: "Settings",
  "settings/notifications": "Notifications",
  "settings/users": "User management",
  "settings/roles": "Roles & permissions",
  "settings/security": "Security & sessions",
  "settings/ocr": "OCR test",
  "settings/email-services": "Email services",
};

/** What a record is called before its heading loads (or in the middle of a trail). */
const RECORD: Record<string, string> = {
  orders: "Order",
  invoices: "Invoice",
  payments: "Payment",
  "online-orders/forms": "Form",
  "online-orders/requests": "Request",
  packing: "Packing sheet",
  "inventory/lots": "Batch",
  receiving: "Shipment",
  scan: "Scan",
  "scan/review": "Reading",
  products: "Product",
  "products/prices": "Price list",
  customers: "Customer",
  suppliers: "Supplier",
};

/** A part of the trail that is not a page of its own links somewhere useful instead. */
const LINK: Record<string, string> = {
  "scan/review": "/scan?tab=review",
  "inventory/lots": "/inventory",
};

/** Screens reached by a tab (`?tab=`) get their own last step. */
const TABS: Record<string, Record<string, string>> = {
  inventory: { movements: "Stock ledger", counts: "Counts" },
  scan: { review: "To review" },
};

export function crumbsFor(pathname: string, search = ""): Crumb[] {
  const parts = pathname.split("/").filter(Boolean);
  const out: Crumb[] = [];
  const pattern: string[] = [];
  parts.forEach((seg, i) => {
    const parent = pattern.join("/");
    const known = NAMES[[...pattern, seg].join("/")];
    const to = `/${parts.slice(0, i + 1).join("/")}`;
    if (known) {
      pattern.push(seg);
      out.push({ label: known, to: LINK[pattern.join("/")] ?? to });
    } else {
      pattern.push(":id");
      out.push({ label: null, fallback: RECORD[parent] ?? "Details", to });
    }
  });
  const tab = new URLSearchParams(search).get("tab");
  const named = tab && TABS[pattern.join("/")]?.[tab];
  if (named) out.push({ label: named, to: `${pathname}?tab=${tab}` });
  // A record in the middle of the trail is never named by the heading (that belongs to the last step).
  return out.map((c, i) => (c.label === null && i < out.length - 1 ? { label: c.fallback ?? "Details", to: c.to } : c));
}

/** The page heading as a crumb: one line, at most 60 characters. */
export function headingLabel(text: string | null | undefined): string | null {
  const t = (text ?? "").replace(/\s+/g, " ").trim();
  return t ? (t.length > 60 ? `${t.slice(0, 60).trimEnd()}…` : t) : null;
}
