import { Link, useLocation } from "react-router-dom";
import { has, hasCompanyWide } from "../api";
import { useAuth } from "../auth";

type Tab = "sales" | "online" | "purchases";
const tabCls = (on: boolean) => `-mb-px border-b-2 px-3 py-2 text-sm font-medium ${on ? "border-brand-primary text-ink" : "border-transparent text-ink-muted hover:text-ink"}`;

/** The tabs of the Orders page (Sales orders · Online · Purchases), each with its own address.
 * Shown on the online-order screens too, so "Online" reads as one tab of Sales Orders. */
export function OrdersTabs({ current }: { current: Tab }) {
  const { session } = useAuth();
  if (!session) return null;
  const tabs = [
    { id: "sales" as const, label: "Sales orders", to: "/orders", show: has(session, "sales_order.read") || has(session, "sales_order.manage") },
    { id: "online" as const, label: "Online", to: "/online-orders", show: hasCompanyWide(session, "order_connect.read") },
    { id: "purchases" as const, label: "Purchases", to: "/purchases", show: has(session, "grn.record") || has(session, "grn.approve") },
  ].filter((t) => t.show || t.id === current);
  return (
    <div role="tablist" aria-label="Orders" className="flex gap-1 border-b border-line">
      {tabs.map((t) => (
        <Link key={t.id} role="tab" aria-selected={current === t.id} to={t.to} className={tabCls(current === t.id)}>
          {t.label}
        </Link>
      ))}
    </div>
  );
}

const ONLINE = [
  { label: "Overview", to: "/online-orders" },
  { label: "Requests", to: "/online-orders/requests" },
  { label: "Form builder", to: "/online-orders/forms" },
  { label: "Embed & share", to: "/online-orders/embed" },
];

/** Online orders inside Sales Orders: the Orders tabs with Online selected, then its own screens. */
export function OnlineOrdersNav() {
  const { pathname } = useLocation();
  return (
    <>
      <OrdersTabs current="online" />
      <nav aria-label="Online orders pages" className="flex flex-wrap gap-1.5">
        {ONLINE.map((v) => {
          const on = v.to === pathname;
          return (
            <Link key={v.to} to={v.to} aria-current={on ? "page" : undefined} className={`chip px-4 py-2 text-sm ${on ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:bg-subtle"}`}>
              {v.label}
            </Link>
          );
        })}
      </nav>
    </>
  );
}
