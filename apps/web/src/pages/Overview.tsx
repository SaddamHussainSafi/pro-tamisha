import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type InvoiceSummary, type OrderSummary, type Page, type Shipment } from "../api";
import { useAuth } from "../auth";
import { cad, dec, type Dec } from "../lib/decimal";
import { greeting, longDate, shortNum } from "../lib/format";
import { EMAIL_STATUS } from "../lib/invoices";
import { ORDER_STATUS } from "../lib/orders";
import { Icon } from "../ui/Icon";
import { OverviewPanels } from "./OverviewPanels";
import { Avatar, BarChart, CardTitle, DonutChart, EmptyState, KpiCard, Progress, StatusChip, Trend } from "../ui/primitives";

// Every figure here comes from the API. Payments are not built yet, so nothing claims "paid" or "overdue".
type Data = { inv: Page<InvoiceSummary> | null; ord: Page<OrderSummary> | null; shp: Page<Shipment> | null };
const LIMIT = 200;
const MONTHS = 6;
const KPI_COLS: Record<number, string> = { 1: "sm:grid-cols-1", 2: "xl:grid-cols-2", 3: "xl:grid-cols-3", 4: "xl:grid-cols-4" };

const ym = (d: Date) => `${d.getFullYear()}-${d.getMonth()}`;
const sum = (xs: InvoiceSummary[]): Dec => xs.reduce((s, i) => s + dec(i.total), 0n);
const shortDate = (s: string) => new Date(s).toLocaleDateString("en-CA", { day: "numeric", month: "short" });
const money = (v: Dec) => cad(v).replace("CAD ", "");

