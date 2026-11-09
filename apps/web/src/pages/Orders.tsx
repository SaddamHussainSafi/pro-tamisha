import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  api,
  canInWarehouse,
  has,
  type Customer,
  type Order,
  type OrderLine,
  type OrderStatus,
  type OrderSummary,
  type InvoiceSummary,
  type Product,
  type ProductSummary,
  type UnitCode,
} from "../api";
import { useAuth } from "../auth";
import { qtyText, stockUnits, wholeOnly } from "../lib/inventory";
import { AMENDMENT_LABEL, lineAmount, ORDER_STATUS, ordersMessage, PICK_STATUS } from "../lib/orders";
import { rateText } from "../lib/pricingErrors";
import { DataGrid, type Column } from "../ui/DataGrid";
import { NumericInput } from "../ui/NumericInput";
import { ConfirmDialog, EmptyState, ErrorState, FilterBar, LoadingRows, PageHeader, PageSkeleton, StatusChip } from "../ui/primitives";

const link = "font-medium text-brand-primary-hover underline-offset-2 hover:underline";
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");

export function OrderChip({ status }: { status: OrderStatus }) {
  return <StatusChip tone={ORDER_STATUS[status].tone}>{ORDER_STATUS[status].label}</StatusChip>;
}

/* ================================ List ================================ */

type View = "open" | "draft" | "backorder" | "closed" | "all";
const VIEWS: { value: View; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "draft", label: "Drafts" },
  { value: "backorder", label: "Backorders" },
  { value: "closed", label: "Closed" },
  { value: "all", label: "All" },
];
const VIEW_STATUS: Record<View, string | undefined> = {
  open: "CONFIRMED,PICKING,PARTIALLY_FULFILLED",
  draft: "DRAFT",
  backorder: undefined,
  closed: "FULFILLED,CLOSED_WITH_SHORTAGE,CANCELLED",
  all: undefined,
};

