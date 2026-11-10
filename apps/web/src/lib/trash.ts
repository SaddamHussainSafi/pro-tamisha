// Trash bin (API: routes/trash.py). The Super Admin moves records to the Trash for 30 days;
// other staff who may edit a record can only ask for it to be deleted.
import { useEffect, useRef } from "react";
import { ApiError, apiRequest, canInWarehouse, hasCompanyWide, has, type Session } from "../api";

export type TrashKind =
  | "product"
  | "case_size"
  | "customer"
  | "customer_group"
  | "supplier"
  | "price_draft"
  | "sales_order"
  | "picking_sheet"
  | "scan"
  | "document"
  | "user"
  | "notification"
  | "website_entry";

export type TrashItem = {
  id: string;
  kind: TrashKind;
  item_id: string;
  label: string;
  trashed_by: string | null;
  trashed_at: string;
  purge_after: string;
  days_left: number;
  already_in_trash: boolean;
};
export type DeleteRequestStatus = "PENDING" | "APPROVED" | "DISMISSED";
export type DeleteRequest = {
  id: string;
  kind: TrashKind;
  item_id: string;
  label: string;
  reason: string;
  status: DeleteRequestStatus;
  requested_by: string | null;
  created_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_note: string;
};
export type Reference = { table: string; noun: string; count: number; examples: string[] };

export const TRASH_DAYS = 30;
export const TRASH_PERMISSION = "trash.manage";

export const KIND_LABEL: Record<TrashKind, string> = {
  product: "Product",
  case_size: "Case size",
  customer: "Customer",
  customer_group: "Customer group",
  supplier: "Supplier",
  price_draft: "Price book draft",
  sales_order: "Sales order",
  picking_sheet: "Picking sheet",
  scan: "Scan",
  document: "Document",
  user: "User",
  notification: "Notification",
  website_entry: "Online order request",
};

/** Where each kind's DELETE lives (next to the record's own API routes). */
const DELETE_PATH: Record<TrashKind, string> = {
  product: "/products",
  case_size: "/unit-profiles",
  customer: "/customers",
  customer_group: "/customer-groups",
  supplier: "/vendors",
  price_draft: "/price-books/versions",
  sales_order: "/sales-orders",
  picking_sheet: "/pick-attempts",
  scan: "/scans/jobs",
  document: "/documents",
  user: "/employees",
  notification: "/notifications",
  website_entry: "/order-connect/submissions",
};

/** Permission that lets someone ask for a delete (null: only the Super Admin deletes these). */
const REQUEST_PERMISSION: Record<TrashKind, string | null> = {
  product: "catalog.manage",
  case_size: "catalog.manage",
  customer: "catalog.manage",
  customer_group: "catalog.manage",
  supplier: "catalog.manage",
  price_draft: "price.draft",
  sales_order: "sales_order.manage",
  picking_sheet: "sales_order.manage",
  scan: "scan.create",
  document: "document.manage",
  user: null,
  notification: null,
  website_entry: "order_connect.review",
};
const WAREHOUSE_KINDS: ReadonlySet<TrashKind> = new Set(["sales_order", "picking_sheet", "scan", "document"]);

/** Where a record lives in the app (after a restore, "Open" goes there). */
export function recordLink(kind: TrashKind, id: string): string {
  switch (kind) {
    case "product":
      return `/products/${id}`;
    case "case_size":
      return "/products/case-sizes";
    case "customer":
      return `/customers/${id}`;
    case "customer_group":
      return "/customers";
    case "supplier":
      return `/suppliers/${id}`;
    case "price_draft":
      return `/products/prices/${id}`;
    case "sales_order":
      return `/orders/${id}`;
    case "picking_sheet":
      return `/picking/${id}`;
    case "scan":
      return `/scan/${id}`;
    case "document":
      return "/documents";
    case "user":
      return "/settings/users";
    case "notification":
      return "/notifications";
    case "website_entry":
      return `/online-orders/requests/${id}`;
  }
}

export const canManageTrash = (s: Session) => hasCompanyWide(s, TRASH_PERMISSION);
/** Deleting needs a two-step sign-in that is turned on and passed for this session. */
export const twoStepReady = (s: Session) => s.mfa_enrolled && !s.mfa_required;

/** What the Delete control does for this person: move to Trash, ask, or nothing. */
export function deleteMode(s: Session, kind: TrashKind, warehouseId?: string | null): "trash" | "request" | null {
  if (canManageTrash(s)) return "trash";
  const perm = REQUEST_PERMISSION[kind];
  if (!perm) return null;
  if (WAREHOUSE_KINDS.has(kind)) return (warehouseId ? canInWarehouse(s, perm, warehouseId) : has(s, perm)) ? "request" : null;
  return hasCompanyWide(s, perm) ? "request" : null;
}

export function daysLeftText(days: number): string {
  if (days <= 0) return "Today";
  return days === 1 ? "1 day" : `${days} days`;
}

/** "3 sales orders (SO-1, SO-2)" lines from a 409 IN_USE answer. */
export function referenceLines(e: unknown): string[] {
  if (!(e instanceof ApiError)) return [];
  const refs = (e.extra.references ?? []) as Reference[];
  return refs.map((r) => {
    const noun = r.count === 1 ? r.noun : plural(r.noun);
    return `${r.count} ${noun}${r.examples.length ? ` (${r.examples.join(", ")})` : ""}`;
  });
}

function plural(word: string): string {
  if (/[^aeiou]y$/.test(word)) return word.slice(0, -1) + "ies";
  return word + "s";
}

/** The sentence to show when a delete or restore is refused. */
export function trashErrorText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.code === "IN_USE") return e.message.split(": it is used by")[0] + " while other records use it:";
    if (e.code === "TWO_STEP_REQUIRED") return "Turn on two-step sign-in in My account first, then try again.";
    if (e.code === "FORBIDDEN") return "You don't have permission to do that.";
    return e.message;
  }
  return "Something went wrong. Try again.";
}

export const trashApi = {
  move: (kind: TrashKind, id: string) => apiRequest<TrashItem>("DELETE", `${DELETE_PATH[kind]}/${id}`, undefined, true),
  list: () => apiRequest<TrashItem[]>("GET", "/trash"),
  restore: (id: string) => apiRequest<{ kind: TrashKind; item_id: string; label: string }>("POST", `/trash/${id}/restore`, undefined, true),
  purge: (id: string) => apiRequest<{ purged: number }>("DELETE", `/trash/${id}`, undefined, true),
  empty: () => apiRequest<{ purged: number }>("POST", "/trash/empty", undefined, true),
  requests: (status: DeleteRequestStatus = "PENDING") => apiRequest<DeleteRequest[]>("GET", `/delete-requests?status=${status}`),
  request: (kind: TrashKind, itemId: string, reason: string) =>
    apiRequest<DeleteRequest>("POST", "/delete-requests", { kind, item_id: itemId, reason }, true),
  approve: (id: string) => apiRequest<TrashItem>("POST", `/delete-requests/${id}/approve`, undefined, true),
  dismiss: (id: string, note = "") => apiRequest<DeleteRequest>("POST", `/delete-requests/${id}/dismiss`, { note }, true),
};

/* Lists reload when something moves to or comes back from the Trash (Undo, Trash page). */
const listeners = new Set<() => void>();
export const notifyTrashChanged = () => listeners.forEach((l) => l());
export function useTrashChanges(fn: () => unknown) {
  const ref = useRef(fn);
  useEffect(() => {
    ref.current = fn;
  });
  useEffect(() => {
    const l = () => void ref.current();
    listeners.add(l);
    return () => void listeners.delete(l);
  }, []);
}
