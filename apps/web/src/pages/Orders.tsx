import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  api,
  ApiError,
  canInWarehouse,
  has,
  type Customer,
  type Order,
  type OrderLine,
  type OrderStatus,
  type OrderSummary,
  type InvoiceSummary,
  type UnitCode,
} from "../api";
import { useAuth } from "../auth";
import { qtyText, wholeOnly } from "../lib/inventory";
import { billable, blankLine, ensureRows, gridProblem, rowProblem, weightOf, type GridLine } from "../lib/lineGrid";
import { AMENDMENT_LABEL, lineAmount, ORDER_STATUS, ordersMessage, PICK_STATUS } from "../lib/orders";
import { rateText } from "../lib/pricingErrors";
import { useTrashChanges } from "../lib/trash";
import { DataGrid, type Column } from "../ui/DataGrid";
import { DeleteAction } from "../ui/DeleteAction";
import { figureColumns, useGridProducts, useLinePreview } from "../lib/gridData";
import { GridProblems, TotalsBar } from "../ui/gridParts";
import { Icon } from "../ui/Icon";
import { LineGrid, type GridColumn, type LineGridHandle } from "../ui/LineGrid";
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
  useTrashChanges(load);

  if (!session) return null;
  const columns: Column<OrderSummary>[] = [
    { key: "no", header: "Order", cell: (r) => <Link className={link} to={`/orders/${r.id}`}>{r.order_no}</Link>, sort: (r) => r.order_no },
    { key: "cust", header: "Customer", cell: (r) => r.customer_name, sort: (r) => r.customer_name },
    { key: "lines", header: "Lines", align: "right", cell: (r) => <span className="num">{r.line_count}</span> },
    { key: "for", header: "Wanted for", cell: (r) => (r.requested_for ? new Date(r.requested_for).toLocaleDateString() : "—") },
    { key: "st", header: "Status", cell: (r) => <OrderChip status={r.status} /> },
    { key: "at", header: "Created", cell: (r) => when(r.created_at), sort: (r) => r.created_at },
    // Only drafts can be deleted. The summary has no warehouse id, so the server makes the per-warehouse check.
    { key: "delete", header: "", align: "right", cell: (r) => (r.status === "DRAFT" ? <DeleteAction kind="sales_order" id={r.id} label={`Order ${r.order_no}`} compact /> : null) },
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

const MAX_ORDER_LINES = 200; // the order API takes up to 200 lines
const ORDER_FORM = "so-form"; // the page header's buttons submit this form
const UNIT_SHORT: Record<UnitCode, string> = { BOX: "boxes", PIECE: "pieces", LB: "lb", KG: "kg" };

/** New sales order: the same always-open entry grid as Create invoice › Direct. Saved as a
 * DRAFT (no prices kept); the rate and gross shown are today's, fixed only on Confirm. */
export function NewOrderPage() {
  return (
    <div className="space-y-5">
      <nav aria-label="Breadcrumb" className="text-sm">
        <Link className="text-brand-primary underline" to="/orders">
          Sales orders
        </Link>{" "}
        / New
      </nav>
      <PageHeader
        title="Create sales order"
        subtitle="Saved as a draft. Prices are fixed when you confirm."
        actions={
          <button type="submit" form={ORDER_FORM} className="btn-primary">
            Save draft
          </button>
        }
      />
      <OrderForm />
    </div>
  );
}

/** A draft's lines in the grid (the "as written" text rides along unseen and is kept). */
function draftRows(o: Order): GridLine[] {
  const rows = o.lines.map((l) => ({
    ...blankLine(),
    product_id: l.product_id,
    unit_code: l.unit_code,
    qty: qtyText(l.ordered_qty),
    text: l.product_name,
    note: l.source_line_text,
    weight: l.weight_lb ? qtyText(l.weight_lb) : "",
  }));
  return ensureRows(rows, MAX_ORDER_LINES);
}

const day = (iso: string | null) => (iso ? iso.slice(0, 10) : "");

/**
 * The order entry form, laid out like Create invoice: New sales order, and a draft being
 * checked and confirmed (a scanned sheet, a typed or online order). Boxes are sold per lb: LB is
 * what the line weighs (the sheet's WEIGHT), and Rate × LB makes the line's gross.
 */
function OrderForm({ draft, onChanged }: { draft?: Order; onChanged?: () => Promise<void> }) {
  const { session } = useAuth();
  const nav = useNavigate();
  const { products } = useGridProducts();
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [customer, setCustomer] = useState(draft?.customer_id ?? "");
  const [wh, setWh] = useState(draft?.warehouse_id ?? "");
  const [wanted, setWanted] = useState(day(draft?.requested_for ?? null));
  const [sourceText, setSourceText] = useState("");
  const [note, setNote] = useState(draft?.note ?? "");
  const [lines, setLines] = useState<GridLine[]>(() => (draft ? draftRows(draft) : ensureRows([], MAX_ORDER_LINES)));
  const [activeRow, setActiveRow] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"save" | "confirm" | null>(null);
  const grid = useRef<LineGridHandle>(null);
  const saveBtn = useRef<HTMLButtonElement>(null);
  const sending = useRef(false);

  useEffect(() => {
    api.customers({ limit: 200 }).then((c) => setCustomers(c.filter((x) => x.active || x.id === draft?.customer_id)), () => setCustomers([]));
    api.warehouses().then((w) => {
      const ok = w.filter((x) => x.active && session && canInWarehouse(session, "sales_order.manage", x.id));
      setWh((cur) => (ok.some((x) => x.id === cur) ? cur : (ok[0]?.id ?? "")));
    }, () => setWh(""));
  }, [session, draft?.customer_id]);
  const lp = useLinePreview(customer, wh, lines);

  const serverProblem = (l: GridLine): string | null => {
    const e = lp.entry(l);
    if (!e) return null;
    if (e.problem && e.problem.code !== "DUPLICATE_LINE") return e.problem.message.replace(/^Line \d+: /, "");
    if (!draft && l.qty.trim() && !e.stock_ok) return `Only ${qtyText(e.available_qty)} ${UNIT_SHORT[e.unit_code]} in stock now (the order can still be saved).`;
    return null;
  };
  const problemFor = (l: GridLine, i: number) => (i === activeRow && !l.product_id ? null : (rowProblem(l, i, lines) ?? serverProblem(l)));
  const problems = lines.flatMap((l, row) => {
    const text = problemFor(l, row);
    return text ? [{ row, text }] : [];
  });
  const toGrid = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key !== "Enter" || e.shiftKey) return;
    e.preventDefault();
    grid.current?.focusFirstEmpty();
  };

  const used = billable(lines);
  const body = () => ({
    customer_id: customer,
    warehouse_id: wh,
    note: note.trim(),
    requested_for: wanted ? new Date(`${wanted}T12:00:00`).toISOString() : null,
    lines: used.map((l) => {
      const w = weightOf(l);
      return { product_id: l.product_id, unit_code: l.unit_code as UnitCode, qty: l.qty.trim(), source_line_text: l.note.trim(), weight_lb: w && w !== "bad" ? w : null };
    }),
  });
  // the draft as last saved (and its version): unchanged means confirm without saving first
  const saved = useRef({ sig: draft ? JSON.stringify(body()) : "", version: draft?.version ?? 0 });

  const fail = (err: unknown) => {
    // "Line N" from the server counts saved lines; say the grid row instead.
    const msg = ordersMessage(err);
    const n = err instanceof ApiError && typeof err.extra.line === "number" ? err.extra.line : null;
    const row = n ? lines.findIndex((l) => l.key === used[n - 1]?.key) : -1;
    if (row !== -1) grid.current?.focusRow(row);
    setError(row === -1 ? msg : msg.replace(/^Line \d+:/, `Line ${row + 1}:`));
  };

  const submit = async (mode: "save" | "confirm") => {
    if (sending.current) return; // the header and the bar both submit: one request at a time
    setError(null);
    const problem = !wh ? "Your account can't create orders." : !customer ? "Choose the customer." : gridProblem(lines);
    if (problem) return setError(problem);
    setBusy(mode);
    sending.current = true;
    try {
      if (!draft) {
        const o = await api.createOrder({ ...body(), source_text: sourceText });
        return nav(`/orders/${o.id}`);
      }
      const sig = JSON.stringify(body());
      if (sig !== saved.current.sig || mode === "save") {
        const o = await api.updateOrder(draft.id, { ...body(), version: saved.current.version });
        saved.current = { sig, version: o.version };
      }
      if (mode === "confirm") await api.confirmOrder(draft.id, saved.current.version);
      await onChanged?.();
    } catch (err) {
      fail(err);
      if (draft && err instanceof ApiError && err.code === "VERSION_CONFLICT") await onChanged?.();
    } finally {
      sending.current = false;
      setBusy(null);
    }
  };

  const columns: GridColumn[] = [
    { key: "sn", header: "S/N", kind: "sn", width: "3rem" },
    { key: "item", header: "Item", kind: "item", enter: true, label: "Item" },
    { key: "qty", header: "Qty", kind: "qty", enter: true, align: "right", width: "5rem", label: "Quantity" },
    { key: "unit", header: "Unit", kind: "unit", width: "5.25rem", label: "Unit" },
    ...(draft ? [] : [{ key: "written", header: "As written", kind: "note", width: "12rem", label: "As written", title: "The line as the customer wrote or said it (optional)" } as GridColumn]),
    ...figureColumns(lp.entry, undefined, { stock: !draft, weighable: true, rateTitle: "Today's price for this customer. It is fixed when the order is confirmed." }),
  ];
  const cust = customers.find((c) => c.id === customer);

  return (
    <form id={ORDER_FORM} className="card space-y-5 p-6" onSubmit={(e: FormEvent) => {
        e.preventDefault();
        const by = (e.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
        void submit(draft && by?.value !== "save" ? "confirm" : "save");
      }} aria-label={draft ? `Order ${draft.order_no}` : "New sales order"}>
      <div className="grid gap-x-5 gap-y-4 md:grid-cols-12">
        <div className="md:col-span-8">
          <label className="label" htmlFor="so-cust">
            Customer
          </label>
          <select id="so-cust" className="input" value={customer} onChange={(e) => setCustomer(e.target.value)} onKeyDown={toGrid}>
            <option value="">Choose…</option>
            {customers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.external_ref ? `${c.external_ref} — ` : ""}
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <div className="md:col-span-4">
          <label className="label" htmlFor="so-wanted">
            Wanted for (optional)
          </label>
          <input id="so-wanted" type="date" className="input" value={wanted} onChange={(e) => setWanted(e.target.value)} onKeyDown={toGrid} />
        </div>
        <div className="md:col-span-3">
          <span className="label">Payment terms</span>
          <p className="input bg-subtle text-ink-muted">{cust?.payment_terms_days ? `${cust.payment_terms_days} days` : "—"}</p>
        </div>
        <div className="md:col-span-9">
          <label className="label" htmlFor="so-note">
            Note (optional)
          </label>
          <input id="so-note" className="input" maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={toGrid} />
        </div>
        {!draft && (
          <div className="md:col-span-12">
            <label className="label" htmlFor="so-src">
              Original request (as the customer wrote or said it)
            </label>
            <textarea id="so-src" className="input min-h-16" value={sourceText} onChange={(e) => setSourceText(e.target.value)} maxLength={10000} />
          </div>
        )}
      </div>
      <div className="space-y-2">
        <LineGrid
          label="Order lines"
          columns={columns}
          lines={lines}
          setLines={(fn) => {
            setNotice(null);
            setLines(fn);
          }}
          products={products}
          maxRows={MAX_ORDER_LINES}
          problem={problemFor}
          handle={grid}
          onActiveRow={setActiveRow}
          onDone={() => saveBtn.current?.focus()}
          onPasted={(n, bad) => setNotice(`Pasted ${n} ${n === 1 ? "row" : "rows"}.${bad ? ` ${bad} ${bad === 1 ? "item doesn't" : "items don't"} match a product — fix the lines marked !` : ""}`)}
          thumbs
          addRows={10}
        />
        <p className="flex items-start gap-2 text-xs text-ink-muted">
          <Icon name="info" size={14} className="mt-0.5 shrink-0" />
          <span>Sold per lb: LB is what the line weighs. Enter moves Item → Qty → next line, Tab moves across; paste rows from Excel straight into the grid.</span>
        </p>
        {notice && (
          <p className="text-sm text-ink" role="status">
            {notice}
          </p>
        )}
        <GridProblems items={problems} />
      </div>
      {error && (
        <p className="alert-danger" role="alert">
          {error}
        </p>
      )}
      <TotalsBar
        subtotal={lp.data?.subtotal ?? null}
        tax={lp.data?.tax_amount ?? null}
        taxLabel="Tax (not assessed)"
        total={lp.data?.total ?? null}
        checked={false}
        status={!customer ? "Choose the customer to see prices." : used.length ? "Today's prices — fixed when the order is confirmed." : "Type or paste products to start."}
      >
        {draft ? (
          <>
            <button type="button" className="btn-ghost" disabled={!!busy} onClick={() => void submit("save")}>
              {busy === "save" ? "Saving…" : "Save draft"}
            </button>
            <button ref={saveBtn} className="btn-primary" disabled={!!busy}>
              {busy === "confirm" ? "Confirming…" : "Confirm order"}
            </button>
          </>
        ) : (
          <>
            <Link className="btn-ghost" to="/orders">
              Cancel
            </Link>
            <button ref={saveBtn} className="btn-primary" disabled={!!busy}>
              {busy ? "Saving…" : "Save draft"}
            </button>
          </>
        )}
      </TotalsBar>
    </form>
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
  if (o.status === "DRAFT" && manage)
    return (
      <div className="space-y-5">
        <nav aria-label="Breadcrumb" className="text-sm">
          <Link className="text-brand-primary underline" to="/orders">
            Sales orders
          </Link>{" "}
          / {o.order_no}
        </nav>
        <PageHeader
          title={o.order_no}
          subtitle={
            <>
              <OrderChip status={o.status} />
              {o.source !== "MANUAL" && <span className="ml-2 text-ink-muted">{o.source === "SCAN" ? "from a scanned sheet" : "from the website"}</span>}
            </>
          }
          actions={
            <>
              <DeleteAction kind="sales_order" id={o.id} label={`Order ${o.order_no}`} warehouseId={o.warehouse_id} onDone={() => nav("/orders")} />
              <button className="btn-ghost" onClick={() => setDialog("cancel")}>
                Cancel draft
              </button>
              <button type="submit" form={ORDER_FORM} name="mode" value="save" className="btn-ghost">
                Save draft
              </button>
              <button type="submit" form={ORDER_FORM} className="btn-primary">
                Confirm order
              </button>
            </>
          }
        />
        {error && (
          <p className="alert-danger" role="alert">
            {error}
          </p>
        )}
        <OrderForm key={o.version} draft={o} onChanged={load} />
        <ConfirmDialog open={dialog === "cancel"} title="Cancel this draft?" confirmLabel="Cancel draft" danger onClose={() => setDialog("none")} onConfirm={() => void run(() => api.cancelOrder(o.id, o.version, "Draft cancelled"))}>
          It stays on record as cancelled.
        </ConfirmDialog>
      </div>
    );
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
            {o.status === "DRAFT" && <DeleteAction kind="sales_order" id={o.id} label={`Order ${o.order_no}`} warehouseId={o.warehouse_id} onDone={() => nav("/orders")} />}
            {manage && o.status === "DRAFT" && <button className="btn-ghost" onClick={() => setDialog("cancel")}>Cancel draft</button>}
            {close && o.status === "CONFIRMED" && o.attempts.every((a) => a.status === "ABORTED") && (
              <button className="btn-ghost" onClick={() => setDialog("cancel")}>Cancel order</button>
            )}
            {close && ["CONFIRMED", "PARTIALLY_FULFILLED"].includes(o.status) && pending && !awaiting && o.attempts.some((a) => a.status !== "ABORTED") && (
              <button className="btn-ghost" onClick={() => setDialog("close")}>Close with shortage</button>
            )}
            {o.open_attempt_id && <Link className="btn-ghost" to={`/packing/${o.open_attempt_id}`}>Open packing sheet</Link>}
            {manage && open && pending && !o.open_attempt_id && (
              <button className="btn-primary" onClick={() => setDialog("pick")}>Print packing sheet</button>
            )}
            {manage && o.status === "DRAFT" && <button className="btn-primary" onClick={() => setDialog("confirm")}>Confirm order</button>}
          </>
        }
      />
      {error && <p className="alert-danger" role="alert">{error}</p>}
      {o.has_backorder && (
        <p className="alert-warning" role="status">Backorder: some items were short on an earlier packing sheet and are still pending for this customer.</p>
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
          <h2 id="pk-h" className="font-semibold">Packing sheets</h2>
          {o.attempts.length === 0 ? (
            <p className="text-sm text-ink-muted">None yet.</p>
          ) : (
            <ul className="divide-y divide-line text-sm">
              {o.attempts.map((a) => (
                <li key={a.id} className="flex items-center justify-between py-2">
                  <Link className={link} to={`/packing/${a.id}`}>{a.pick_code}</Link>
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
      <ConfirmDialog open={dialog === "pick"} title="Print a packing sheet?" confirmLabel="Create sheet" onClose={() => setDialog("none")} onConfirm={() => void run(async () => { const p = await api.openPick(o.id, o.version); nav(`/packing/${p.id}`); })}>
        The sheet lists every pending quantity. The warehouse records what was actually packed.
      </ConfirmDialog>
      <ReasonDialog
        open={dialog === "cancel" || dialog === "close"}
        title={dialog === "close" ? "Close with shortage?" : "Cancel this order?"}
        body={dialog === "close" ? "All pending quantities are cancelled. Supplied quantities are unaffected." : "Nothing has been packed. The order stays on record as cancelled."}
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