export function OrdersPage() {
  const { session } = useAuth();
  const nav = useNavigate();
  const [view, setView] = useState<View>("open");
  const [search, setSearch] = useState("");
  const [rows, setRows] = useState<OrderSummary[] | null>(null);
  const [total, setTotal] = useState(0);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState(false);

  const load = useCallback(async (more?: string) => {
    try {
      const page = await api.orders({
        status: VIEW_STATUS[view],
        backorder: view === "backorder" ? true : undefined,
        q: search.trim(),
        cursor: more,
        limit: 50,
      });
      setRows((prev) => (more && prev ? [...prev, ...page.items] : page.items));
      setTotal(page.total);
      setCursor(page.next_cursor);
      setError(false);
    } catch {
      setError(true);
    }
  }, [view, search]);
  useEffect(() => {
    const t = setTimeout(() => void load(), 200);
    return () => clearTimeout(t);
  }, [load]);

  if (!session) return null;
  const columns: Column<OrderSummary>[] = [
    { key: "no", header: "Order", cell: (r) => <Link className={link} to={`/orders/${r.id}`}>{r.order_no}</Link>, sort: (r) => r.order_no },
    { key: "cust", header: "Customer", cell: (r) => r.customer_name, sort: (r) => r.customer_name },
    { key: "lines", header: "Lines", align: "right", cell: (r) => <span className="num">{r.line_count}</span> },
    { key: "for", header: "Wanted for", cell: (r) => (r.requested_for ? new Date(r.requested_for).toLocaleDateString() : "—") },
    { key: "st", header: "Status", cell: (r) => <OrderChip status={r.status} /> },
    { key: "at", header: "Created", cell: (r) => when(r.created_at), sort: (r) => r.created_at },
  ];
  return (
    <div className="space-y-5">
      <PageHeader
        title="Sales orders"
        subtitle="Customer requests as written. Invoices are numbered only when goods are actually supplied."
        actions={has(session, "sales_order.manage") && <Link className="btn-primary" to="/orders/new">New order</Link>}
      />
      <FilterBar search={search} onSearch={setSearch} options={VIEWS} value={view} onChange={setView} placeholder="Search order or customer" />
      <section className="card overflow-clip">
        {error ? (
          <ErrorState title="Couldn't load orders" onRetry={() => void load()} />
        ) : rows === null ? (
          <LoadingRows cols={7} />
        ) : (
          <>
            <DataGrid
              rows={rows}
              columns={columns}
              rowKey={(r) => r.id}
              caption={`Sales orders (${total})`}
              onOpen={(r) => nav(`/orders/${r.id}`)}
              empty={<EmptyState title="No orders here" hint="Try another filter, or create a new order." />}
            />
            {cursor && (
              <div className="flex justify-center border-t border-line p-4">
                <button className="btn-ghost" onClick={() => void load(cursor)}>Load more</button>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}

/* ================================ New order ================================ */

type DraftLine = { key: number; product_id: string; unit_code: UnitCode | ""; qty: string; text: string };

export function NewOrderPage() {
  const { session } = useAuth();
  const nav = useNavigate();
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [details, setDetails] = useState<Record<string, Product>>({});
  const [customer, setCustomer] = useState("");
  const [wh, setWh] = useState("");
  const [sourceText, setSourceText] = useState("");
  const [lines, setLines] = useState<DraftLine[]>([{ key: 1, product_id: "", unit_code: "", qty: "", text: "" }]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.customers({ limit: 200 }).then((c) => setCustomers(c.filter((x) => x.active)), () => setCustomers([]));
    api.products({ active: true, limit: 200 }).then((p) => setProducts(p.items), () => setProducts([]));
    // No warehouse field: new orders go to the first warehouse this user may manage orders in.
    api.warehouses().then((w) => {
      const ok = w.filter((x) => x.active && session && canInWarehouse(session, "sales_order.manage", x.id));
      setWh((cur) => cur || (ok[0]?.id ?? ""));
    }, () => setWh(""));
  }, [session]);

  const pick = async (key: number, product_id: string) => {
    let p = details[product_id];
    if (!p && product_id) {
      p = await api.product(product_id);
      setDetails((d) => ({ ...d, [product_id]: p as Product }));
    }
    const unit = p ? (stockUnits(p).includes(p.stock_unit) ? p.stock_unit : stockUnits(p)[0] ?? "") : "";
    setLines((ls) => ls.map((l) => (l.key === key ? { ...l, product_id, unit_code: unit } : l)));
  };
  const patch = (key: number, v: Partial<DraftLine>) => setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...v } : l)));

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    const filled = lines.filter((l) => l.product_id);
    if (!customer || !wh || filled.length === 0) return setError(wh ? "Choose a customer and at least one product." : "Your account can't create orders.");
    if (filled.some((l) => !l.qty || !l.unit_code)) return setError("Every line needs a unit and a quantity.");
    setBusy(true);
    try {
      const o = await api.createOrder({
        customer_id: customer,
        warehouse_id: wh,
        source_text: sourceText,
        lines: filled.map((l) => ({ product_id: l.product_id, unit_code: l.unit_code as UnitCode, qty: l.qty, source_line_text: l.text })),
      });
      nav(`/orders/${o.id}`);
    } catch (err) {
      setError(ordersMessage(err));
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <PageHeader title="New sales order" subtitle="Saved as a draft. Prices are fixed when you confirm." />
      <form className="card space-y-4 p-6" onSubmit={(e) => void submit(e)} aria-label="New sales order">
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <label className="label" htmlFor="so-cust">Customer</label>
            <select id="so-cust" className="input" value={customer} onChange={(e) => setCustomer(e.target.value)} required>
              <option value="">Choose…</option>
              {customers.map((c) => <option key={c.id} value={c.id}>{c.external_ref ? `${c.external_ref} — ` : ""}{c.name}</option>)}
            </select>
          </div>
        </div>
        <div>
          <label className="label" htmlFor="so-src">Original request (as the customer wrote or said it)</label>
          <textarea id="so-src" className="input min-h-20" value={sourceText} onChange={(e) => setSourceText(e.target.value)} maxLength={10000} />
        </div>
        <fieldset className="space-y-2">
          <legend className="label">Lines</legend>
          {lines.map((l, i) => {
            const p = details[l.product_id];
            const units = p ? stockUnits(p) : [];
            return (
              <div key={l.key} className="grid items-end gap-2 md:grid-cols-[2fr_1fr_1fr_2fr_auto]" data-testid={`line-${i + 1}`}>
                <div>
                  <label className="label" htmlFor={`ln-p-${l.key}`}>Product {i + 1}</label>
                  <select id={`ln-p-${l.key}`} className="input" value={l.product_id} onChange={(e) => void pick(l.key, e.target.value)}>
                    <option value="">Choose…</option>
                    {products.map((x) => <option key={x.id} value={x.id}>{x.sku} — {x.name}</option>)}
                  </select>
                </div>
                <div>
                  <label className="label" htmlFor={`ln-u-${l.key}`}>Unit</label>
                  <select id={`ln-u-${l.key}`} className="input" value={l.unit_code} onChange={(e) => patch(l.key, { unit_code: e.target.value as UnitCode })}>
                    {units.length === 0 && <option value="">—</option>}
                    {units.map((u) => <option key={u} value={u}>{u}</option>)}
                  </select>
                </div>
                <NumericInput
                  label="Quantity"
                  value={l.qty}
                  onChange={(v) => patch(l.key, { qty: v })}
                  places={l.unit_code && wholeOnly(l.unit_code) ? 0 : 4}
                  invalidHint={l.unit_code && wholeOnly(l.unit_code) ? "Whole numbers only" : undefined}
                />
                <div>
                  <label className="label" htmlFor={`ln-t-${l.key}`}>As written</label>
                  <input id={`ln-t-${l.key}`} className="input" value={l.text} onChange={(e) => patch(l.key, { text: e.target.value })} maxLength={500} />
                </div>
                <button
                  type="button"
                  className="btn-ghost"
                  aria-label={`Remove line ${i + 1}`}
                  disabled={lines.length === 1}
                  onClick={() => setLines((ls) => ls.filter((x) => x.key !== l.key))}
                >
                  Remove
                </button>
              </div>
            );
          })}
          <button
            type="button"
            className="btn-ghost"
            onClick={() => setLines((ls) => [...ls, { key: Math.max(...ls.map((x) => x.key)) + 1, product_id: "", unit_code: "", qty: "", text: "" }])}
          >
            Add line
          </button>
        </fieldset>
        {error && <p className="alert-danger" role="alert">{error}</p>}
        <div className="flex justify-end gap-2">
          <Link className="btn-ghost" to="/orders">Cancel</Link>
          <button className="btn-primary" disabled={busy}>{busy ? "Saving…" : "Save draft"}</button>
        </div>
      </form>
    </div>
  );
}