export function OverviewPage() {
  const { session } = useAuth();
  const [data, setData] = useState<Data | null>(null);

  useEffect(() => {
    let live = true;
    void Promise.allSettled([api.invoices({ limit: LIMIT }), api.orders({ limit: LIMIT }), api.shipments({ limit: LIMIT })]).then(([inv, ord, shp]) => {
      if (!live) return;
      setData({
        inv: inv.status === "fulfilled" ? inv.value : null,
        ord: ord.status === "fulfilled" ? ord.value : null,
        shp: shp.status === "fulfilled" ? shp.value : null,
      });
    });
    return () => {
      live = false;
    };
  }, []);

  const first = session?.employee.display_name.split(/\s+/)[0] ?? "there";
  const now = new Date();

  const invoices = [...(data?.inv?.items ?? [])].sort((a, b) => b.issued_at.localeCompare(a.issued_at));
  const months = Array.from({ length: MONTHS }, (_, i) => new Date(now.getFullYear(), now.getMonth() - (MONTHS - 1 - i), 1));
  const byMonth = months.map((m) => ({
    label: m.toLocaleDateString("en-CA", { month: "short" }),
    total: sum(invoices.filter((x) => ym(new Date(x.issued_at)) === ym(m))),
  }));
  const thisMonth = byMonth[MONTHS - 1]?.total ?? 0n;
  const lastMonth = byMonth[MONTHS - 2]?.total ?? 0n;
  const change = lastMonth > 0n ? Math.round(Number(((thisMonth - lastMonth) * 1000n) / lastMonth) / 10) : null;

  const orders = data?.ord?.items ?? [];
  const count = (...st: string[]) => orders.filter((o) => st.includes(o.status)).length;
  const openOrders = orders.filter((o) => ["CONFIRMED", "PICKING", "PARTIALLY_FULFILLED"].includes(o.status));
  const drafts = count("DRAFT");
  const shipments = data?.shp?.items ?? [];
  const toReceive = shipments.filter((s) => s.status === "OPEN" || s.status === "COUNTED").length;
  const posted = shipments.filter((s) => s.status === "POSTED").length;
  const fulfilled = count("FULFILLED", "CLOSED_WITH_SHORTAGE");
  const live = orders.filter((o) => o.status !== "CANCELLED").length;
  const kpis = (data?.inv ? 2 : 0) + (data?.ord ? 1 : 0) + (data?.shp ? 1 : 0);

  return (
    <div className="space-y-7 pb-4">
      <section className="flex flex-wrap items-end justify-between gap-5">
        <div>
          <p className="text-base text-ink-muted">{longDate(now)}</p>
          <h1 className="mt-1 text-[34px] font-semibold leading-tight tracking-tight sm:text-[44px]">
            <span className="sr-only">Overview — </span>
            {greeting(now)}, {first}!
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Link to="/scan" className="btn-ghost border-transparent py-3"><Icon name="scan" />Scan</Link>
          <Link to="/receiving" className="btn-ghost border-transparent py-3"><Icon name="receiving" />Receive</Link>
          <Link to="/orders/new" className="btn-primary px-6 py-3"><Icon name="plus" />New order</Link>
        </div>
      </section>

      {!data ? (
        <OverviewSkeleton />
      ) : (
        <>
          <section aria-label="Key figures" className={`grid grid-cols-1 gap-5 sm:grid-cols-2 ${KPI_COLS[kpis] ?? "xl:grid-cols-4"}`}>
            {data.inv && (
              <KpiCard accent icon="invoices" label="Invoiced this month" value={money(thisMonth)} unit="CAD" note={change !== null ? `${change >= 0 ? "+" : ""}${change}% vs last month` : undefined} />
            )}
            {data.ord && <KpiCard accent={!data.inv} icon="orders" label="Open orders" value={openOrders.length} note={drafts ? `${drafts} draft${drafts === 1 ? "" : "s"}` : undefined} />}
            {data.shp && <KpiCard icon="receiving" label="Shipments to receive" value={toReceive} />}
            {data.inv && <KpiCard icon="file" label="Invoices issued" value={data.inv.total} />}
          </section>

          <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
            {data.inv && (
              <section className="card p-6 xl:col-span-8" aria-labelledby="sales-h">
                <CardTitle id="sales-h" title="Invoiced" to="/invoices" />
                <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2">
                  <span className="num text-[44px] font-semibold leading-none tracking-tight">{money(thisMonth)}</span>
                  <span className="text-lg font-medium text-ink-muted">CAD</span>
                  {change !== null && <Trend dir={change >= 0 ? "up" : "down"}>{Math.abs(change)}%</Trend>}
                </div>
                <BarChart
                  bars={byMonth.map((m) => ({ label: m.label, value: Number(m.total / 1_000_000n), display: cad(m.total) }))}
                  highlight={MONTHS - 1}
                  axis={(v) => `$${shortNum(v)}`}
                  ariaLabel={`Invoiced per month, ${byMonth[MONTHS - 1]?.label}: ${cad(thisMonth)}`}
                />
              </section>
            )}

            {data.ord && (
              <section className={`card p-6 sm:p-8 ${data.inv ? "xl:col-span-4" : "xl:col-span-6"}`} aria-labelledby="stage-h">
                <CardTitle id="stage-h" title="Orders by stage" to="/orders" />
                <div className="mt-6">
                  <DonutChart
                    centerLabel="orders"
                    ariaLabel="Orders by stage"
                    slices={[
                      { label: "Packing", value: count("PICKING", "PARTIALLY_FULFILLED") },
                      { label: "Confirmed", value: count("CONFIRMED") },
                      { label: "Fulfilled", value: fulfilled },
                      { label: "Draft or closed", value: count("DRAFT", "CANCELLED") },
                    ]}
                  />
                </div>
              </section>
            )}

            {data.inv && (
              <section className="card p-6 xl:col-span-8" aria-labelledby="inv-h">
                <CardTitle id="inv-h" title="Recent invoices" action={<Link to="/invoices" className="btn-ghost">View all</Link>} />
                {invoices.length === 0 ? (
                  <EmptyState title="No invoices yet" />
                ) : (
                  <div className="relative mt-6 overflow-x-auto rounded-ctl border border-line">
                    <table className="w-full min-w-[640px] border-separate border-spacing-0">
                      <caption className="sr-only">Most recent invoices</caption>
                      <thead>
                        <tr>
                          <th scope="col" className="th">Invoice</th>
                          <th scope="col" className="th">Customer</th>
                          <th scope="col" className="th">Issued</th>
                          <th scope="col" className="th text-right">Total</th>
                          <th scope="col" className="th">Email</th>
                        </tr>
                      </thead>
                      <tbody>
                        {invoices.slice(0, 5).map((i) => (
                          <tr key={i.id} className="hover:bg-subtle">
                            <td className="td num font-semibold"><Link to={`/invoices/${i.id}`} className="text-brand-primary hover:underline">{i.invoice_no}</Link></td>
                            <td className="td"><span className="flex items-center gap-3"><Avatar name={i.customer_name} tone="ink" /><span className="truncate font-medium">{i.customer_name}</span></span></td>
                            <td className="td num text-ink-muted">{shortDate(i.issued_at)}</td>
                            <td className="td num text-right text-base font-semibold">{money(dec(i.total))}</td>
                            <td className="td"><StatusChip tone={EMAIL_STATUS[i.email_status].tone}>{EMAIL_STATUS[i.email_status].label}</StatusChip></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            )}

            {(data.ord || data.shp) && (
              <section className={`card p-6 sm:p-8 ${data.inv ? "xl:col-span-4" : "xl:col-span-6"}`} aria-labelledby="flow-h">
                <CardTitle id="flow-h" title="Fulfilment" />
                <div className="mt-6 space-y-6">
                  {data.ord && <Progress label="Orders fulfilled" value={fulfilled} max={live} display={`${fulfilled} / ${live}`} />}
                  {data.shp && <Progress label="Shipments posted" value={posted} max={shipments.length} display={`${posted} / ${shipments.length}`} />}
                </div>
                {data.ord && openOrders.length > 0 && (
                  <ul className="mt-7 space-y-1 border-t border-line pt-5">
                    {openOrders.slice(0, 3).map((o) => (
                      <li key={o.id}>
                        <Link to={`/orders/${o.id}`} className="-mx-3 flex items-center gap-3.5 rounded-ctl px-3 py-2.5 hover:bg-subtle">
                          <Avatar name={o.customer_name} />
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-base font-semibold">{o.customer_name}</span>
                            <span className="num block text-sm text-ink-muted">{o.order_no}</span>
                          </span>
                          <StatusChip tone={ORDER_STATUS[o.status].tone}>{ORDER_STATUS[o.status].label}</StatusChip>
                        </Link>
                      </li>
                    ))}
                  </ul>
                )}
              </section>
            )}

            {!data.inv && !data.ord && !data.shp && (
              <section className="card p-6 xl:col-span-12">
                <EmptyState title="Nothing to show for your role" hint="Use the menu to open the areas you work in." />
              </section>
            )}
          </div>
          <OverviewPanels orders={orders} />
        </>
      )}
    </div>
  );
}

/** Same shape as the loaded overview (four figures, then the two charts), so it doesn't jump in. */
function OverviewSkeleton() {
  return (
    <div role="status" aria-busy="true" aria-label="Loading" className="space-y-7">
      <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-4" aria-hidden>
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="card flex min-h-[200px] flex-col justify-between p-6">
            <div className="skeleton size-14 rounded-ctl" />
            <div className="space-y-3">
              <div className="skeleton h-4 w-1/2" />
              <div className="skeleton h-8 w-2/3" />
            </div>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-12" aria-hidden>
        <div className="card h-[30rem] p-6 xl:col-span-8">
          <div className="skeleton h-6 w-40" />
        </div>
        <div className="card h-[30rem] p-6 xl:col-span-4">
          <div className="skeleton h-6 w-40" />
        </div>
      </div>
      <span className="sr-only">Loading…</span>
    </div>
  );
}
