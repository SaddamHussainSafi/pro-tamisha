import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, canInWarehouse, type Customer, type DirectPreview, type PickSummary, type Product, type ProductSummary, type UnitCode } from "../api";
import { termsLabel } from "../lib/customers";
import { useAuth } from "../auth";
import { cad, dec, qty } from "../lib/decimal";
import { billable, formProblem, productsFromInvoice, unitsFor, untouched, withPrefill, type DraftLine } from "../lib/directInvoice";
import { invoiceMessage } from "../lib/invoices";
import { DataGrid, type Column } from "../ui/DataGrid";
import { CardTitle, ConfirmDialog, EmptyState, LoadingRows, PageHeader, StatusChip } from "../ui/primitives";

const UNIT: Record<UnitCode, string> = { BOX: "boxes", PIECE: "pieces", LB: "lb", KG: "kg" };
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

let nextKey = 1;
const blank = (): DraftLine => ({ key: nextKey++, product_id: "", unit_code: "", qty: "" });

function Direct() {
  const { session } = useAuth();
  const navigate = useNavigate();
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [details, setDetails] = useState<Record<string, Product>>({});
  const [wh, setWh] = useState("");
  const [customer, setCustomer] = useState("");
  const [lines, setLines] = useState<DraftLine[]>([blank()]);
  const [note, setNote] = useState("");
  const [salesperson, setSalesperson] = useState("");
  const [po, setPo] = useState("");
  const [lastInvoice, setLastInvoice] = useState<string | null>(null);
  const prefillFor = useRef("");
  const [pv, setPv] = useState<DirectPreview | null>(null);
  const [handed, setHanded] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.customers({ active: true, limit: 500 }).then(setCustomers, () => setCustomers([]));
    api.invoiceSettings().then(
      (st) => setSalesperson((cur) => cur || st.default_salesperson),
      () => undefined,
    );
    api.products({ active: true, limit: 200 }).then((p) => setProducts(p.items), () => setProducts([]));
    api.warehouses().then((w) => {
      const ok = w.filter((x) => x.active && session && (canInWarehouse(session, "invoice.issue", x.id) || canInWarehouse(session, "invoice.prepare", x.id)));
      setWh(ok[0]?.id ?? "");
    }, () => setWh(""));
  }, [session]);

  if (!session) return null;
  const canIssue = !!wh && canInWarehouse(session, "invoice.issue", wh);
  const used = billable(lines);
  const cust = customers.find((c) => c.id === customer);
  const changed = () => {
    setPv(null);
    setHanded(false);
  };

  async function setProduct(key: number, product_id: string) {
    changed();
    // Record the pick at once (a prefill arriving meanwhile must see the list as touched).
    setLines((ls) => ls.map((l) => (l.key === key ? { ...l, product_id, unit_code: "", prefilled: false } : l)));
    let d = details[product_id];
    if (!d && product_id) {
      d = await api.product(product_id);
      setDetails((x) => ({ ...x, [product_id]: d as Product }));
    }
    setLines((ls) => ls.map((l) => (l.key === key && l.product_id === product_id ? { ...l, unit_code: d ? (unitsFor(d)[0] ?? "") : "" } : l)));
  }
  const setLine = (key: number, patch: Partial<DraftLine>) => {
    changed();
    setLines((ls) => ls.map((l) => (l.key === key ? { ...l, ...patch } : l)));
  };
  /** Start the product list from the customer's last invoice, so staff only enter quantities.
   * Only while the list is untouched (no quantity typed, nothing added by hand). */
  async function chooseCustomer(id: string) {
    changed();
    setCustomer(id);
    prefillFor.current = id;
    if (!untouched(lines)) return;
    setLastInvoice(null);
    if (!id) return setLines((cur) => withPrefill(cur, [], blank));
    try {
      const page = await api.invoices({ customer_id: id, limit: 1 });
      const last = page.items[0];
      const rows = last ? productsFromInvoice(await api.invoice(last.id)).filter((r) => products.some((p) => p.id === r.product_id)) : [];
      const fetched = await Promise.all(rows.filter((r) => !details[r.product_id]).map((r) => api.product(r.product_id)));
      if (prefillFor.current !== id) return; // another customer was chosen meanwhile
      const all = { ...details, ...Object.fromEntries(fetched.map((d) => [d.id, d])) };
      setDetails(all);
      const pre: DraftLine[] = rows.map((r) => {
        const units = all[r.product_id] ? unitsFor(all[r.product_id] as Product) : [];
        return { key: nextKey++, product_id: r.product_id, unit_code: units.includes(r.unit_code) ? r.unit_code : (units[0] ?? ""), qty: "", prefilled: true };
      });
      setLines((cur) => withPrefill(cur, pre, blank)); // keeps anything typed or picked meanwhile
      setLastInvoice(last ? last.invoice_no : null);
    } catch {
      if (prefillFor.current === id) setLines((cur) => withPrefill(cur, [], blank)); // no invoice access
    }
  }

  const body = () => ({
    customer_id: customer,
    warehouse_id: wh,
    salesperson: salesperson.trim(),
    customer_po: po.trim(),
    note: note.trim(),
    lines: used.map((l) => ({ product_id: l.product_id, unit_code: l.unit_code as UnitCode, qty: l.qty.trim() })),
  });

  async function check() {
    setError(null);
    const problem = !wh ? "You can't invoice in any warehouse." : formProblem(customer, lines);
    if (problem) return setError(problem);
    setBusy(true);
    try {
      setPv(await api.previewDirectInvoice(body()));
    } catch (err) {
      setError(invoiceMessage(err));
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
      navigate(`/invoices/${inv.id}`);
    } catch (err) {
      setError(invoiceMessage(err));
      setPv(null);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <section className="card space-y-4 p-6" aria-labelledby="ni-direct">
        <CardTitle id="ni-direct" title="Direct invoice" />
        <p className="text-sm text-ink-muted">For small sales handed over without a picking sheet. Prices come from the price book; stock is taken from the oldest expiry first.</p>
        {error && (
          <p role="alert" className="alert-danger">
            {error}
          </p>
        )}
        <div className="grid gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <div>
            <label className="label" htmlFor="ni-customer">
              Customer
            </label>
            <select id="ni-customer" className="input" value={customer} onChange={(e) => void chooseCustomer(e.target.value)}>
            <option value="">Choose…</option>
              {customers.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                  {c.external_ref ? ` (${c.external_ref})` : ""}
                </option>
              ))}
            </select>
          </div>
          <div>
            <span className="label" id="ni-date-label">
              Invoice date
            </span>
            <p className="input bg-subtle" aria-labelledby="ni-date-label">
              {today()}
            </p>
          </div>
          <div>
            <label className="label" htmlFor="ni-salesperson">
              Salesperson
            </label>
            <input id="ni-salesperson" className="input" maxLength={100} value={salesperson} onChange={(e) => setSalesperson(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="ni-po">
              Customer PO No. (optional)
            </label>
            <input id="ni-po" className="input" maxLength={64} value={po} onChange={(e) => setPo(e.target.value)} />
          </div>
        </div>
        {cust && <CustomerOnInvoice c={cust} />}
        <fieldset className="space-y-3">
          <legend className="label">Products</legend>
          {lastInvoice && lines.some((l) => l.prefilled) && (
            <p className="text-sm text-ink-muted" role="status">
              {`Products from ${cust?.name ?? "this customer"}'s last invoice (${lastInvoice}) are listed. Enter the quantities; products left empty are not billed.`}
            </p>
          )}
          {lines.map((l, i) => {
            const units = l.product_id && details[l.product_id] ? unitsFor(details[l.product_id] as Product) : [];
            return (
              <div key={l.key} className="grid items-end gap-2 sm:grid-cols-[minmax(0,2fr)_9rem_8rem_auto]">
                <div>
                  <label className="sr-only" htmlFor={`ni-p-${l.key}`}>{`Product, line ${i + 1}`}</label>
                  <select id={`ni-p-${l.key}`} className="input" value={l.product_id} onChange={(e) => void setProduct(l.key, e.target.value)}>
                    <option value="">Choose a product…</option>
                    {products.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name} ({p.sku})
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="sr-only" htmlFor={`ni-u-${l.key}`}>{`Unit, line ${i + 1}`}</label>
                  <select id={`ni-u-${l.key}`} className="input" value={l.unit_code} disabled={!units.length} onChange={(e) => setLine(l.key, { unit_code: e.target.value as UnitCode })}>
                    {units.map((u) => (
                      <option key={u} value={u}>
                        {UNIT[u]}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="sr-only" htmlFor={`ni-q-${l.key}`}>{`Quantity, line ${i + 1}`}</label>
                  <input id={`ni-q-${l.key}`} className="input num" inputMode="decimal" placeholder="Qty" value={l.qty} autoFocus={i === 0 && !!l.prefilled} onChange={(e) => setLine(l.key, { qty: e.target.value })} />
                </div>
                <button
                  type="button"
                  className="btn-ghost px-3"
                  aria-label={`Remove line ${i + 1}`}
                  disabled={lines.length === 1}
                  onClick={() => {
                    changed();
                    setLines((ls) => ls.filter((x) => x.key !== l.key));
                  }}
                >
                  Remove
                </button>
              </div>
            );
          })}
          <button type="button" className="btn-ghost" onClick={() => setLines((ls) => [...ls, blank()])}>
            Add product
          </button>
        </fieldset>
        <div>
          <label className="label" htmlFor="ni-note">
            Note on the invoice (optional)
          </label>
          <input id="ni-note" className="input" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
        </div>
        <div className="flex justify-end">
          <button type="button" className="btn-primary" disabled={busy} onClick={() => void check()}>
            Check prices and stock
          </button>
        </div>
      </section>

      {pv && (
        <section className="card space-y-4 p-6" aria-labelledby="ni-preview">
          <CardTitle id="ni-preview" title="Invoice preview" />
          <table className="w-full text-sm">
            <caption className="sr-only">Invoice preview lines</caption>
            <thead>
              <tr className="text-left text-ink-muted">
                <th className="py-2 font-medium">Product</th>
                <th className="py-2 text-right font-medium">Quantity</th>
                <th className="py-2 text-right font-medium">Price</th>
                <th className="py-2 text-right font-medium">Amount</th>
                <th className="py-2 font-medium">Stock</th>
              </tr>
            </thead>
            <tbody>
              {pv.lines.map((l, i) => {
                const st = pv.stock[i];
                return (
                  <tr key={`${l.sku}-${l.unit_code}`} className="border-t border-line">
                    <td className="py-2.5">
                      {l.description} <span className="text-ink-muted">· {l.sku}</span>
                    </td>
                    <td className="num py-2.5 text-right">
                      {qty(dec(l.billed_qty))} {UNIT[l.unit_code]}
                    </td>
                    <td className="num py-2.5 text-right">{cad(dec(l.unit_rate))}</td>
                    <td className="num py-2.5 text-right font-semibold">{cad(dec(l.line_amount))}</td>
                    <td className="py-2.5">{st && (st.ok ? <StatusChip tone="success">In stock</StatusChip> : <StatusChip tone="danger">{`Only ${qty(dec(st.available_qty))}`}</StatusChip>)}</td>
                  </tr>
                );
              })}
            </tbody>
            <tfoot>
              <tr className="border-t border-line">
                <td colSpan={3} className="py-2.5 text-right text-ink-muted">
                  Tax
                </td>
                <td className="num py-2.5 text-right">{cad(dec(pv.tax_amount))}</td>
                <td />
              </tr>
              <tr>
                <td colSpan={3} className="py-2.5 text-right font-semibold">
                  Total
                </td>
                <td className="num py-2.5 text-right text-lg font-semibold" data-testid="direct-total">
                  {cad(dec(pv.total))}
                </td>
                <td />
              </tr>
            </tfoot>
          </table>
          {!pv.stock_ok && (
            <p role="alert" className="alert-warning">
              Not enough stock for every line. Lower the quantities or count the stock first.
            </p>
          )}
          {canIssue ? (
            <>
              <label className="flex items-center gap-2.5 text-sm">
                <input type="checkbox" className="size-4 accent-brand-primary" checked={handed} onChange={(e) => setHanded(e.target.checked)} />
                The goods have been handed over to the customer
              </label>
              <div className="flex justify-end">
                <button type="button" className="btn-accent" disabled={busy || !pv.stock_ok || !handed} onClick={() => setConfirm(true)}>
                  Issue invoice
                </button>
              </div>
            </>
          ) : (
            <p className="text-sm text-ink-muted">A manager issues the invoice. You can check prices and stock here.</p>
          )}
        </section>
      )}
      <ConfirmDialog open={confirm} title="Issue this invoice?" confirmLabel="Issue invoice" onConfirm={() => void issue()} onClose={() => setConfirm(false)}>
        <p>
          It gets its number now, the stock leaves the warehouse and the invoice is emailed if the customer has an email. Total {pv ? cad(dec(pv.total)) : ""}. Issued invoices can't be edited.
        </p>
      </ConfirmDialog>
    </>
  );
}

const today = () => new Date().toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });

/** What the invoice prints from the customer record (Bill To, Ship To, Terms, Vendor #). */
function CustomerOnInvoice({ c }: { c: Customer }) {
  const bill = c.bill_to_address ?? "";
  const ship = [c.ship_to_name ?? c.name, c.ship_to_address ?? bill].filter(Boolean).join("\n");
  const items = [
    { label: "Bill to", value: [c.name, bill].filter(Boolean).join("\n") },
    { label: "Ship to", value: ship },
    { label: "Terms", value: termsLabel(c.payment_terms_days) },
    { label: "Vendor #", value: c.vendor_no ?? "—" },
  ];
  return (
    <div className="rounded-lg border border-line bg-subtle p-4" aria-label="From the customer record">
      <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
        {items.map((x) => (
          <div key={x.label}>
            <dt className="text-ink-muted">{x.label}</dt>
            <dd className="whitespace-pre-line">{x.value}</dd>
          </div>
        ))}
      </dl>
      {!bill && (
        <p className="mt-3 text-sm text-ink-muted">
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