/* ================================ Detail ================================ */

type Dialog = "none" | "confirm" | "cancel" | "close" | "pick";

export function OrderPage() {
  const { id = "" } = useParams();
  const { session } = useAuth();
  const nav = useNavigate();
  const [o, setO] = useState<Order | null>(null);
  const [loadErr, setLoadErr] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog>("none");
  const [reason, setReason] = useState("");
  const [lineCancel, setLineCancel] = useState<OrderLine | null>(null);

  const load = useCallback(async () => {
    try {
      setO(await api.order(id));
      setLoadErr(false);
    } catch {
      setLoadErr(true);
    }
  }, [id]);
  useEffect(() => void load(), [load]);

  const run = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      setDialog("none");
      setReason("");
      await load();
    } catch (err) {
      setError(ordersMessage(err));
      setDialog("none");
      await load();
    }
  };

  if (!session) return null;
  if (loadErr) return <ErrorState title="Couldn't load this order" onRetry={() => void load()} />;
  if (!o) return <PageSkeleton />;
  const manage = canInWarehouse(session, "sales_order.manage", o.warehouse_id);
  const close = canInWarehouse(session, "sales_order.close", o.warehouse_id);
  const open = ["CONFIRMED", "PICKING", "PARTIALLY_FULFILLED"].includes(o.status);
  const pending = o.lines.some((l) => Number(l.pending_qty) > 0);
  const awaiting = o.lines.some((l) => Number(l.awaiting_invoice_qty) > 0);
  const total = o.lines.reduce<string[]>((acc, l) => (l.rate ? [...acc, lineAmount(l.rate, l.ordered_qty)] : acc), []);

  const columns: Column<OrderLine>[] = [
    { key: "n", header: "#", cell: (l) => <span className="num">{l.line_no}</span> },
    { key: "p", header: "Product", cell: (l) => <span><span className="font-medium">{l.sku}</span> <span className="text-ink-muted">{l.product_name}</span>{l.source_line_text && <span className="block text-xs text-ink-muted">“{l.source_line_text}”</span>}</span> },
    { key: "u", header: "Unit", cell: (l) => l.unit_code },
    { key: "o", header: "Ordered", align: "right", cell: (l) => <span className="num">{qtyText(l.ordered_qty)}</span> },
    { key: "s", header: "Supplied", align: "right", cell: (l) => <span className="num">{qtyText(l.dispatched_qty)}</span> },
    { key: "a", header: "Awaiting invoice", align: "right", cell: (l) => <span className="num">{qtyText(l.awaiting_invoice_qty)}</span> },
    { key: "pd", header: "Pending", align: "right", cell: (l) => <span className="num font-semibold" data-testid={`pending-${l.sku}`}>{qtyText(l.pending_qty)}{l.backorder && <span className="ml-1 text-xs font-normal text-ink">backorder</span>}</span> },
    { key: "c", header: "Cancelled", align: "right", cell: (l) => <span className="num text-ink-muted">{qtyText(l.cancelled_qty)}</span> },
    { key: "r", header: "Rate (CAD)", align: "right", cell: (l) => (l.rate ? <span className="num" title={`${l.price_scope ?? ""} · ${l.price_version_code ?? ""}`}>{rateText(l.rate)}</span> : <span className="text-ink-muted">set at confirm</span>) },
    ...(close && open
      ? [{ key: "x", header: "", cell: (l: OrderLine) => (Number(l.pending_qty) > 0 ? <button className="btn-ghost py-1 text-xs" onClick={() => setLineCancel(l)}>Cancel qty</button> : null) }]
      : []),
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title={o.order_no}
        subtitle={<><span className="font-medium text-ink">{o.customer_name}</span>{o.customer_ref && ` (${o.customer_ref})`} · <OrderChip status={o.status} /></>}
        actions={
          <>
            {manage && o.status === "DRAFT" && <button className="btn-ghost" onClick={() => setDialog("cancel")}>Cancel draft</button>}
            {close && o.status === "CONFIRMED" && o.attempts.every((a) => a.status === "ABORTED") && (
              <button className="btn-ghost" onClick={() => setDialog("cancel")}>Cancel order</button>
            )}
            {close && ["CONFIRMED", "PARTIALLY_FULFILLED"].includes(o.status) && pending && !awaiting && o.attempts.some((a) => a.status !== "ABORTED") && (
              <button className="btn-ghost" onClick={() => setDialog("close")}>Close with shortage</button>
            )}
            {o.open_attempt_id && <Link className="btn-ghost" to={`/picking/${o.open_attempt_id}`}>Open picking sheet</Link>}
            {manage && open && pending && !o.open_attempt_id && (
              <button className="btn-primary" onClick={() => setDialog("pick")}>Print picking sheet</button>
            )}
            {manage && o.status === "DRAFT" && <button className="btn-primary" onClick={() => setDialog("confirm")}>Confirm order</button>}
          </>
        }
      />
      {error && <p className="alert-danger" role="alert">{error}</p>}
      {o.has_backorder && (
        <p className="alert-warning" role="status">Backorder: some items were short on an earlier pick and are still pending for this customer.</p>
      )}
      <section className="card p-0" aria-label="Order lines">
        <DataGrid rows={o.lines} columns={columns} rowKey={(l) => l.id} caption="Order lines" />
        {total.length > 0 && (
          <p className="border-t border-line px-4 py-2 text-right text-sm text-ink-muted">
            Prices fixed at confirmation{o.confirmed_by_name && ` by ${o.confirmed_by_name}`} · {when(o.confirmed_at)}
          </p>
        )}
      </section>
      <div className="grid gap-5 lg:grid-cols-2">
        <section className="card space-y-2 p-6" aria-labelledby="src-h">
          <h2 id="src-h" className="font-semibold">Original request</h2>
          <p className="text-xs text-ink-muted">Kept exactly as captured ({o.source === "SCAN" ? "scanned" : o.source === "WEB" ? "sent from the website, converted" : "typed"} by {o.created_by_name ?? "system"}, {when(o.created_at)}). It never changes.</p>
          {o.source_text ? <pre className="whitespace-pre-wrap rounded bg-canvas p-3 text-sm">{o.source_text}</pre> : <p className="text-sm text-ink-muted">No free text.</p>}
          <ul className="text-sm">
            {o.source_snapshot.map((s) => <li key={s.line_no}><span className="num">{s.line_no}.</span> {s.sku} — {qtyText(s.qty ?? "0")} {s.unit_code}{s.text && <span className="text-ink-muted"> (“{s.text}”)</span>}</li>)}
          </ul>
        </section>
        <section className="card space-y-3 p-6" aria-labelledby="pk-h">
          <h2 id="pk-h" className="font-semibold">Picking sheets</h2>
          {o.attempts.length === 0 ? (
            <p className="text-sm text-ink-muted">None yet.</p>
          ) : (
            <ul className="divide-y divide-line text-sm">
              {o.attempts.map((a) => (
                <li key={a.id} className="flex items-center justify-between py-2">
                  <Link className={link} to={`/picking/${a.id}`}>{a.pick_code}</Link>
                  <StatusChip tone={PICK_STATUS[a.status].tone}>{PICK_STATUS[a.status].label}</StatusChip>
                </li>
              ))}
            </ul>
          )}
          <OrderInvoices orderId={o.id} />
          <h2 className="pt-2 font-semibold">Changes</h2>
          {o.amendments.length === 0 ? (
            <p className="text-sm text-ink-muted">No quantities cancelled.</p>
          ) : (
            <ul className="space-y-1 text-sm">
              {o.amendments.map((a) => (
                <li key={a.id}>
                  <span className="font-medium">{AMENDMENT_LABEL[a.kind] ?? a.kind}</span>
                  {a.line_no !== null && ` · line ${a.line_no}`}{a.qty && ` · ${qtyText(a.qty)}`} — “{a.reason}” <span className="text-ink-muted">({a.actor_name ?? "system"}, {when(a.created_at)})</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <ConfirmDialog open={dialog === "confirm"} title="Confirm this order?" confirmLabel="Confirm order" onClose={() => setDialog("none")} onConfirm={() => void run(() => api.confirmOrder(o.id, o.version))}>
        Today's prices for {o.customer_name} are fixed on every line. No invoice number is assigned yet.
      </ConfirmDialog>
      <ConfirmDialog open={dialog === "pick"} title="Print a picking sheet?" confirmLabel="Create sheet" onClose={() => setDialog("none")} onConfirm={() => void run(async () => { const p = await api.openPick(o.id, o.version); nav(`/picking/${p.id}`); })}>
        The sheet lists every pending quantity. The warehouse records what was actually packed.
      </ConfirmDialog>
      <ReasonDialog
        open={dialog === "cancel" || dialog === "close"}
        title={dialog === "close" ? "Close with shortage?" : "Cancel this order?"}
        body={dialog === "close" ? "All pending quantities are cancelled. Supplied quantities are unaffected." : "Nothing has been picked. The order stays on record as cancelled."}
        confirmLabel={dialog === "close" ? "Close order" : "Cancel order"}
        reason={reason}
        setReason={setReason}
        onClose={() => setDialog("none")}
        onConfirm={() => void run(() => (dialog === "close" ? api.closeOrder(o.id, o.version, reason) : api.cancelOrder(o.id, o.version, reason)))}
      />
      {lineCancel && (
        <LineCancelDialog
          line={lineCancel}
          onClose={() => setLineCancel(null)}
          onConfirm={(qty, why) => { setLineCancel(null); void run(() => api.cancelLineQty(o.id, lineCancel.id, { version: o.version, qty, reason: why })); }}
        />
      )}
    </div>
  );
}

export function ReasonDialog(props: {
  open: boolean;
  title: string;
  body: string;
  confirmLabel: string;
  reason: string;
  setReason: (s: string) => void;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const ok = props.reason.trim().length >= 3;
  return (
    <ConfirmDialog open={props.open} title={props.title} confirmLabel={props.confirmLabel} danger onClose={props.onClose} onConfirm={() => ok && props.onConfirm()}>
      <p>{props.body}</p>
      <label className="label mt-3" htmlFor="reason-input">Reason (required)</label>
      <input id="reason-input" className="input" value={props.reason} onChange={(e) => props.setReason(e.target.value)} maxLength={500} />
      {!ok && <p className="mt-1 text-xs">At least 3 characters.</p>}
    </ConfirmDialog>
  );
}

function LineCancelDialog({ line, onClose, onConfirm }: { line: OrderLine; onClose: () => void; onConfirm: (qty: string, reason: string) => void }) {
  const [qty, setQty] = useState(qtyText(line.pending_qty));
  const [reason, setReason] = useState("");
  const ok = reason.trim().length >= 3 && qty !== "";
  return (
    <ConfirmDialog open title={`Cancel quantity on line ${line.line_no}?`} confirmLabel="Cancel quantity" danger onClose={onClose} onConfirm={() => ok && onConfirm(qty, reason)}>
      <p>{line.sku}: {qtyText(line.pending_qty)} {line.unit_code} pending.</p>
      <NumericInput label="Quantity to cancel" value={qty} onChange={setQty} places={wholeOnly(line.unit_code) ? 0 : 4} unit={line.unit_code} />
      <label className="label mt-2" htmlFor="lc-reason">Reason (required)</label>
      <input id="lc-reason" className="input" value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />
    </ConfirmDialog>
  );
}

function OrderInvoices({ orderId }: { orderId: string }) {
  const [rows, setRows] = useState<InvoiceSummary[] | null>(null);
  useEffect(() => {
    api.invoices({ sales_order_id: orderId }).then((r) => setRows(r.items), () => setRows(null));
  }, [orderId]);
  if (!rows) return null;
  return (
    <>
      <h2 className="pt-2 font-semibold">Invoices</h2>
      {rows.length === 0 ? (
        <p className="text-sm text-ink-muted">None issued.</p>
      ) : (
        <ul className="divide-y divide-line text-sm">
          {rows.map((r) => (
            <li key={r.id} className="flex items-center justify-between py-2">
              <Link className={link} to={`/invoices/${r.id}`}>{r.invoice_no}</Link>
              <span className="num">{r.total} CAD</span>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
