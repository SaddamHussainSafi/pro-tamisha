import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { flushSync } from "react-dom";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, ApiError, canInWarehouse, type Customer, type DirectInvoiceIn, type DirectPreview, type PickSummary, type UnitCode, type Warehouse } from "../api";
import { termsLabel } from "../lib/customers";
import { useAuth } from "../auth";
import { cad, dec } from "../lib/decimal";
import { productsFromInvoice } from "../lib/directInvoice";
import { qtyText } from "../lib/inventory";
import { invoiceMessage } from "../lib/invoices";
import { billable, blankLine, ensureRows, gridProblem, isFilled, nextKey, pickProduct, rowProblem, untouched, withPrefill, type GridLine } from "../lib/lineGrid";
import { DataGrid, type Column } from "../ui/DataGrid";
import { batchColumn, figureColumns, useGridProducts, useLinePreview } from "../lib/gridData";
import { GridProblems, TotalsBar } from "../ui/gridParts";
import { Icon } from "../ui/Icon";
import { LineGrid, type GridColumn, type LineGridHandle } from "../ui/LineGrid";
import { CardTitle, ConfirmDialog, EmptyState, LoadingRows, PageHeader } from "../ui/primitives";

const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
const tabs = [
  { key: "sheet", label: "From a picking sheet" },
  { key: "direct", label: "Direct invoice (no picking sheet)" },
] as const;

/** Invoices › Create invoice: bill a verified picking sheet, or a small sale with no sheet. */
export function NewInvoicePage() {
  const [params] = useSearchParams();
  const tab = params.get("from") === "direct" ? "direct" : "sheet";
  return (
    <div className="space-y-5">
      <nav aria-label="Breadcrumb" className="text-sm">
        <Link className="text-brand-primary underline" to="/invoices">
          Invoices
        </Link>{" "}
        / New
      </nav>
      <PageHeader title="Create invoice" subtitle="Bill goods that were picked and checked, or a small sale handed over without a picking sheet." />
      <nav aria-label="How to invoice" className="flex flex-wrap gap-1.5">
        {tabs.map((t) => (
          <Link
            key={t.key}
            to={t.key === "sheet" ? "/invoices/new" : "/invoices/new?from=direct"}
            aria-current={tab === t.key ? "page" : undefined}
            className={`chip px-4 py-2 text-sm ${tab === t.key ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:bg-subtle"}`}
          >
            {t.label}
          </Link>
        ))}
      </nav>
      {tab === "sheet" ? <FromSheet /> : <Direct />}
    </div>
  );
}

function FromSheet() {
  const [rows, setRows] = useState<PickSummary[] | null>(null);
  useEffect(() => {
    api.picks({ status: "VERIFIED" }).then(setRows, () => setRows([]));
  }, []);
  const cols: Column<PickSummary>[] = [
    { key: "c", header: "Customer", cell: (p) => p.customer_name, sort: (p) => p.customer_name },
    { key: "s", header: "Picking sheet", cell: (p) => <span className="whitespace-nowrap">{p.pick_code}</span> },
    { key: "l", header: "Lines", align: "right", cell: (p) => <span className="num">{p.line_count}</span> },
    { key: "v", header: "Checked", cell: (p) => (p.verified_at ? <span className="whitespace-nowrap">{day(p.verified_at)}</span> : "—") },
    {
      key: "a",
      header: "",
      cell: (p) => (
        <Link className="btn-primary px-4 py-2 text-xs" to={`/picking/${p.id}/invoice`} aria-label={`Prepare invoice for ${p.pick_code}`}>
          Prepare invoice
        </Link>
      ),
    },
  ];
  return (
    <section className="card space-y-4 p-6" aria-labelledby="ni-sheets">
      <CardTitle id="ni-sheets" title="Picking sheets ready to invoice" />
      <p className="text-sm text-ink-muted">Checked sheets that are not invoiced yet. The invoice editor lets you enter case weights before issuing.</p>
      {rows === null ? (
        <LoadingRows cols={4} />
      ) : (
        <DataGrid
          rows={rows}
          columns={cols}
          rowKey={(p) => p.id}
          caption="Picking sheets ready to invoice"
          empty={<EmptyState title="Nothing is waiting to be invoiced" hint="For a small sale without a picking sheet, use Direct invoice." />}
        />
      )}
    </section>
  );
}

