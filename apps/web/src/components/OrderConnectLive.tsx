import { useEffect, useState } from "react";
import { has } from "../api";
import { useAuth } from "../auth";
import { oc } from "../lib/orderConnect";
import { useOcLive } from "../lib/useOcLive";

/** Unread website orders for the signed-in person, shown on the sidebar entry. */
export function OrderConnectBadge({ collapsed }: { collapsed: boolean }) {
  const { session } = useAuth();
  const allowed = !!session && has(session, "order_connect.read");
  const [n, setN] = useState(0);
  const load = () => {
    if (allowed) oc.badge().then((r) => setN(r.unread), () => undefined);
  };
  useEffect(load, [allowed]);
  useOcLive(load);
  if (!allowed || n === 0) return null;
  const text = n > 99 ? "99+" : String(n);
  const cls = collapsed
    ? "num absolute right-1 top-1 grid min-w-5 place-items-center rounded-full bg-brand-primary px-1 text-[11px] font-semibold leading-5 text-white ring-2 ring-card"
    : "num absolute left-7 top-0.5 grid min-w-5 place-items-center rounded-full bg-brand-primary px-1 text-[11px] font-semibold leading-5 text-white ring-2 ring-card";
  return (
    <>
      <span className={cls} aria-hidden data-testid="oc-badge">{text}</span>
      <span className="sr-only">, {n} unread</span>
    </>
  );
}
