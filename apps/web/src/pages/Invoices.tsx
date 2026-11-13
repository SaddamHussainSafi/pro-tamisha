import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, canInWarehouse, has, invoicePdfUrl, type Invoice, type InvoicePreview, type InvoiceSummary, type Pick, type ShipmentDetail } from "../api";
import { useAuth } from "../auth";
import { defaultCaseWeight } from "../lib/caseSizes";
import { cad, dec, qty, toFixed } from "../lib/decimal";
import { qtyText } from "../lib/inventory";
import { INVOICE_STATE } from "../lib/payments";
import { shortfall } from "../lib/purchases";
import { EMAIL_STATUS, invoiceMessage, toWeightRows, weightTotals, type WeightDraft } from "../lib/invoices";
import { DataGrid, type Column } from "../ui/DataGrid";
import { NumericInput } from "../ui/NumericInput";
import { InvoiceFiles } from "./Documents";
import { AddedNotice, AddFoundStock } from "./Purchases";
import { ConfirmDialog, EmptyState, ErrorState, FilterBar, LoadingRows, PageHeader, PageSkeleton, StatusChip } from "../ui/primitives";

const link = "font-medium text-brand-primary-hover underline-offset-2 hover:underline";
const money = (v: string) => cad(dec(v));
const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });

export const EmailChip = ({ status }: { status: Invoice["email_status"] }) => <StatusChip tone={EMAIL_STATUS[status].tone}>{EMAIL_STATUS[status].label}</StatusChip>;

/* ================================ List ================================ */

type View = "all" | "QUEUED" | "FAILED" | "NO_EMAIL";
const VIEWS: { value: View; label: string }[] = [
  { value: "all", label: "All" },
  { value: "QUEUED", label: "Email queued" },
  { value: "FAILED", label: "Email failed" },
  { value: "NO_EMAIL", label: "No email" },
];

