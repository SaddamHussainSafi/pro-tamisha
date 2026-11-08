import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, has, type AgingCustomer, type OrderSummary, type ProductSalesRow, type SalesReport } from "../api";
import { useAuth } from "../auth";
import { cad, dec } from "../lib/decimal";
import { ORDER_STATUS } from "../lib/orders";
import { amount, chartBars, CHART_TITLE, isoDay, presetRange } from "../lib/reports";
import { BarChart, CardTitle, EmptyState, Progress, StatusChip } from "../ui/primitives";

const link = "font-medium text-brand-primary hover:underline";

/** A card whose body scrolls on its own (keyboard focusable, named for screen readers). */
function ScrollPanel({ id, title, to, children, className = "" }: { id: string; title: string; to?: string; children: ReactNode; className?: string }) {
  return (
    <section className={`card flex flex-col p-6 ${className}`} aria-labelledby={id}>
      <CardTitle id={id} title={title} to={to} />
      <div tabIndex={0} role="region" aria-label={`${title}, scrollable`} className="mt-4 max-h-80 min-h-0 flex-1 overflow-y-auto pr-1 focus-visible:outline-2 focus-visible:outline-brand-primary">
        {children}
      </div>
    </section>
  );
}

/**
 * Overview › work panels: sales trend and best sellers (Reports API), orders waiting on the
 * warehouse, and customers who are late (receivables). Each panel shows only with the
 * permission behind it.
 */
export function OverviewPanels({ orders }: { orders: OrderSummary[] }) {
  const { session } = useAuth();
  const canReport = !!session && has(session, "report.read");
  const canAging = !!session && has(session, "invoice.read") && (has(session, "payment.record") || has(session, "payment.verify"));
  const [sales, setSales] = useState<SalesReport | null>(null);
  const [top, setTop] = useState<ProductSalesRow[] | null>(null);
  const [late, setLate] = useState<AgingCustomer[] | null>(null);

  useEffect(() => {
    if (!canReport) return;
    const r = presetRange("30d", isoDay(new Date()));
    const q = { date_from: r.from, date_to: r.to };
    api.reportSales({ ...q, bucket: "day" }).then(setSales, () => setSales(null));
    api.reportByProduct({ ...q, limit: 8 }).then((p) => setTop(p.rows), () => setTop(null));
  }, [canReport]);
  useEffect(() => {
    if (canAging) api.receivables().then((r) => setLate(r.customers.filter((c) => dec(c.total) > dec(c.current)).sort((a, b) => (dec(b.total) - dec(b.current) > dec(a.total) - dec(a.current) ? 1 : -1))), () => setLate(null));
  }, [canAging]);

  const waiting = orders.filter((o) => ["CONFIRMED", "PICKING", "PARTIALLY_FULFILLED"].includes(o.status)).sort((a, b) => a.created_at.localeCompare(b.created_at));
  const bars = sales ? chartBars(sales.series, sales.bucket) : null;
  const best = top?.[0] ? dec(top[0].net_sales) : 0n;

  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
      {sales && bars && (
        <section className="card p-6 sm:p-8 xl:col-span-8" aria-labelledby="ov-trend">
          <CardTitle id="ov-trend" title={`${CHART_TITLE[bars.unit]} · last 30 days`} to="/reports" />
          <p className="mt-1 text-sm text-ink-muted">
            {cad(dec(sales.net_sales))} before tax · {sales.invoice_count} invoices · {sales.customer_count} customers
          </p>
          {bars.bars.some((b) => b.value > 0) ? (
            <BarChart bars={bars.bars} highlight={bars.bars.reduce((m, b, i, a) => (b.value > (a[m]?.value ?? 0) ? i : m), 0)} ariaLabel="Net sales, last 30 days" axis={(v) => (v >= 1000 ? `${Math.round(v / 1000)}k` : String(Math.round(v)))} />
          ) : (
            <EmptyState title="No invoices in the last 30 days" />
          )}
        </section>
      )}
      {top && (
        <ScrollPanel id="ov-top" title="Best sellers · 30 days" to="/reports" className={sales ? "xl:col-span-4" : "xl:col-span-6"}>
          {top.length ? (
            <ul className="space-y-5">
              {top.map((r) => (
                <li key={`${r.product_id}-${r.unit_code}`}>
                  <Progress label={`${r.name} · ${amount(r.qty, r.unit_code)}`} value={Number(dec(r.net_sales) / 1_000_000n)} max={Math.max(1, Number(best / 1_000_000n))} display={cad(dec(r.net_sales))} />
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="Nothing sold yet" />
          )}
        </ScrollPanel>
      )}
      <ScrollPanel id="ov-waiting" title="Orders waiting on the warehouse" to="/picking" className="xl:col-span-6">
        {waiting.length ? (
          <ul className="divide-y divide-line">
            {waiting.map((o) => (
              <li key={o.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
                <span className="min-w-0">
                  <Link className={link} to={`/orders/${o.id}`}>
                    {o.order_no}
                  </Link>
                  <span className="block truncate text-sm text-ink-muted">{o.customer_name}</span>
                </span>
                <StatusChip tone={ORDER_STATUS[o.status].tone}>{ORDER_STATUS[o.status].label}</StatusChip>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState title="Nothing waiting" hint="Confirmed orders show here until they are picked." />
        )}
      </ScrollPanel>
      {late && (
        <ScrollPanel id="ov-late" title="Customers paying late" to="/receivables" className="xl:col-span-6">
          {late.length ? (
            <ul className="divide-y divide-line">
              {late.map((c) => (
                <li key={c.customer_id} className="flex flex-wrap items-center justify-between gap-2 py-3">
                  <span className="min-w-0">
                    <Link className={link} to={`/customers/${c.customer_id}`}>
                      {c.customer_name}
                    </Link>
                    <span className="block text-sm text-ink-muted">{c.open_invoices} open invoice{c.open_invoices === 1 ? "" : "s"}</span>
                  </span>
                  <span className="num whitespace-nowrap font-semibold text-danger">{cad(dec(c.total) - dec(c.current))} late</span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState title="Nobody is late" hint="Every open invoice is still within its terms." />
          )}
        </ScrollPanel>
      )}
    </div>
  );
}
