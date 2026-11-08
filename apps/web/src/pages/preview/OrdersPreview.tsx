import { useMemo, useState } from "react";
import { ORDER_STATUS, SAMPLE_ORDERS, type OrderStatus, type SampleOrder } from "../../mock/sample";
import { DataGrid, type Column } from "../../ui/DataGrid";
import { Icon } from "../../ui/Icon";
import { EmptyState, ErrorState, FilterBar, LoadingRows, PageHeader, SampleBanner, StatusChip } from "../../ui/primitives";

type F = "ALL" | OrderStatus;
type View = "data" | "loading" | "empty" | "error";

const cols: Column<SampleOrder>[] = [
  { key: "id", header: "Order", cell: (o) => <span className="num font-medium">{o.id}</span>, sort: (o) => o.id },
  { key: "customer", header: "Customer", cell: (o) => o.customer, sort: (o) => o.customer },
  { key: "date", header: "Date", cell: (o) => <span className="num">{o.date}</span>, sort: (o) => o.date },
  { key: "ordered", header: "Ordered", cell: (o) => o.ordered, sort: (o) => BigInt(o.ordered), align: "right" },
  { key: "picked", header: "Picked", cell: (o) => o.picked, sort: (o) => BigInt(o.picked), align: "right" },
  { key: "dispatched", header: "Dispatched", cell: (o) => o.dispatched, sort: (o) => BigInt(o.dispatched), align: "right" },
  {
    key: "back",
    header: "Backorder",
    cell: (o) => (o.backordered === "0" ? <span className="text-ink-muted">0</span> : <span className="font-semibold text-danger">{o.backordered}</span>),
    sort: (o) => BigInt(o.backordered),
    align: "right",
  },
  { key: "status", header: "Status", cell: (o) => <StatusChip tone={ORDER_STATUS[o.status].tone}>{ORDER_STATUS[o.status].label}</StatusChip>, sort: (o) => o.status },
  { key: "source", header: "Source", cell: (o) => <span className="text-ink-muted">{o.source}</span> },
];

export function OrdersPreview() {
  const [f, setF] = useState<F>("ALL");
  const [q, setQ] = useState("");
  const [view, setView] = useState<View>("data");
  const [open, setOpen] = useState<SampleOrder | null>(null);

  const rows = useMemo(
    () =>
      SAMPLE_ORDERS.filter(
        (o) => (f === "ALL" || o.status === f) && (q === "" || `${o.id} ${o.customer}`.toLowerCase().includes(q.toLowerCase())),
      ),
    [f, q],
  );
  const count = (s: OrderStatus) => SAMPLE_ORDERS.filter((o) => o.status === s).length;
  const options = [
    { value: "ALL" as F, label: "All", count: SAMPLE_ORDERS.length },
    ...(["NEW", "PICKING", "PARTIAL", "DISPATCHED", "INVOICED"] as const).map((s) => ({ value: s as F, label: ORDER_STATUS[s].label, count: count(s) })),
  ];

  return (
    <div className="space-y-4">
      <SampleBanner>
        Preview states:{" "}
        {(["data", "loading", "empty", "error"] as View[]).map((v) => (
          <button key={v} className={`mx-0.5 underline ${view === v ? "font-semibold" : ""}`} onClick={() => setView(v)}>{v}</button>
        ))}
      </SampleBanner>
      <PageHeader title="Orders" subtitle="Ordered vs picked vs dispatched, per order" actions={<button className="btn-primary"><Icon name="scan" size={16} />Scan order</button>} />
      <div className="flex gap-4">
        <section className="card min-w-0 flex-1 overflow-hidden">
          <div className="border-b border-line p-3">
            <FilterBar search={q} onSearch={setQ} options={options} value={f} onChange={setF} placeholder="Search order or customer" />
          </div>
          {view === "loading" && <LoadingRows cols={7} />}
          {view === "error" && <ErrorState title="Couldn't load orders" detail="The server didn't respond. Your filters are kept." onRetry={() => setView("data")} />}
          {view === "empty" && <EmptyState title="No orders yet" hint="Scan an order sheet or create one manually." />}
          {view === "data" && (
            <DataGrid
              caption="Orders"
              rows={rows}
              columns={cols}
              rowKey={(o) => o.id}
              onOpen={setOpen}
              selectedKey={open?.id}
              empty={<EmptyState title="No orders match" hint="Clear the search or pick another status." />}
            />
          )}
          <div className="flex items-center justify-between border-t border-line px-3 py-2 text-xs text-ink-muted">
            <span className="num">{view === "data" ? rows.length : 0} of {SAMPLE_ORDERS.length} (server pagination in Module 04)</span>
            <span>↑/↓ move · Enter open</span>
          </div>
        </section>

        {open && (
          <aside className="card hidden w-80 shrink-0 flex-col lg:flex" aria-label={`Order ${open.id}`}>
            <div className="flex items-center justify-between border-b border-line px-4 py-3">
              <div>
                <div className="num font-semibold">{open.id}</div>
                <div className="text-xs text-ink-muted">{open.customer}</div>
              </div>
              <button className="rounded p-1 hover:bg-canvas" onClick={() => setOpen(null)} aria-label="Close panel"><Icon name="x" /></button>
            </div>
            <div className="space-y-4 p-4 text-sm">
              <div className="grid aspect-[3/4] place-items-center rounded-[var(--radius-ctl)] border border-dashed border-line bg-canvas text-center text-xs text-ink-muted">
                Original scan (immutable)<br />appears here — sample has none
              </div>
              <div>
                <div className="kicker mb-1">Lines</div>
                <ul className="divide-y divide-line">
                  {open.lines.map((l, i) => (
                    <li key={i} className="flex justify-between py-1.5"><span>{l.product}</span><span className="num text-ink-muted">{l.qty} {l.unit}</span></li>
                  ))}
                </ul>
              </div>
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