export function InvoicesPage() {
  const nav = useNavigate();
  const { session } = useAuth();
  const [view, setView] = useState<View>("all");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<InvoiceSummary[] | null>(null);
  const [total, setTotal] = useState(0);
  const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState(false);

  const load = useCallback(
    async (cursor?: string) => {
      try {
        const r = await api.invoices({ q: q.trim() || undefined, email_status: view === "all" ? undefined : view, cursor });
        setRows((prev) => (cursor && prev ? [...prev, ...r.items] : r.items));
        setTotal(r.total);
        setNext(r.next_cursor);
        setError(false);
      } catch {
        setError(true);
      }
    },
    [q, view],
  );
  useEffect(() => {
    const t = setTimeout(() => void load(), 200);
    return () => clearTimeout(t);
  }, [load]);

  const columns: Column<InvoiceSummary>[] = [
    { key: "n", header: "Invoice", cell: (r) => <Link className={link} to={`/invoices/${r.id}`}>{r.invoice_no}</Link>, sort: (r) => r.invoice_no },
    { key: "c", header: "Customer", cell: (r) => <>{r.customer_name}{r.customer_ref && <span className="text-ink-muted"> · {r.customer_ref}</span>}</>, sort: (r) => r.customer_name },
    { key: "o", header: "Order", cell: (r) => r.order_no },
    { key: "d", header: "Issued", cell: (r) => day(r.issued_at), sort: (r) => r.issued_at },
    { key: "due", header: "Due", cell: (r) => day(r.due_at), sort: (r) => r.due_at },
    { key: "t", header: "Total", align: "right", cell: (r) => <span className="num">{money(r.total)}</span>, sort: (r) => dec(r.total) },
    { key: "b", header: "Balance", align: "right", cell: (r) => <span className="num">{money(r.balance)}</span>, sort: (r) => dec(r.balance) },
    { key: "p", header: "Payment", cell: (r) => <StatusChip tone={INVOICE_STATE[r.state].tone}>{INVOICE_STATE[r.state].label}</StatusChip> },
    { key: "e", header: "Email", cell: (r) => <EmailChip status={r.email_status} /> },
  ];
  return (
    <div className="space-y-5">
      <PageHeader
        title="Invoices"
        subtitle="Issued invoices never change. Prices and PDFs stay exactly as issued."
        actions={
          session && (has(session, "invoice.prepare") || has(session, "invoice.issue")) ? (
            <Link className="btn-primary" to="/invoices/new">
              Create invoice
            </Link>
          ) : undefined
        }
      />
      <FilterBar search={q} onSearch={setQ} options={VIEWS} value={view} onChange={setView} placeholder="Invoice no., order no. or customer" />
      <section className="card overflow-clip">
        {error ? (
          <ErrorState title="Couldn't load invoices" onRetry={() => void load()} />
        ) : rows === null ? (
          <LoadingRows cols={7} />
        ) : (
          <>
            <DataGrid
              rows={rows}
              columns={columns}
              rowKey={(r) => r.id}
              caption={`Invoices (${total})`}
              onOpen={(r) => nav(`/invoices/${r.id}`)}
              empty={<EmptyState title="No invoices yet" hint="Invoices are issued from a verified packing sheet." action={<Link className="btn-ghost" to="/packing">Open packing</Link>} />}
            />
            {next && (
              <div className="flex justify-center border-t border-line p-4">
                <button className="btn-ghost" onClick={() => void load(next)}>Load more</button>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}

/* ================================ Detail ================================ */

function LinesTable({ lines, caption }: { lines: InvoicePreview["lines"]; caption: string }) {
  return (
    <table className="w-full">
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr>
          <th className="th">Product</th>
          <th className="th text-right">Cases</th>
          <th className="th text-right">Billed qty</th>
          <th className="th text-right">Rate</th>
          <th className="th text-right">Amount</th>
        </tr>
      </thead>
      <tbody>
        {lines.map((l, i) => (
          <tr key={`${l.pick_line_id}-${i}`} data-testid={`inv-line-${l.sku}`}>
            <td className="td">
              <span className="font-medium">{l.sku}</span> <span className="text-ink-muted">{l.description}</span>
              {l.case_weight_lb && <div className="text-xs text-ink-muted">{qtyText(l.cases ?? "0")} × {qtyText(l.case_weight_lb)} lb (weighed)</div>}
            </td>
            <td className="td num text-right">{l.cases ? qtyText(l.cases) : "—"}</td>
            <td className="td num text-right">{qtyText(l.billed_qty)} {l.unit_code}</td>
            <td className="td num text-right">{money(l.unit_rate).replace("CAD ", "")}/{l.unit_code}</td>
            <td className="td num text-right">{money(l.line_amount)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Totals({ subtotal, tax, total, note }: { subtotal: string; tax: string; total: string; note: string }) {
  return (
    <dl className="ml-auto grid w-full max-w-xs grid-cols-2 gap-x-4 gap-y-1 text-sm">
      <dt className="text-ink-muted">Subtotal</dt>
      <dd className="num text-right">{money(subtotal)}</dd>
      <dt className="text-ink-muted">Tax <span className="text-xs">({note.split(" - ")[0]})</span></dt>
      <dd className="num text-right">{money(tax)}</dd>
      <dt className="font-semibold">Total</dt>
      <dd className="num text-right font-semibold" data-testid="invoice-total">{money(total)}</dd>
    </dl>
  );
}

export function InvoicePage() {
  const { id = "" } = useParams();
  const { session } = useAuth();
  const [inv, setInv] = useState<Invoice | null>(null);
  const [loadErr, setLoadErr] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setInv(await api.invoice(id));
      setLoadErr(false);
    } catch {
      setLoadErr(true);
    }
  }, [id]);
  useEffect(() => void load(), [load]);

  if (!session) return null;
  if (loadErr) return <ErrorState title="Couldn't load this invoice" onRetry={() => void load()} />;
  if (!inv) return <PageSkeleton />;
  const canEmail =
    (canInWarehouse(session, "invoice.issue", inv.warehouse_id) || canInWarehouse(session, "invoice.prepare", inv.warehouse_id)) &&
    inv.email_status !== "QUEUED";
  const resend = async () => {
    setError(null);
    setSaved(null);
    try {
      setInv(await api.resendInvoiceEmail(inv.id));
      setSaved("Email queued.");
    } catch (err) {
      setError(invoiceMessage(err));
    }
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title={`Invoice ${inv.invoice_no}`}
        subtitle={
          <>
            {inv.customer_name} · Order <Link className={link} to={`/orders/${inv.sales_order_id}`}>{inv.order_no}</Link> · Sheet{" "}
            <Link className={link} to={`/packing/${inv.picking_attempt_id}`}>{inv.pick_code}</Link> · <StatusChip tone="success">Issued</StatusChip>{" "}
            <StatusChip tone={INVOICE_STATE[inv.state].tone}>{INVOICE_STATE[inv.state].label}</StatusChip>
          </>
        }
        actions={
          <>
            <a className="btn-ghost" href={invoicePdfUrl(inv.id)} target="_blank" rel="noreferrer">Open PDF</a>
            <a className="btn-ghost" href={invoicePdfUrl(inv.id)} download={`${inv.invoice_no}.pdf`}>Download PDF</a>
            {canEmail && <button className="btn-primary" onClick={() => void resend()}>{inv.email_status === "NO_EMAIL" ? "Email invoice" : "Email again"}</button>}
          </>
        }
      />
      {error && <p className="alert-danger" role="alert">{error}</p>}
      {saved && <p className="alert-success" role="status">{saved}</p>}
      <div className="grid gap-5 lg:grid-cols-[1fr_20rem]">
        <section className="card space-y-4 overflow-x-auto p-0 pb-4" aria-label="Invoice lines">
          <LinesTable lines={inv.lines} caption="Invoice lines" />
          <div className="px-4">
            <Totals subtotal={inv.subtotal} tax={inv.tax_amount} total={inv.total} note={inv.tax_note} />
          </div>
        </section>
        <aside className="space-y-5">
          <section className="card space-y-2 p-6 text-sm" aria-labelledby="inv-facts">
            <h2 id="inv-facts" className="font-semibold">Details</h2>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
              <dt className="text-ink-muted">Issued</dt>
              <dd>{new Date(inv.issued_at).toLocaleString()}</dd>
              <dt className="text-ink-muted">Due</dt>
              <dd>{day(inv.due_at)}</dd>
              <dt className="text-ink-muted">Paid</dt>
              <dd className="num">{money(inv.paid_amount)}</dd>
              <dt className="text-ink-muted">Balance</dt>
              <dd className="num" data-testid="invoice-balance">{money(inv.balance)}</dd>
              <dt className="text-ink-muted">Issued by</dt>
              <dd>{inv.issued_by_name ?? "—"}</dd>
              <dt className="text-ink-muted">Template</dt>
              <dd>{inv.template_version}</dd>
              <dt className="text-ink-muted">PDF SHA-256</dt>
              <dd className="num break-all text-xs" title="Fingerprint of the stored PDF; it never changes">{inv.pdf_sha256.slice(0, 16)}…</dd>
            </dl>
          </section>
          <section className="card space-y-2 p-6 text-sm" aria-labelledby="inv-email">
            <h2 id="inv-email" className="font-semibold">Email</h2>
            <EmailChip status={inv.email_status} />
            {inv.email_to && <p>To {inv.email_to}</p>}
            {inv.email_sent_at && <p className="text-ink-muted">Sent {new Date(inv.email_sent_at).toLocaleString()}</p>}
            {inv.email_error && <p className="text-danger">{inv.email_error}</p>}
            <p className="text-xs text-ink-muted">Development mailbox only — no real email leaves the system yet (D-15).</p>
          </section>
          <InvoiceFiles invoiceId={inv.id} warehouseId={inv.warehouse_id} />
          <section className="card space-y-2 p-6 text-sm" aria-labelledby="inv-stock">
            <h2 id="inv-stock" className="font-semibold">Stock deducted</h2>
            <ul className="space-y-1">
              {inv.movements.map((m) => (
                <li key={m.id}>
                  <Link className={link} to={`/inventory/lots/${m.lot_id}`}>{m.lot_code}</Link> <span className="num">{qtyText(m.delta_qty)} {m.unit_code}</span>
                  <span className="text-ink-muted"> → {qtyText(m.qty_after)} left</span>
                </li>
              ))}
            </ul>
          </section>
        </aside>
      </div>
    </div>
  );
}

/* ================================ Issue (from a verified pick) ================================ */

export function IssueInvoicePage() {
  const { id = "" } = useParams();
  const { session } = useAuth();
  const nav = useNavigate();
  const [pick, setPick] = useState<Pick | null>(null);
  const [pv, setPv] = useState<InvoicePreview | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [byWeight, setByWeight] = useState<Record<string, boolean>>({});
  const [weights, setWeights] = useState<Record<string, WeightDraft[]>>({});
  const [caseWeight, setCaseWeight] = useState<Record<string, string | null>>({}); // sku -> default case-size weight
  useEffect(() => {
    api.caseSizes().then(
      (all) => {
        const bySku: Record<string, string | null> = {};
        for (const sku of new Set(all.map((c) => c.sku))) bySku[sku] = defaultCaseWeight(all.filter((c) => c.sku === sku), "BOX");
        setCaseWeight(bySku);
      },
      () => setCaseWeight({}),
    );
  }, []);
  const [dispatched, setDispatched] = useState(false);
  const [note, setNote] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [added, setAdded] = useState<ShipmentDetail | null>(null);

  const enteredWeights = useCallback(
    () => Object.fromEntries(Object.entries(weights).filter(([k]) => byWeight[k]).map(([k, rows]) => [k, toWeightRows(rows)])),
    [weights, byWeight],
  );

  const refresh = useCallback(
    async (w: Record<string, ReturnType<typeof toWeightRows>> = {}) => {
      setError(null);
      try {
        setPv(await api.invoicePreview(id, w));
      } catch (err) {
        if (err instanceof ApiError && (err.code === "ALREADY_INVOICED" || err.code === "INVALID_STATE")) {
          setLoadErr(err.code === "ALREADY_INVOICED" ? "already" : "state");
        } else {
          setError(invoiceMessage(err));
        }
      }
    },
    [id],
  );
  useEffect(() => {
    void (async () => {
      try {
        setPick(await api.pick(id));
      } catch {
        setLoadErr("load");
        return;
      }
      await refresh();
    })();
  }, [id, refresh]);

  if (!session) return null;
  if (loadErr === "already") {
    return <ErrorState title="This packing sheet is already invoiced" detail="Open it from the invoice list." onRetry={() => nav("/invoices")} />;
  }
  if (loadErr === "state") return <ErrorState title="This packing sheet isn't verified yet" detail="A supervisor must verify the packing first." onRetry={() => nav(`/packing/${id}`)} />;
  if (loadErr) return <ErrorState title="Couldn't load this packing sheet" onRetry={() => window.location.reload()} />;
  if (!pick || !pv) return <PageSkeleton />;
  const canIssue = canInWarehouse(session, "invoice.issue", pick.warehouse_id);
  const weighable = pick.lines.filter((l) => pv.weighable_pick_line_ids.includes(l.id));
  const weightsReady = weighable.every((l) => {
    if (!byWeight[l.id]) return true;
    const t = weightTotals(weights[l.id] ?? []);
    return t !== null && l.picked_qty !== null && dec(String(t.cases)) === dec(l.picked_qty);
  });

  const setRows = (lineId: string, rows: WeightDraft[]) => setWeights((w) => ({ ...w, [lineId]: rows }));
  const issue = async () => {
    setBusy(true);
    setError(null);
    try {
      const inv = await api.issueInvoice(pick.id, { version: pv.pick_version, dispatch_confirmed: dispatched, weights: enteredWeights(), note });
      nav(`/invoices/${inv.id}`);
    } catch (err) {
      setConfirm(false);
      setError(invoiceMessage(err));
      if (err instanceof ApiError && err.status === 409) {
        setPick(await api.pick(id).catch(() => pick));
        await refresh(enteredWeights());
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title={`Invoice for ${pv.pick_code}`}
        subtitle={<>Order <Link className={link} to={`/orders/${pv.order_id}`}>{pv.order_no}</Link> · {pv.customer_name} · terms {pv.payment_terms_days} days · {pv.email_to ? `emails to ${pv.email_to}` : "no email on file"}</>}
        actions={<Link className="btn-ghost" to={`/packing/${pick.id}`}>Back to sheet</Link>}
      />
      <p className="alert-warning" role="note">Draft preview — nothing is numbered and no stock moves until you issue. Tax: {pv.tax_note}.</p>
      {error && <p className="alert-danger" role="alert">{error}</p>}

      {weighable.length > 0 && (
        <section className="card space-y-4 p-6" aria-labelledby="cw-h">
          <div>
            <h2 id="cw-h" className="font-semibold">Bill by weight (optional)</h2>
            <p className="text-sm text-ink-muted">For boxes sold by the pound, enter the scale weight of each case. Same weights can share a row.</p>
          </div>
          {weighable.map((l) => {
            const rows = weights[l.id] ?? [{ cases: "", weight: "" }];
            const t = weightTotals(rows);
            const picked = l.picked_qty ?? "0";
            const mismatch = t !== null && dec(String(t.cases)) !== dec(picked);
            return (
              <fieldset key={l.id} className="space-y-3 rounded-[var(--radius-ctl)] border border-line p-3" data-testid={`cw-${l.sku}`}>
                <legend className="px-1 text-sm font-medium">{l.sku} — {qtyText(picked)} {l.unit_code} packed</legend>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={!!byWeight[l.id]}
                    onChange={(e) => {
                      setByWeight((b) => ({ ...b, [l.id]: e.target.checked }));
                      // Default case size fills the first row; the user replaces it with the scale weight.
                      const w = l.unit_code === "BOX" ? caseWeight[l.sku] : null;
                      if (!weights[l.id]) setRows(l.id, [{ cases: w ? toFixed(dec(picked), 0) : "", weight: w ?? "" }]);
                    }}
                  />
                  Bill {l.sku} by weight (per lb)
                </label>
                {byWeight[l.id] && (
                  <>
                    {l.unit_code === "BOX" && caseWeight[l.sku] && (
                      <p className="text-xs text-ink-muted">Filled in from the default case size ({qty(dec(caseWeight[l.sku] ?? "0"), 2)} lb a box). Replace it with the scale weight if it differs.</p>
                    )}
                    {rows.map((r, i) => (
                      <div key={i} className="flex flex-wrap items-end gap-2">
                        <div className="w-28">
                          <NumericInput label={`Cases, row ${i + 1}`} value={r.cases} onChange={(v) => setRows(l.id, rows.map((x, j) => (j === i ? { ...x, cases: v } : x)))} places={0} unit="BOX" />
                        </div>
                        <div className="w-36">
                          <NumericInput label={`Weight each, row ${i + 1}`} value={r.weight} onChange={(v) => setRows(l.id, rows.map((x, j) => (j === i ? { ...x, weight: v } : x)))} places={4} unit="lb" />
                        </div>
                        {rows.length > 1 && (
                          <button type="button" className="btn-ghost py-1 text-xs" aria-label={`Remove row ${i + 1}`} onClick={() => setRows(l.id, rows.filter((_, j) => j !== i))}>Remove</button>
                        )}
                      </div>
                    ))}
                    <div className="flex flex-wrap items-center gap-3 text-sm">
                      <button type="button" className="btn-ghost py-1 text-xs" onClick={() => setRows(l.id, [...rows, { cases: "", weight: "" }])}>Add row</button>
                      <span className={mismatch ? "text-danger" : "text-ink-muted"} aria-live="polite">
                        {t ? `${t.cases} of ${qtyText(picked)} cases · ${t.lb} lb` : "Enter cases and weight for each row"}
                      </span>
                    </div>
                  </>
                )}
              </fieldset>
            );
          })}
          <button className="btn-ghost" disabled={!weightsReady} onClick={() => void refresh(enteredWeights())}>Update preview</button>
        </section>
      )}

      <section className="card space-y-4 overflow-x-auto p-0 pb-4" aria-label="Invoice preview">
        <LinesTable lines={pv.lines} caption="Invoice preview lines" />
        <div className="px-4">
          <Totals subtotal={pv.subtotal} tax={pv.tax_amount} total={pv.total} note={pv.tax_note} />
        </div>
      </section>

      <AddedNotice added={added} />
      <section className="card space-y-3 p-6" aria-labelledby="st-h">
        <h2 id="st-h" className="font-semibold">Stock check</h2>
        <ul className="space-y-1 text-sm">
          {pv.stock.map((s) => (
            <li key={s.pick_line_id} data-testid={`stock-${s.sku}`} className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <StatusChip tone={s.ok ? "success" : "danger"}>{s.ok ? "In stock" : "Short"}</StatusChip>
              <span>{s.sku}: deduct {qtyText(s.deduct_qty)} {s.unit_code}, {qtyText(s.available_qty)} available</span>
              {!s.ok && shortfall(s.deduct_qty, s.available_qty) && (
                <AddFoundStock
                  warehouseId={pick.warehouse_id}
                  productId={s.product_id}
                  productName={pick.lines.find((l) => l.id === s.pick_line_id)?.product_name ?? s.sku}
                  unit={s.unit_code}
                  qty={shortfall(s.deduct_qty, s.available_qty) as string}
                  neededFor={`Invoice for packing sheet ${pick.pick_code}`}
                  onAdded={(x) => {
                    setAdded(x);
                    void refresh(enteredWeights());
                  }}
                />
              )}
            </li>
          ))}
        </ul>
      </section>

      {canIssue ? (
        <section className="card space-y-3 p-6" aria-labelledby="is-h">
          <h2 id="is-h" className="font-semibold">Issue</h2>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={dispatched} onChange={(e) => setDispatched(e.target.checked)} />
            The goods on this sheet have been dispatched to the customer
          </label>
          <div>
            <label className="label" htmlFor="inv-note">Note on invoice (optional)</label>
            <input id="inv-note" className="input" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
          </div>
          <button className="btn-primary" disabled={!dispatched || !pv.can_issue || !weightsReady || busy} onClick={() => setConfirm(true)}>
            Issue invoice
          </button>
          {!pv.can_issue && <p className="text-sm text-danger">Not enough stock in the system to issue. If the goods are on the shelf, add them above with “found in the warehouse”; otherwise count the stock.</p>}
        </section>
      ) : (
        <p className="alert-warning" role="note">A manager issues this invoice. You can check the preview.</p>
      )}

      <ConfirmDialog open={confirm} title={`Issue invoice for ${money(pv.total)}?`} confirmLabel={busy ? "Issuing…" : "Issue invoice"} onClose={() => setConfirm(false)} onConfirm={() => void issue()}>
        This assigns the next invoice number, deducts the stock and stores the PDF. Issued invoices cannot be edited.
      </ConfirmDialog>
    </div>
  );
}