const MAX_LINES = 100; // a direct invoice takes up to 100 lines
const draftKey = (employee: string) => `pt.direct-invoice-draft.v1:${employee}`;
type Draft = { at: string; customer: string; salesperson: string; po: string; note: string; wh: string; lines: GridLine[] };
const UNIT_SHORT: Record<UnitCode, string> = { BOX: "boxes", PIECE: "pieces", LB: "lb", KG: "kg" };
const clock = (iso: string) => new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });

/** Direct invoice: header prefilled from the customer, then a spreadsheet grid that is always
 * open. Live figures come from the server line preview; "Check prices and stock" runs the
 * issue preview (the figures the invoice will carry) before Issue invoice. */
function Direct() {
  const { session } = useAuth();
  const navigate = useNavigate();
  const { products } = useGridProducts();
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [wh, setWh] = useState("");
  const [customer, setCustomer] = useState("");
  const [lines, setLines] = useState<GridLine[]>(() => ensureRows([], MAX_LINES));
  const [note, setNote] = useState("");
  const [salesperson, setSalesperson] = useState("");
  const [po, setPo] = useState("");
  const [lastInvoice, setLastInvoice] = useState<string | null>(null);
  const prefillFor = useRef("");
  const linesNow = useRef(lines);
  linesNow.current = lines;
  const [pv, setPv] = useState<{ sig: string; keys: number[]; data: DirectPreview } | null>(null);
  const [handed, setHanded] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [draftAt, setDraftAt] = useState<string | null>(null);
  const [activeRow, setActiveRow] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const grid = useRef<LineGridHandle>(null);
  const checkBtn = useRef<HTMLButtonElement>(null);
  const handedBox = useRef<HTMLInputElement>(null);
  const storeKey = session ? draftKey(session.employee.id) : "";

  useEffect(() => {
    api.customers({ active: true, limit: 500 }).then(setCustomers, () => setCustomers([]));
    api.invoiceSettings().then(
      (st) => setSalesperson((cur) => cur || st.default_salesperson),
      () => undefined,
    );
    api.warehouses().then((w) => {
      const ok = w.filter((x) => x.active && session && (canInWarehouse(session, "invoice.issue", x.id) || canInWarehouse(session, "invoice.prepare", x.id)));
      setWarehouses(ok);
      setWh((cur) => (ok.some((x) => x.id === cur) ? cur : (ok[0]?.id ?? "")));
    }, () => setWarehouses([]));
  }, [session]);

  // A draft saved on this computer comes back (per user). Storage may be blocked: start empty.
  useEffect(() => {
    if (!storeKey) return;
    try {
      const raw = localStorage.getItem(storeKey);
      if (!raw) return;
      const d = JSON.parse(raw) as Draft;
      prefillFor.current = d.customer;
      setCustomer(d.customer);
      setSalesperson(d.salesperson);
      setPo(d.po);
      setNote(d.note);
      if (d.wh) setWh(d.wh);
      setLines(ensureRows(d.lines.map((l) => ({ ...l, key: nextKey() })), MAX_LINES));
      setDraftAt(d.at);
    } catch {
      /* no draft */
    }
  }, [storeKey]);

  const lp = useLinePreview(customer, wh, lines);
  if (!session) return null;
  const canIssue = !!wh && canInWarehouse(session, "invoice.issue", wh);
  const used = billable(lines);
  const cust = customers.find((c) => c.id === customer);
  const body = (): DirectInvoiceIn => ({
    customer_id: customer,
    warehouse_id: wh,
    salesperson: salesperson.trim(),
    customer_po: po.trim(),
    note: note.trim(),
    lines: used.map((l) => ({ product_id: l.product_id, unit_code: l.unit_code as UnitCode, qty: l.qty.trim(), ...(l.lot_id ? { lot_id: l.lot_id } : {}) })),
  });
  const sig = JSON.stringify(body());
  const checked = pv && pv.sig === sig ? pv : null; // any edit after checking needs a new check
  const checkedFor = (l: GridLine) => {
    const i = checked ? checked.keys.indexOf(l.key) : -1;
    const ln = checked?.data.lines[i];
    const st = checked?.data.stock[i];
    return ln && st ? { rate: ln.unit_rate, amount: ln.line_amount, weight: null, ok: st.ok, available: st.available_qty, lots: st.lots } : undefined;
  };
  const serverProblem = (l: GridLine): string | null => {
    const e = lp.entry(l);
    if (!e || (l.prefilled && !l.qty.trim())) return null;
    if (e.problem && e.problem.code !== "DUPLICATE_LINE") return e.problem.message.replace(/^Line \d+: /, "");
    if (l.qty.trim() && !e.stock_ok) return `Only ${qtyText(e.available_qty)} ${UNIT_SHORT[e.unit_code]} in stock${l.lot_id ? " in this batch" : ""}.`;
    return null;
  };
  // The row being typed in: no "no product matches" until the cursor leaves it.
  const problemFor = (l: GridLine, i: number) => (i === activeRow && !l.product_id ? null : (rowProblem(l, i, lines) ?? serverProblem(l)));
  const problems = lines.flatMap((l, row) => {
    const text = problemFor(l, row);
    return text ? [{ row, text }] : [];
  });

  const edit = (fn: (ls: GridLine[]) => GridLine[]) => {
    setNotice(null);
    setLines(fn);
  };
  /** Start the grid from the customer's last invoice, so staff only enter quantities.
   * Only while the grid is untouched (no quantity typed, nothing added by hand). */
  async function chooseCustomer(id: string) {
    setCustomer(id);
    prefillFor.current = id;
    if (!untouched(linesNow.current)) return;
    setLastInvoice(null);
    if (!id) return setLines((cur) => withPrefill(cur, [], MAX_LINES));
    try {
      const page = await api.invoices({ customer_id: id, limit: 1 });
      const last = page.items[0];
      const rows = last ? productsFromInvoice(await api.invoice(last.id)) : [];
      if (prefillFor.current !== id) return; // another customer was chosen meanwhile
      const pre = rows.flatMap((r) => {
        const p = products.find((x) => x.id === r.product_id);
        if (!p) return [];
        const l = pickProduct(blankLine(), p);
        return [{ ...l, unit_code: p.units.includes(r.unit_code) ? r.unit_code : l.unit_code, prefilled: true }];
      });
      setLines((cur) => withPrefill(cur, pre, MAX_LINES)); // keeps anything typed meanwhile
      setLastInvoice(last && pre.length ? last.invoice_no : null);
    } catch {
      if (prefillFor.current === id) setLines((cur) => withPrefill(cur, [], MAX_LINES)); // no invoice access
    }
  }

  /** "Line 2: ..." from the server counts billed lines; say the grid row instead. */
  function lineMessage(err: unknown, keys: number[]): string {
    const msg = invoiceMessage(err);
    const n = err instanceof ApiError && typeof err.extra.line === "number" ? err.extra.line : null;
    const row = n ? lines.findIndex((l) => l.key === keys[n - 1]) : -1;
    if (row === -1) return msg;
    grid.current?.focusRow(row);
    return msg.replace(/^Line \d+:/, `Line ${row + 1}:`);
  }

  async function check() {
    setError(null);
    const problem = !wh ? "You can't invoice in any warehouse." : !customer ? "Choose the customer." : gridProblem(lines);
    if (problem) return setError(problem);
    setBusy(true);
    const b = body();
    const keys = used.map((l) => l.key);
    try {
      const data = await api.previewDirectInvoice(b);
      flushSync(() => {
        setPv({ sig: JSON.stringify(b), keys, data });
        setHanded(false);
      });
      handedBox.current?.focus(); // next: tick the hand-over, Tab, Enter to issue
    } catch (err) {
      setError(lineMessage(err, keys));
    } finally {
      setBusy(false);
    }
  }

  async function issue() {
    setConfirm(false);
    setError(null);
    setBusy(true);
    try {
      const inv = await api.issueDirectInvoice({ ...body(), dispatch_confirmed: handed });
      try {
        localStorage.removeItem(storeKey);
      } catch {
        /* storage blocked */
      }
      navigate(`/invoices/${inv.id}`);
    } catch (err) {
      setError(lineMessage(err, checked?.keys ?? []));
      setPv(null);
    } finally {
      setBusy(false);
    }
  }

  function saveDraft() {
    const at = new Date().toISOString();
    try {
      localStorage.setItem(storeKey, JSON.stringify({ at, customer, salesperson, po, note, wh, lines: lines.filter(isFilled) } satisfies Draft));
      setDraftAt(at);
      setNotice(`Draft saved on this computer at ${clock(at)}. It opens here again until the invoice is issued.`);
    } catch {
      setNotice("This browser can't keep a draft (site storage is blocked).");
    }
  }
  function discardDraft() {
    try {
      localStorage.removeItem(storeKey);
    } catch {
      /* storage blocked */
    }
    setDraftAt(null);
    setCustomer("");
    prefillFor.current = "";
    setPo("");
    setNote("");
    setLastInvoice(null);
    setLines(ensureRows([], MAX_LINES));
    setNotice("Draft discarded.");
  }
  const toGrid = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key !== "Enter" || e.shiftKey) return;
    e.preventDefault();
    grid.current?.focusFirstEmpty();
  };

  const columns: GridColumn[] = [
    { key: "sn", header: "S/N", kind: "sn", width: "3rem" },
    { key: "item", header: "Item", kind: "item", enter: true, label: "Item" },
    { key: "qty", header: "Qty", kind: "qty", enter: true, align: "right", width: "5rem", label: "Quantity" },
    { key: "unit", header: "Unit", kind: "unit", width: "5.25rem", label: "Unit" },
    batchColumn(lp.entry, (l) => checkedFor(l)?.lots),
  ];
  const figures = figureColumns(lp.entry, checkedFor, { stock: false });
  columns.push({
    key: "so",
    header: "SO Link",
    kind: "display",
    width: "5rem",
    title: "A direct invoice opens its own sales order when it is issued",
    render: (l) => (l.product_id && !(l.prefilled && !l.qty.trim()) ? <span className="text-ink-muted">New</span> : null),
  }, ...figures);

  const totals = checked?.data ?? lp.data;
  const status = checked
    ? checked.data.stock_ok
      ? "Checked against today's prices and stock."
      : "Checked — not enough stock for every line."
    : !customer
      ? "Choose the customer to see prices."
      : lp.failed
        ? `Live prices unavailable: ${lp.failed}`
        : used.length
          ? "Provisional — Check prices and stock before issuing."
          : "Type or paste products to start.";

  return (
    <>
      <section className="card space-y-5 p-6" aria-labelledby="ni-direct">
        <div className="space-y-1">
          <CardTitle id="ni-direct" title="Direct invoice" />
          <p className="text-sm text-ink-muted">For small sales handed over without a picking sheet. Prices come from the price book; stock is taken from the oldest expiry first.</p>
        </div>
        {draftAt && (
          <p className="alert-success flex flex-wrap items-center justify-between gap-2" role="status">
            {`Draft from ${new Date(draftAt).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })} is open.`}
            <button type="button" className="btn-ghost px-3 py-1 text-xs" onClick={discardDraft}>
              Discard draft
            </button>
          </p>
        )}
        <div className="grid gap-x-5 gap-y-4 md:grid-cols-12">
          <div className="md:col-span-8">
            <label className="label" htmlFor="ni-customer">
              Customer
            </label>
            <select id="ni-customer" className="input" value={customer} onChange={(e) => void chooseCustomer(e.target.value)} onKeyDown={toGrid}>
              <option value="">Choose…</option>
              {customers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                  {c.external_ref ? ` (${c.external_ref})` : ""}
                </option>
              ))}
            </select>
          </div>
          <ReadOnly className="md:col-span-4" id="ni-date" label="Invoice date" value={today()} title="Invoices are dated the day they are issued" />
          <div className="md:col-span-8">
            <label className="label" htmlFor="ni-salesperson">
              Salesperson
            </label>
            <input id="ni-salesperson" className="input" maxLength={100} value={salesperson} onChange={(e) => setSalesperson(e.target.value)} onKeyDown={toGrid} />
          </div>
          <div className="md:col-span-4">
            <label className="label" htmlFor="ni-po">
              Customer PO No. (optional)
            </label>
            <input id="ni-po" className="input" maxLength={64} value={po} onChange={(e) => setPo(e.target.value)} onKeyDown={toGrid} />
          </div>
          <div className="md:col-span-4">
            <label className="label" htmlFor="ni-wh">
              Warehouse
            </label>
            <select
              id="ni-wh"
              className="input"
              value={wh}
              onKeyDown={toGrid}
              onChange={(e) => {
                setWh(e.target.value);
                edit((ls) => ls.map((l) => ({ ...l, lot_id: "" }))); // batches belong to a warehouse
              }}
            >
              {!warehouses.length && <option value="">None you can invoice in</option>}
              {warehouses.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </div>
          <ReadOnly className="md:col-span-2" id="ni-tax" label="Tax code" value="Not assessed" title="Tax rules are waiting for the accountant's sign-off (D-03); tax is 0.00 until then" />
          <ReadOnly className="md:col-span-3" id="ni-terms" label="Payment terms" value={cust ? termsLabel(cust.payment_terms_days) : "—"} title="From the customer record" />
          <ReadOnly className="md:col-span-3" id="ni-cur" label="Currency" value="CAD" title="Invoices are in Canadian dollars" />
          <div className="md:col-span-12">
            <label className="label" htmlFor="ni-note">
              Narration / Note (optional)
            </label>
            <input id="ni-note" className="input" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={toGrid} />
          </div>
        </div>
        {cust && <CustomerOnInvoice c={cust} />}
        <div className="space-y-2">
          {lastInvoice && lines.some((l) => l.prefilled) && (
            <p className="text-sm text-ink-muted" role="status">
              {`Products from ${cust?.name ?? "this customer"}'s last invoice (${lastInvoice}) are listed. Enter the quantities; products left empty are not billed.`}
            </p>
          )}
          <LineGrid
            label="Invoice lines"
            columns={columns}
            lines={lines}
            setLines={edit}
            products={products}
            maxRows={MAX_LINES}
            problem={problemFor}
            handle={grid}
            onActiveRow={setActiveRow}
            onDone={() => checkBtn.current?.focus()}
            onPasted={(n, bad) => setNotice(`Pasted ${n} ${n === 1 ? "row" : "rows"}.${bad ? ` ${bad} ${bad === 1 ? "item doesn't" : "items don't"} match a product — fix the lines marked !` : ""}`)}
          />
          <p className="flex items-start gap-2 text-xs text-ink-muted">
            <Icon name="info" size={14} className="mt-0.5 shrink-0" />
            <span>Rows are active by default — start typing to add items. Enter moves Item → Qty → next line, Tab moves across, arrows move around; paste rows from Excel straight into the grid.</span>
          </p>
          {notice && (
            <p className="text-sm text-ink" role="status">
              {notice}
            </p>
          )}
          <GridProblems items={problems} />
        </div>
        {error && (
          <p role="alert" className="alert-danger">
            {error}
          </p>
        )}
        {checked && !checked.data.stock_ok && (
          <p role="alert" className="alert-warning">
            Not enough stock for every line. Lower the quantities, choose another batch, or count the stock first.
          </p>
        )}
        <TotalsBar
          left={
            <button type="button" className="btn-ghost" onClick={saveDraft} disabled={!lines.some(isFilled) && !customer}>
              Save draft
            </button>
          }
          subtotal={totals?.subtotal ?? null}
          tax={totals?.tax_amount ?? null}
          taxLabel="Tax (not assessed)"
          total={totals?.total ?? null}
          checked={!!checked}
          status={status}
        >
          {checked && checked.data.stock_ok && canIssue ? (
            <>
              <label className="flex items-center gap-2.5 text-sm">
                <input ref={handedBox} type="checkbox" className="size-4 accent-brand-primary" checked={handed} onChange={(e) => setHanded(e.target.checked)} />
                Goods handed over to the customer
              </label>
              <button type="button" className="btn-accent" disabled={busy || !handed} onClick={() => setConfirm(true)}>
                Issue invoice
              </button>
            </>
          ) : (
            <>
              {checked && !canIssue && <span className="text-sm text-ink-muted">A manager issues the invoice. You can check prices and stock here.</span>}
              <button ref={checkBtn} type="button" className="btn-primary" disabled={busy} onClick={() => void check()}>
                {busy ? "Checking…" : "Check prices and stock"}
              </button>
            </>
          )}
        </TotalsBar>
      </section>
      <ConfirmDialog open={confirm} title="Issue this invoice?" confirmLabel="Issue invoice" onConfirm={() => void issue()} onClose={() => setConfirm(false)}>
        <p>
          It gets its number now, the stock leaves the warehouse and the invoice is emailed if the customer has an email. Total {checked ? cad(dec(checked.data.total)) : ""}. Issued invoices can't be edited.
        </p>
      </ConfirmDialog>
    </>
  );
}

