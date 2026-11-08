import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  api,
  has,
  type Customer,
  type CustomerSalesReport,
  type ProductSalesReport,
  type ProductSummary,
  type ReceivingReport,
  type Receivables,
  type ReportSort,
  type SalesReport,
  type StockFlowReport,
  type UnitCode,
} from "../api";
import { useAuth } from "../auth";
import { dec, qty } from "../lib/decimal";
import {
  amount,
  bucketFor,
  CHART_TITLE,
  chartBars,
  csvName,
  downloadCsv,
  isoDay,
  money,
  peak,
  presetRange,
  PRESETS,
  rangeProblem,
  signed,
  toCsv,
  UNIT_LABEL,
  unitsText,
  type Preset,
  type Range,
} from "../lib/reports";
import { DataGrid, type Column } from "../ui/DataGrid";
import { Icon } from "../ui/Icon";
import { BarChart, CardTitle, EmptyState, ErrorState, KpiCard, LoadingRows, PageHeader, Stat } from "../ui/primitives";

type Data = {
  sales: SalesReport;
  products: ProductSalesReport;
  customers: CustomerSalesReport;
  stock: StockFlowReport;
  receiving: ReceivingReport;
};

const PER: Record<UnitCode, string> = { BOX: "box", PIECE: "piece", LB: "lb", KG: "kg" };
const plain = (s: string) => qty(dec(s));
const day = (iso: string) => new Date(iso.length === 10 ? `${iso}T12:00:00` : iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });

function CsvButton({ label, onClick, disabled }: { label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button type="button" className="btn-ghost px-4 py-2" onClick={onClick} disabled={disabled} aria-label={`Download ${label} as CSV`}>
      <Icon name="download" size={16} />
      CSV
    </button>
  );
}

function Section({ id, title, action, note, children }: { id: string; title: string; action?: ReactNode; note?: ReactNode; children: ReactNode }) {
  return (
    <section className="card space-y-4 p-6" aria-labelledby={id}>
      <CardTitle id={id} title={title} action={action} />
      {note && <p className="text-sm text-ink-muted">{note}</p>}
      {children}
    </section>
  );
}

export function ReportsPage() {
  const { session } = useAuth();
  const allowed = !!session && has(session, "report.read");
  const canAging = !!session && has(session, "invoice.read") && (has(session, "payment.record") || has(session, "payment.verify"));
  const [preset, setPreset] = useState<Preset>("30d");
  const [range, setRange] = useState<Range>(() => presetRange("30d", isoDay(new Date())));
  const [customerId, setCustomerId] = useState("");
  const [productId, setProductId] = useState("");
  const [sort, setSort] = useState<ReportSort>("top");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [data, setData] = useState<Data | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const [aging, setAging] = useState<Receivables | null>(null);
  const seq = useRef(0);
  const problem = rangeProblem(range);

  useEffect(() => {
    if (!allowed) return;
    api.customers({ limit: 500 }).then(setCustomers, () => setCustomers([]));
    api.products({ active: true, limit: 200 }).then((p) => setProducts(p.items), () => setProducts([]));
  }, [allowed]);

  useEffect(() => {
    if (canAging) api.receivables().then(setAging, () => setAging(null));
  }, [canAging]);

  const load = useCallback(async () => {
    if (!allowed || problem) return;
    const n = ++seq.current;
    const q = { date_from: range.from, date_to: range.to, product_id: productId || undefined };
    const sq = { ...q, customer_id: customerId || undefined };
    setBusy(true);
    try {
      const [sales, prods, custs, stock, receiving] = await Promise.all([
        api.reportSales({ ...sq, bucket: bucketFor(range) }),
        api.reportByProduct({ ...sq, sort, limit: 50 }),
        api.reportByCustomer({ ...sq, limit: 50 }),
        api.reportStockFlow(q),
        api.reportReceiving(q),
      ]);
      if (n !== seq.current) return; // a newer filter change won
      setData({ sales, products: prods, customers: custs, stock, receiving });
      setError(false);
    } catch {
      if (n === seq.current) setError(true);
    } finally {
      if (n === seq.current) setBusy(false);
    }
  }, [allowed, problem, range, customerId, productId, sort]);

  useEffect(() => void load(), [load]);

  if (!session) return null;
  if (!allowed)
    return (
      <div className="space-y-5">
        <PageHeader title="Reports" />
        <EmptyState title="You don't have access to reports" hint="Ask a manager or finance if you need sales, stock or receiving figures." />
      </div>
    );

  const choose = (p: Preset) => {
    setPreset(p);
    if (p !== "custom") setRange(presetRange(p, isoDay(new Date())));
  };
  const edit = (k: keyof Range, v: string) => {
    setPreset("custom");
    setRange((r) => ({ ...r, [k]: v }));
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title="Reports"
        subtitle="Sales, stock movement and receiving for the period you choose. Figures come from issued invoices, the stock ledger and posted receipts."
        actions={
          canAging ? (
            <Link className="btn-ghost" to="/receivables">
              What customers owe
            </Link>
          ) : undefined
        }
      />

      <section className="card space-y-4 p-6" aria-labelledby="rep-filters">
        <h2 id="rep-filters" className="sr-only">
          Filters
        </h2>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Period">
          {PRESETS.map((o) => (
            <button
              key={o.value}
              type="button"
              aria-pressed={preset === o.value}
              onClick={() => choose(o.value)}
              className={`chip cursor-pointer px-3.5 py-1.5 text-[13px] ${preset === o.value ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:bg-subtle"}`}
            >
              {o.label}
            </button>
          ))}
        </div>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <label className="block">
            <span className="label">From</span>
            <input className="input" type="date" value={range.from} max={range.to} onChange={(e) => edit("from", e.target.value)} />
          </label>
          <label className="block">
            <span className="label">To</span>
            <input className="input" type="date" value={range.to} min={range.from} onChange={(e) => edit("to", e.target.value)} />
          </label>
          <label className="block">
            <span className="label">Customer (sales only)</span>
            <select className="input" value={customerId} onChange={(e) => setCustomerId(e.target.value)}>
              <option value="">All customers</option>
              {customers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                  {c.external_ref ? ` (${c.external_ref})` : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="label">Product</span>
            <select className="input" value={productId} onChange={(e) => setProductId(e.target.value)}>
              <option value="">All products</option>
              {products.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} ({p.sku})
                </option>
              ))}
            </select>
          </label>
        </div>
        {problem && (
          <p role="alert" className="alert-warning">
            {problem}
          </p>
        )}
      </section>

      {error ? (
        <ErrorState title="Couldn't load the reports" onRetry={() => void load()} />
      ) : !data ? (
        <div className="card">
          <LoadingRows cols={5} />
        </div>
      ) : (
        <div className={`space-y-5 transition-opacity ${busy ? "opacity-60" : ""}`} aria-busy={busy}>
          <Body data={data} aging={aging} range={range} sort={sort} onSort={setSort} />
        </div>
      )}
    </div>
  );
}

