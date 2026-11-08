import { Link } from "react-router-dom";
import { useAuth } from "../../auth";
import { cad, dec, qty } from "../../lib/decimal";
import { greeting, shortNum } from "../../lib/format";
import { ORDER_STATUS, SAMPLE_ACTIVITY, SAMPLE_ORDERS, SAMPLE_WEEK } from "../../mock/sample";
import { Icon, type IconName } from "../../ui/Icon";
import { Avatar, BarChart, CardTitle, DonutChart, KpiCard, Progress, SampleBanner, StatusChip, Trend, type Tone } from "../../ui/primitives";

const TONE_ICON: Record<Tone, IconName> = { info: "scan", warning: "receiving", danger: "alert", success: "check", neutral: "file" };
const TONE_TILE: Record<Tone, string> = {
  info: "bg-info/10 text-info",
  warning: "bg-warning/12 text-warning",
  danger: "bg-danger/10 text-danger",
  success: "bg-success/12 text-success",
  neutral: "bg-subtle text-ink-muted",
};

export function OverviewPreview() {
  const { session } = useAuth();
  const first = session?.employee.display_name.split(/\s+/)[0] ?? "there";
  const week = SAMPLE_WEEK.map((w) => ({ ...w, n: dec(w.v) }));
  const total = week.reduce((s, w) => s + w.n, 0n);
  const peak = week.reduce((best, w, i) => (w.n > (week[best]?.n ?? 0n) ? i : best), 0);
  const queue = SAMPLE_ORDERS.filter((o) => o.status === "NEW" || o.status === "PARTIAL").slice(0, 5);
  const n = (...st: string[]) => SAMPLE_ORDERS.filter((o) => st.includes(o.status)).length;

  return (
    <div className="space-y-7 pb-4">
      <SampleBanner />

      <section className="flex flex-wrap items-end justify-between gap-5">
        <div>
          <p className="text-base text-ink-muted">Week of 2 Nov 2026</p>
          <h1 className="mt-1 text-[34px] font-semibold leading-tight tracking-tight sm:text-[44px]">
            <span className="sr-only">Overview — </span>
            {greeting()}, {first}!
          </h1>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Link to="/preview/receiving" className="btn-ghost border-transparent py-3"><Icon name="receiving" />Receive</Link>
          <Link to="/preview/invoice" className="btn-ghost border-transparent py-3"><Icon name="invoices" />Invoice</Link>
          <Link to="/preview/orders" className="btn-accent px-6 py-3"><Icon name="plus" />New order</Link>
        </div>
      </section>

      <section aria-label="Key figures" className="grid grid-cols-1 gap-5 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard accent icon="invoices" label="Net sales this week" value={cad(total).replace("CAD ", "")} unit="CAD" note="+12% vs last week" />
        <KpiCard icon="payments" label="Paid" value={cad(dec("98740.15")).replace("CAD ", "")} unit="CAD" note="78%" tone="success" />
        <KpiCard icon="clock" label="Outstanding" value={cad(dec("27316.75")).replace("CAD ", "")} unit="CAD" note="6 due" tone="warning" />
        <KpiCard icon="inventory" label="Boxes available" value={qty(dec("1842"), 0)} note="64 lots" />
      </section>

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
        <section className="card p-6 sm:p-8 xl:col-span-8" aria-labelledby="sales-h">
          <CardTitle id="sales-h" title="Net sales" to="/preview/orders" />
          <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2">
            <span className="num text-[44px] font-semibold leading-none tracking-tight">{cad(total).replace("CAD ", "")}</span>
            <span className="text-lg font-medium text-ink-muted">CAD</span>
            <Trend dir="up">12%</Trend>
          </div>
          <BarChart
            bars={week.map((w) => ({ label: w.d, value: Number(w.n / 1_000_000n), display: cad(w.n) }))}
            highlight={peak}
            axis={(v) => `$${shortNum(v)}`}
            ariaLabel={`Net sales per day, peak ${week[peak]?.d}: ${cad(week[peak]?.n ?? 0n)}`}
          />
        </section>

        <section className="card p-6 sm:p-8 xl:col-span-4" aria-labelledby="stage-h">
          <CardTitle id="stage-h" title="Orders by stage" to="/preview/orders" />
          <div className="mt-6">
            <DonutChart
              centerLabel="orders"
              ariaLabel="Orders by stage"
              slices={[
                { label: "Picking", value: n("PICKING", "PARTIAL") },
                { label: "New", value: n("NEW") },
                { label: "Dispatched", value: n("DISPATCHED", "INVOICED") },
                { label: "Cancelled", value: n("CANCELLED") },
              ]}
            />
          </div>
        </section>

        <section className="card p-6 sm:p-8 xl:col-span-8" aria-labelledby="queue-h">
          <CardTitle id="queue-h" title="Action queue" action={<Link to="/preview/orders" className="btn-ghost">View all</Link>} />
          <div className="relative mt-6 overflow-x-auto rounded-[20px] border border-line">
            <table className="w-full min-w-[600px] border-separate border-spacing-0">
              <caption className="sr-only">Orders waiting for action</caption>
              <thead>
                <tr>
                  <th scope="col" className="th">Order</th>
                  <th scope="col" className="th">Customer</th>
                  <th scope="col" className="th text-right">Boxes</th>
                  <th scope="col" className="th">Status</th>
                </tr>
              </thead>
              <tbody>
                {queue.map((o) => (
                  <tr key={o.id} className="hover:bg-subtle">
                    <td className="td num font-semibold"><Link to="/preview/orders" className="text-brand-primary hover:underline">{o.id}</Link></td>
                    <td className="td"><span className="flex items-center gap-3"><Avatar name={o.customer} tone="ink" /><span className="truncate font-medium">{o.customer}</span></span></td>
                    <td className="td num text-right text-base font-semibold">{o.ordered}</td>
                    <td className="td"><StatusChip tone={ORDER_STATUS[o.status].tone}>{ORDER_STATUS[o.status].label}</StatusChip></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="card p-6 sm:p-8 xl:col-span-4" aria-labelledby="act-h">
          <CardTitle id="act-h" title="Today" />
          <div className="mt-6 space-y-6">
            <Progress label="Orders fulfilled" value={17} max={24} display="17 / 24" />
            <Progress label="Boxes picked" value={1218} max={1460} display="1,218 / 1,460" />
          </div>
          <ul className="mt-7 space-y-1 border-t border-line pt-5">
            {SAMPLE_ACTIVITY.slice(0, 3).map((a) => (
              <li key={a.text} className="flex items-center gap-3.5 py-2">
                <span className={`grid size-11 shrink-0 place-items-center rounded-full ${TONE_TILE[a.tone]}`}><Icon name={TONE_ICON[a.tone]} /></span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-base font-medium">{a.text}</span>
                  <span className="num block text-sm text-ink-muted">{a.at}</span>
                </span>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}