function ReadOnly({ id, label, value, title, className = "" }: { id: string; label: string; value: string; title?: string; className?: string }) {
  return (
    <div className={className}>
      <span className="label" id={`${id}-label`}>
        {label}
      </span>
      <p className="input bg-subtle" aria-labelledby={`${id}-label`} title={title}>
        {value}
      </p>
    </div>
  );
}

const today = () => new Date().toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });

/** What the invoice prints from the customer record (Bill To, Ship To, Terms, Vendor #), on
 * one compact strip so the grid stays near the top. */
function CustomerOnInvoice({ c }: { c: Customer }) {
  const one = (s: string) => s.split(/\n+/).map((x) => x.trim()).filter(Boolean).join(", ");
  const bill = c.bill_to_address ?? "";
  const ship = [c.ship_to_name ?? c.name, c.ship_to_address ?? bill].filter(Boolean).join("\n");
  const items = [
    { label: "Bill to", value: one([c.name, bill].filter(Boolean).join("\n")) },
    { label: "Ship to", value: one(ship) },
    { label: "Terms", value: termsLabel(c.payment_terms_days) },
    { label: "Vendor #", value: c.vendor_no ?? "—" },
  ];
  return (
    <div className="rounded-[var(--radius-ctl)] border border-line bg-subtle px-4 py-2.5" aria-label="From the customer record">
      <dl className="grid gap-x-6 gap-y-1 text-xs sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1.4fr)_auto_auto]">
        {items.map((x) => (
          <div key={x.label} className="flex min-w-0 gap-1.5">
            <dt className="shrink-0 text-ink-muted">{x.label}</dt>
            <dd className="truncate" title={x.value}>
              {x.value}
            </dd>
          </div>
        ))}
      </dl>
      {!bill && (
        <p className="mt-1 text-xs text-ink-muted">
          No address on file.{" "}
          <Link className="text-brand-primary underline" to={`/customers/${c.id}`}>
            Add the bill-to address
          </Link>{" "}
          so it prints on the invoice.
        </p>
      )}
    </div>
  );
}