function Body({ data, aging, range, sort, onSort }: { data: Data; aging: Receivables | null; range: Range; sort: ReportSort; onSort: (s: ReportSort) => void }) {
  const { sales, products, customers, stock, receiving } = data;
  const { unit, bars } = chartBars(sales.series, sales.bucket);
  const top = peak(bars);
  const period = `${day(sales.date_from)} – ${day(sales.date_to)}`;

  type PRow = ProductSalesReport["rows"][number];
  const pCols: Column<PRow>[] = [
    {
      key: "p",
      header: "Product",
      cell: (r) => (
        <Link className="font-medium text-brand-primary hover:underline" to={`/products/${r.product_id}`}>
          {r.name}
          <span className="font-normal text-ink-muted"> · {r.sku}</span>
        </Link>
      ),
      sort: (r) => r.name,
    },
    { key: "q", header: "Sold", align: "right", cell: (r) => <span className="num whitespace-nowrap">{amount(r.qty, r.unit_code)}</span> },
    { key: "r", header: "Average rate", align: "right", cell: (r) => <span className="num whitespace-nowrap">{`${money(r.avg_rate)} / ${PER[r.unit_code]}`}</span> },
    { key: "n", header: "Net sales", align: "right", cell: (r) => <span className="num whitespace-nowrap font-semibold">{money(r.net_sales)}</span>, sort: (r) => dec(r.net_sales) },
    { key: "i", header: "Invoices", align: "right", cell: (r) => <span className="num whitespace-nowrap">{r.invoice_count}</span>, sort: (r) => BigInt(r.invoice_count) },
    { key: "c", header: "Customers", align: "right", cell: (r) => <span className="num whitespace-nowrap">{r.customer_count}</span>, sort: (r) => BigInt(r.customer_count) },
  ];
  type CRow = CustomerSalesReport["rows"][number];
  const cCols: Column<CRow>[] = [
    {
      key: "c",
      header: "Customer",
      cell: (r) => (
        <>
          {r.name}
          {r.external_ref && <span className="text-ink-muted"> · {r.external_ref}</span>}
        </>
      ),
      sort: (r) => r.name,
    },
    { key: "q", header: "Quantities", cell: (r) => <span className="num whitespace-nowrap">{unitsText(r.quantities)}</span> },
    { key: "i", header: "Invoices", align: "right", cell: (r) => <span className="num whitespace-nowrap">{r.invoice_count}</span>, sort: (r) => BigInt(r.invoice_count) },
    { key: "n", header: "Net sales", align: "right", cell: (r) => <span className="num whitespace-nowrap font-semibold">{money(r.net_sales)}</span>, sort: (r) => dec(r.net_sales) },
    { key: "g", header: "With tax", align: "right", cell: (r) => <span className="num whitespace-nowrap">{money(r.gross)}</span> },
    { key: "l", header: "Last invoice", align: "right", cell: (r) => day(r.last_invoice_at), sort: (r) => r.last_invoice_at },
  ];
  type SRow = StockFlowReport["rows"][number];
  const sCols: Column<SRow>[] = [
    {
      key: "p",
      header: "Product",
      cell: (r) => (
        <Link className="font-medium text-brand-primary hover:underline" to={`/products/${r.product_id}`}>
          {r.name}
          <span className="font-normal text-ink-muted"> · {r.sku}</span>
        </Link>
      ),
      sort: (r) => r.name,
    },
    { key: "u", header: "Unit", cell: (r) => UNIT_LABEL[r.unit_code] },
    { key: "o", header: "Opening", align: "right", cell: (r) => <span className="num whitespace-nowrap">{signed(r.opening)}</span> },
    { key: "r", header: "Received", align: "right", cell: (r) => <span className="num whitespace-nowrap">{signed(r.received)}</span> },
    { key: "d", header: "Dispatched", align: "right", cell: (r) => <span className="num whitespace-nowrap">{signed(r.dispatched)}</span> },
    { key: "x", header: "Other", align: "right", cell: (r) => <span className="num whitespace-nowrap">{signed(r.other)}</span> },
    { key: "c", header: "Closing", align: "right", cell: (r) => <span className="num whitespace-nowrap font-semibold">{signed(r.closing)}</span>, sort: (r) => dec(r.closing) },
  ];
  type VRow = ReceivingReport["rows"][number];
  const vCols: Column<VRow>[] = [
    {
      key: "v",
      header: "Vendor",
      cell: (r) => (
        <>
          {r.name}
          {r.country_code && <span className="text-ink-muted"> · {r.country_code}</span>}
        </>
      ),
      sort: (r) => r.name,
    },
    { key: "u", header: "Unit", cell: (r) => UNIT_LABEL[r.unit_code] },
    { key: "s", header: "Shipments", align: "right", cell: (r) => <span className="num whitespace-nowrap">{r.shipments}</span> },
    { key: "d", header: "Declared", align: "right", cell: (r) => <span className="num whitespace-nowrap">{plain(r.declared)}</span> },
    { key: "a", header: "Accepted", align: "right", cell: (r) => <span className="num whitespace-nowrap">{plain(r.accepted)}</span> },
    { key: "m", header: "Damaged", align: "right", cell: (r) => <span className="num whitespace-nowrap">{plain(r.damaged)}</span> },
    { key: "j", header: "Rejected", align: "right", cell: (r) => <span className="num whitespace-nowrap">{plain(r.rejected)}</span> },
  ];

  const csv = {
    products: () =>
      downloadCsv(
        csvName(`products-${sort}`, range),
        toCsv(
          ["SKU", "Product", "Unit", "Quantity", "Net sales (CAD, before tax)", "Average rate (CAD)", "Invoices", "Customers"],
          products.rows.map((r) => [r.sku, r.name, r.unit_code, r.qty, r.net_sales, r.avg_rate, r.invoice_count, r.customer_count]),
        ),
      ),
    customers: () =>
      downloadCsv(
        csvName("customers", range),
        toCsv(
          ["Customer", "Customer ref", "Invoices", "Quantities", "Net sales (CAD, before tax)", "With tax (CAD)", "Last invoice"],
          customers.rows.map((r) => [r.name, r.external_ref, r.invoice_count, unitsText(r.quantities), r.net_sales, r.gross, r.last_invoice_at]),
        ),
      ),
    stock: () =>
      downloadCsv(
        csvName("stock-flow", range),
        toCsv(
          ["SKU", "Product", "Unit", "Opening", "Received", "Dispatched", "Other", "Closing"],
          stock.rows.map((r) => [r.sku, r.name, r.unit_code, r.opening, r.received, r.dispatched, r.other, r.closing]),
        ),
      ),
    receiving: () =>
      downloadCsv(
        csvName("receiving", range),
        toCsv(
          ["Vendor code", "Vendor", "Country", "Unit", "Shipments", "Declared", "Accepted", "Damaged", "Rejected"],
          receiving.rows.map((r) => [r.code, r.name, r.country_code, r.unit_code, r.shipments, r.declared, r.accepted, r.damaged, r.rejected]),
        ),
      ),
  };

  return (
    <>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <KpiCard label="Net sales" value={money(sales.net_sales)} icon="reports" accent note="Before tax" />
        <KpiCard label="Invoices" value={sales.invoice_count} icon="invoices" note={`${sales.customer_count} ${sales.customer_count === 1 ? "customer" : "customers"}`} />
        <KpiCard label="Invoiced with tax" value={money(sales.gross)} icon="coins" note={`Tax ${money(sales.tax)}`} />
        {aging ? (
          <KpiCard label="Owed now" value={money(aging.total)} icon="payments" tone="warning" note="All open invoices" />
        ) : (
          <KpiCard label="Received in period" value={receiving.shipments} icon="receiving" note="Posted shipments" />
        )}
      </div>

      <div className="grid gap-5 xl:grid-cols-[2fr_1fr]">
        <Section id="rep-chart" title={CHART_TITLE[unit]} note={period}>
          {top < 0 ? (
            <EmptyState title="No invoices in this period" hint="Try a longer period or clear the filters." />
          ) : (
            <BarChart bars={bars} highlight={top} ariaLabel={`${CHART_TITLE[unit]}, ${period}`} axis={(v) => (v >= 1000 ? `${Math.round(v / 1000)}k` : String(Math.round(v)))} />
          )}
        </Section>
        <Section id="rep-qty" title="Quantities sold" note="Each unit is counted on its own.">
          {sales.quantities.length ? (
            <div className="grid gap-5">
              {sales.quantities.map((q) => (
                <Stat key={q.unit_code} icon="boxes" label={UNIT_LABEL[q.unit_code]} value={plain(q.qty)} />
              ))}
            </div>
          ) : (
            <EmptyState title="Nothing sold yet" />
          )}
        </Section>
      </div>

      <Section
        id="rep-products"
        title="Products"
        note={`${products.rows.length} of ${products.total_rows} products by net sales. Profit and margin are not shown until the costing method is agreed.`}
        action={
          <>
            <div className="flex gap-1.5" role="group" aria-label="Product order">
              {(["top", "least"] as const).map((s) => (
                <button
                  key={s}
                  type="button"
                  aria-pressed={sort === s}
                  onClick={() => onSort(s)}
                  className={`chip cursor-pointer px-3.5 py-1.5 text-[13px] ${sort === s ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:bg-subtle"}`}
                >
                  {s === "top" ? "Best sellers" : "Least sold"}
                </button>
              ))}
            </div>
            <CsvButton label="products" onClick={csv.products} disabled={!products.rows.length} />
          </>
        }
      >
        <DataGrid rows={products.rows} columns={pCols} rowKey={(r) => `${r.product_id}:${r.unit_code}`} caption={`Products, ${period}`} empty={<EmptyState title="No products sold in this period" />} />
      </Section>

      <Section id="rep-customers" title="Customers" note={`${customers.rows.length} of ${customers.total_rows} customers by net sales.`} action={<CsvButton label="customers" onClick={csv.customers} disabled={!customers.rows.length} />}>
        <DataGrid rows={customers.rows} columns={cCols} rowKey={(r) => r.customer_id} caption={`Customers, ${period}`} empty={<EmptyState title="No customers invoiced in this period" />} />
      </Section>

      <Section
        id="rep-stock"
        title="Stock movement"
        note="Opening + received + dispatched + other = closing, from the stock ledger. Other covers opening balances, adjustments, counts, damage, returns and reversals. Closing includes damaged stock."
        action={<CsvButton label="stock movement" onClick={csv.stock} disabled={!stock.rows.length} />}
      >
        <DataGrid rows={stock.rows} columns={sCols} rowKey={(r) => `${r.product_id}:${r.unit_code}`} caption={`Stock movement, ${period}`} empty={<EmptyState title="No stock and no movements in this period" />} />
      </Section>

      <Section
        id="rep-receiving"
        title="Receiving by vendor"
        note={`${receiving.shipments} posted ${receiving.shipments === 1 ? "shipment" : "shipments"}. Declared is what the vendor's documents say; accepted, damaged and rejected are what was counted.`}
        action={<CsvButton label="receiving" onClick={csv.receiving} disabled={!receiving.rows.length} />}
      >
        <DataGrid rows={receiving.rows} columns={vCols} rowKey={(r) => `${r.vendor_id}:${r.unit_code}`} caption={`Receiving by vendor, ${period}`} empty={<EmptyState title="No shipments posted in this period" />} />
      </Section>
    </>
  );
}
