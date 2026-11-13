// Global breadcrumbs: where this screen sits, worked out from the address and the menu (nav.ts).
// The menu names the section and the entry; this file names the screens underneath an entry.
// A record id is named by the page's own heading (see components/Breadcrumbs).
import { locate } from "../nav";

export type Crumb = {
  /** null = a record: the page heading names it (the shell fills it in). */
  label: string | null;
  /** Generic name used until the heading is there, or for a record in the middle of the trail. */
  fallback?: string;
  /** null = a menu section without a page of its own: shown as plain text. */
  to: string | null;
};

/** Screens underneath a menu entry, by address pattern (":id" = a record). "" = not a step. */
const NAMES: Record<string, string> = {
  "orders/new": "New",
  "invoices/new": "New",
  "purchases/new": "New",
  "online-orders": "Online",
  "online-orders/forms": "Form builder",
  "online-orders/requests": "Requests",
  "online-orders/embed": "Embed & share",
  "packing/:id/invoice": "Issue invoice",
  "inventory/lots": "Batches",
  "scan/review": "To review",
  "gmail/oauth": "",
  "gmail/oauth/callback": "Connecting",
  "products/prices": "Price book",
  "suppliers/payments": "Supplier payments",
  receivables: "Receivables",
  "settings/notifications": "Notifications",
  "settings/roles": "Roles & permissions",
  "settings/security": "Security & sessions",
  "settings/ocr": "OCR test",
  search: "Search",
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
  purchases: "Purchase",
  scan: "Scan",
  "scan/review": "Reading",
  products: "Product",
  "products/prices": "Price list",
  customers: "Customer",
  suppliers: "Supplier",
};

/** A step that is not a page of its own links somewhere useful instead. */
const LINK: Record<string, string> = {
  "scan/review": "/scan?tab=review",
  "inventory/lots": "/inventory",
};

/** Screens reached by a tab (`?tab=`) get their own last step. */
const TABS: Record<string, Record<string, string>> = {
  inventory: { movements: "Stock ledger", counts: "Counts" },
  scan: { review: "To review" },
};

const parts = (path: string) => path.split("/").filter(Boolean);

export function crumbsFor(pathname: string, search = ""): Crumb[] {
  const segs = parts(pathname);
  const out: Crumb[] = [];
  const hit = locate(pathname, search);
  let start = 0; // first part of the address the menu has not named
  if (hit) {
    const { node, leaf } = hit;
    if (leaf) out.push({ label: node.label, to: node.to }, { label: leaf.label, to: leaf.to });
    else if (node.children) out.push({ label: node.label, to: node.to }, { label: node.self ?? node.label, to: node.to });
    else out.push({ label: node.label, to: node.to });
    // An entry that owns another address keeps that address's own name as the next step.
    start = parts(hit.base).length - (hit.viaOwns ? 1 : 0);
  }
  const pattern = segs.slice(0, start);
  segs.slice(start).forEach((seg, i) => {
    const parent = pattern.join("/");
    const key = [...pattern, seg].join("/");
    const to = `/${segs.slice(0, start + i + 1).join("/")}`;
    if (key in NAMES) {
      pattern.push(seg);
      if (NAMES[key]) out.push({ label: NAMES[key] as string, to: LINK[key] ?? to });
    } else {
      pattern.push(":id");
      out.push({ label: null, fallback: RECORD[parent] ?? "Details", to });
    }
  });
  const tab = new URLSearchParams(search).get("tab");
  const named = !hit?.byQuery && tab ? TABS[pattern.join("/")]?.[tab] : undefined;
  if (named) out.push({ label: named, to: `${pathname}?tab=${tab}` });
  // A record in the middle of the trail is never named by the heading (that belongs to the last step).
  return out.map((c, i) => (c.label === null && i < out.length - 1 ? { label: c.fallback ?? "Details", to: c.to } : c));
}

/** The page heading as a crumb: one line, at most 60 characters. */
export function headingLabel(text: string | null | undefined): string | null {
  const t = (text ?? "").replace(/\s+/g, " ").trim();
  return t ? (t.length > 60 ? `${t.slice(0, 60).trimEnd()}…` : t) : null;
}
