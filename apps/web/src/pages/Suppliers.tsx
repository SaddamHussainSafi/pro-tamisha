import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, hasCompanyWide, type SupplierBalance, type SupplierLedger, type SupplierPayMethod, type SupplierPayment } from "../api";
import { useAuth } from "../auth";
import { dec, qty } from "../lib/decimal";
import { amountIn, amountProblem, balanceWords, PAY_METHOD, supplierMessage } from "../lib/suppliers";
import { DataGrid, type Column } from "../ui/DataGrid";
import { CardTitle, ConfirmDialog, EmptyState, ErrorState, KpiCard, LoadingRows, PageHeader, StatusChip } from "../ui/primitives";

type Msg = { tone: "error" | "ok"; text: string } | null;
const link = "font-medium text-brand-primary hover:underline";
const day = (iso: string) => new Date(iso.length === 10 ? `${iso}T12:00:00` : iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const UNIT: Record<string, string> = { BOX: "boxes", PIECE: "pieces", LB: "lb", KG: "kg" };

function Notice({ msg }: { msg: Msg }) {
  if (!msg) return null;
  return (
    <p role={msg.tone === "error" ? "alert" : "status"} className={msg.tone === "error" ? "alert-danger" : "alert-success"}>
      {msg.text}
    </p>
  );
}

function NoAccess({ title }: { title: string }) {
  return (
    <div className="space-y-5">
      <PageHeader title={title} />
      <EmptyState title="You don't have access to supplier accounts" hint="Ask an Owner, Manager or Finance if you need to see what we owe suppliers." />
    </div>
  );
}

/** Record a payment to a supplier. `vendorId` fixes the supplier (detail page); otherwise pick one. */
function RecordPayment({
  suppliers,
  vendorId,
  shipments = [],
  onDone,
  onCancel,
}: {
  suppliers: { id: string; name: string; currency: string }[];
  vendorId?: string;
  shipments?: { id: string; shipment_no: string }[];
  onDone: (p: SupplierPayment) => void;
  onCancel: () => void;
}) {
  const first = suppliers.find((s) => s.id === vendorId);
  const [f, setF] = useState({ vendor: vendorId ?? "", method: "WIRE" as SupplierPayMethod, amount: "", currency: first?.currency ?? "", paid_on: today(), reference: "", shipment: "", note: "" });
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setMsg(null);
    if (!f.vendor) return setMsg({ tone: "error", text: "Choose the supplier." });
    const problem = amountProblem(f.amount);
    if (problem) return setMsg({ tone: "error", text: problem });
    if (!/^[A-Za-z]{3}$/.test(f.currency.trim())) return setMsg({ tone: "error", text: "Enter the 3-letter currency, e.g. USD." });
    setBusy(true);
    try {
      const p = await api.recordSupplierPayment({
        vendor_id: f.vendor,
        method: f.method,
        amount: f.amount.trim(),
        currency: f.currency.trim().toUpperCase(),
        paid_on: f.paid_on,
        reference: f.reference.trim() || null,
        shipment_id: f.shipment || null,
        note: f.note.trim() || null,
      });
      onDone(p);
    } catch (err) {
      setMsg({ tone: "error", text: supplierMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card space-y-4 p-6" aria-labelledby="sp-new" onSubmit={submit} noValidate>
      <h2 id="sp-new" className="text-xl font-semibold tracking-tight">
        Record a payment to a supplier
      </h2>
      <Notice msg={msg} />
      <div className="grid gap-4 md:grid-cols-3">
        {!vendorId && (
          <div>
            <label className="label" htmlFor="sp-vendor">
              Supplier
            </label>
            <select
              id="sp-vendor"
              className="input"
              value={f.vendor}
              onChange={(e) => setF({ ...f, vendor: e.target.value, currency: suppliers.find((s) => s.id === e.target.value)?.currency ?? f.currency })}
            >
              <option value="">Choose…</option>
              {suppliers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </div>
        )}
        <div>
          <label className="label" htmlFor="sp-amount">
            Amount paid
          </label>
          <input id="sp-amount" className="input num" inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />
        </div>
        <div>
          <label className="label" htmlFor="sp-currency">
            Currency
          </label>
          <input id="sp-currency" className="input uppercase" maxLength={3} value={f.currency} onChange={(e) => setF({ ...f, currency: e.target.value })} />
        </div>
        <div>
          <label className="label" htmlFor="sp-date">
            Paid on
          </label>
          <input id="sp-date" type="date" className="input" max={today()} value={f.paid_on} onChange={(e) => setF({ ...f, paid_on: e.target.value })} />
        </div>
        <div>
          <label className="label" htmlFor="sp-method">
            How
          </label>
          <select id="sp-method" className="input" value={f.method} onChange={(e) => setF({ ...f, method: e.target.value as SupplierPayMethod })}>
            {Object.entries(PAY_METHOD).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="sp-ref">
            Bank or cheque reference
          </label>
          <input id="sp-ref" className="input" maxLength={100} value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} />
        </div>
        {shipments.length > 0 && (
          <div>
            <label className="label" htmlFor="sp-shipment">
              For shipment (optional)
            </label>
            <select id="sp-shipment" className="input" value={f.shipment} onChange={(e) => setF({ ...f, shipment: e.target.value })}>
              <option value="">Not for one shipment</option>
              {shipments.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.shipment_no}
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="md:col-span-2">
          <label className="label" htmlFor="sp-note">
            Note (optional)
          </label>
          <input id="sp-note" className="input" maxLength={500} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} />
        </div>
      </div>
      <p className="text-sm text-ink-muted">A recorded payment can't be edited. If it's wrong, void it with a reason and record it again.</p>
      <div className="flex justify-end gap-2">
        <button type="button" className="btn-ghost" onClick={onCancel}>
          Cancel
        </button>
        <button className="btn-primary" disabled={busy}>
          Record payment
        </button>
      </div>
    </form>
  );
}

function PaymentsGrid({ rows, canRecord, showSupplier, onVoided }: { rows: SupplierPayment[]; canRecord: boolean; showSupplier: boolean; onVoided: (msg: Msg) => void }) {
  const [voiding, setVoiding] = useState<SupplierPayment | null>(null);
  const [reason, setReason] = useState("");
  const cols: Column<SupplierPayment>[] = [
    {
      key: "n",
      header: "Payment",
      cell: (p) => <span className={`whitespace-nowrap font-medium ${p.voided_at ? "text-ink-muted line-through" : ""}`}>{p.payment_no}</span>,
      sort: (p) => p.payment_no,
    },
    ...(showSupplier
      ? [
          {
            key: "v",
            header: "Supplier",
            cell: (p: SupplierPayment) => (
              <Link className={link} to={`/suppliers/${p.vendor_id}`}>
                {p.vendor_name}
              </Link>
            ),
            sort: (p: SupplierPayment) => p.vendor_name,
          },
        ]
      : []),
    { key: "d", header: "Paid on", cell: (p) => <span className="whitespace-nowrap">{day(p.paid_on)}</span>, sort: (p) => p.paid_on },
    { key: "m", header: "How", cell: (p) => PAY_METHOD[p.method] },
    { key: "r", header: "Reference", cell: (p) => p.reference ?? "—" },
    { key: "s", header: "Shipment", cell: (p) => p.shipment_no ?? "—" },
    { key: "a", header: "Amount", align: "right", cell: (p) => <span className={`num whitespace-nowrap font-semibold ${p.voided_at ? "text-ink-muted line-through" : ""}`}>{amountIn(p.currency, p.amount)}</span>, sort: (p) => dec(p.amount) },
    {
      key: "x",
      header: "Status",
      cell: (p) => (p.voided_at ? <StatusChip tone="neutral">{`Voided: ${p.void_reason ?? ""}`}</StatusChip> : <StatusChip tone="success">Paid</StatusChip>),
    },
    ...(canRecord
      ? [
          {
            key: "act",
            header: "Actions",
            cell: (p: SupplierPayment) =>
              p.voided_at ? null : (
                <button type="button" className="btn-ghost px-3 py-1.5 text-xs" aria-label={`Void ${p.payment_no}`} onClick={() => setVoiding(p)}>
                  Void
                </button>
              ),
          },
        ]
      : []),
  ];
  async function confirm() {
    if (!voiding) return;
    const p = voiding;
    setVoiding(null);
    try {
      await api.voidSupplierPayment(p.id, reason.trim());
      onVoided({ tone: "ok", text: `${p.payment_no} voided. It stays on the ledger, struck through.` });
    } catch (err) {
      onVoided({ tone: "error", text: supplierMessage(err) });
    }
    setReason("");
  }
  return (
    <>
      <DataGrid rows={rows} columns={cols} rowKey={(p) => p.id} caption="Supplier payments" empty={<EmptyState title="No payments recorded" />} />
      <ConfirmDialog open={voiding !== null} title={`Void ${voiding?.payment_no ?? ""}?`} confirmLabel="Void payment" danger onConfirm={() => (reason.trim().length >= 3 ? void confirm() : undefined)} onClose={() => setVoiding(null)}>
        <p>Use this when the payment was entered by mistake. It stays on the ledger, struck through, and no longer counts.</p>
        <label className="label mt-3 block" htmlFor="sp-void-reason">
          Why? (required)
        </label>
        <input id="sp-void-reason" className="input" value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} />
      </ConfirmDialog>
    </>
  );
}

/* ---------------------------------------------------------------- supplier page */

export function SupplierDetailPage() {
  const { id = "" } = useParams();
  const { session } = useAuth();
  const [params] = useSearchParams();
  const [led, setLed] = useState<SupplierLedger | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const allowed = !!session && hasCompanyWide(session, "supplier.ledger.read");
  const canRecord = !!session && hasCompanyWide(session, "supplier_payment.record");
  const load = useCallback(async () => {
    if (!allowed) return;
    try {
      setLed(await api.supplierLedger(id));
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 404 ? "Supplier not found." : "Couldn't load this supplier.");
    }
  }, [allowed, id]);
  useEffect(() => void load(), [load]);
  if (!session) return null;
  if (!allowed) return <NoAccess title="Supplier" />;
  if (err) return <ErrorState title={err} onRetry={() => void load()} />;
  if (!led) return <LoadingRows cols={5} />;
  const tab = ["ledger", "shipments", "payments"].includes(params.get("tab") ?? "") ? (params.get("tab") as string) : "ledger";
  const tabs = [
    { key: "ledger", label: "Ledger" },
    { key: "shipments", label: `Shipments (${led.shipments.length})` },
    { key: "payments", label: `Payments (${led.payments.filter((p) => !p.voided_at).length})` },
  ];
  type Entry = SupplierLedger["entries"][number];
  const eCols: Column<Entry>[] = [
    { key: "d", header: "Date", cell: (e) => <span className="whitespace-nowrap">{day(e.on)}</span> },
    {
      key: "w",
      header: "What",
      cell: (e) =>
        e.kind === "SHIPMENT" ? (
          <Link className={link} to={`/receiving/${e.id}`}>
            Shipment {e.ref}
          </Link>
        ) : (
          <span className={e.voided ? "text-ink-muted line-through" : ""}>Payment {e.ref}</span>
        ),
    },
    { key: "b", header: "Billed", align: "right", cell: (e) => (dec(e.billed) ? <span className="num whitespace-nowrap">{amountIn(e.currency, e.billed)}</span> : "") },
    { key: "p", header: "Paid", align: "right", cell: (e) => (dec(e.paid) ? <span className={`num whitespace-nowrap ${e.voided ? "text-ink-muted line-through" : ""}`}>{amountIn(e.currency, e.paid)}</span> : "") },
    { key: "r", header: "Balance", align: "right", cell: (e) => <span className="num whitespace-nowrap font-semibold">{amountIn(e.currency, e.balance)}</span> },
  ];
  type Ship = SupplierLedger["shipments"][number];
  const sCols: Column<Ship>[] = [
    {
      key: "n",
      header: "Shipment",
      cell: (s) => (
        <Link className={`${link} whitespace-nowrap`} to={`/receiving/${s.id}`}>
          {s.shipment_no}
        </Link>
      ),
    },
    { key: "i", header: "Their invoice", cell: (s) => s.vendor_invoice_no ?? "—" },
    { key: "d", header: "Received", cell: (s) => <span className="whitespace-nowrap">{day(s.posted_at)}</span> },
    {
      key: "u",
      header: "Ordered → accepted",
      cell: (s) => (
        <ul className="space-y-0.5">
          {s.units.map((u) => (
            <li key={u.unit_code} className="num whitespace-nowrap text-sm">
              {qty(dec(u.declared))} → {qty(dec(u.accepted))} {UNIT[u.unit_code]}
              {dec(u.damaged) > 0n && <span className="text-warning"> · {qty(dec(u.damaged))} damaged</span>}
              {dec(u.rejected) > 0n && <span className="text-danger"> · {qty(dec(u.rejected))} rejected</span>}
            </li>
          ))}
        </ul>
      ),
    },
    {
      key: "b",
      header: "Billed",
      align: "right",
      cell: (s) => (
        <span className="num whitespace-nowrap font-semibold">
          {amountIn(s.currency, s.billed)}
          {s.unpriced_lines > 0 && <span className="block text-xs font-normal text-warning">{s.unpriced_lines} line(s) without a price</span>}
        </span>
      ),
    },
    {
      key: "c",
      header: "Freight, duty…",
      align: "right",
      cell: (s) => (s.landed_costs.length ? <span className="num whitespace-nowrap text-sm">{s.landed_costs.map((c) => `${c.kind.toLowerCase()} ${amountIn(c.currency, c.amount)}`).join(" · ")}</span> : "—"),
    },
  ];
  const nav = (key: string) => (key === "ledger" ? `/suppliers/${led.vendor_id}` : `/suppliers/${led.vendor_id}?tab=${key}`);
  return (
    <div className="space-y-5">
      <nav aria-label="Breadcrumb" className="text-sm">
        <Link className="text-brand-primary underline" to="/suppliers">
          Suppliers
        </Link>{" "}
        / {led.code}
      </nav>
      <PageHeader
        title={led.name}
        subtitle={
          <span className="inline-flex flex-wrap items-center gap-2">
            {led.code}
            {led.country_code && <span>· {led.country_code}</span>}
            <span>· pays in {led.currency}</span>
            {led.email && <span>· {led.email}</span>}
            {!led.active && <StatusChip tone="neutral">Inactive</StatusChip>}
          </span>
        }
        actions={
          canRecord && !recording ? (
            <button type="button" className="btn-accent" onClick={() => setRecording(true)}>
              Record payment
            </button>
          ) : undefined
        }
      />
      <Notice msg={msg} />
      {recording && (
        <RecordPayment
          suppliers={[{ id: led.vendor_id, name: led.name, currency: led.currency }]}
          vendorId={led.vendor_id}
          shipments={led.shipments.map((s) => ({ id: s.id, shipment_no: s.shipment_no }))}
          onCancel={() => setRecording(false)}
          onDone={(p) => {
            setRecording(false);
            setMsg({ tone: "ok", text: `${p.payment_no} recorded: ${amountIn(p.currency, p.amount)}.` });
            void load();
          }}
        />
      )}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {(led.totals.length ? led.totals : [{ currency: led.currency, billed: "0.00", paid: "0.00", balance: "0.00" }]).map((t, i) => (
          <KpiCard key={t.currency} label={`Balance (${t.currency})`} value={amountIn(t.currency, t.balance)} icon="coins" accent={i === 0} note={balanceWords(t.currency, t.balance)} />
        ))}
        {led.totals.map((t) => (
          <KpiCard key={`b-${t.currency}`} label={`Billed / paid (${t.currency})`} value={amountIn(t.currency, t.billed)} icon="invoices" note={`Paid ${amountIn(t.currency, t.paid)}`} />
        ))}
      </div>
      <section className="card space-y-3 p-6" aria-labelledby="sp-returned">
        <CardTitle id="sp-returned" title="Received, damaged and rejected" />
        {led.returned.length ? (
          <ul className="grid gap-2 text-sm sm:grid-cols-2">
            {led.returned.map((u) => (
              <li key={u.unit_code} className="rounded-ctl bg-subtle p-3">
                <span className="font-semibold">{UNIT[u.unit_code]}</span>: {qty(dec(u.declared))} on their documents, {qty(dec(u.accepted))} accepted, {qty(dec(u.damaged))} damaged, {qty(dec(u.rejected))} rejected
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-ink-muted">Nothing received from this supplier yet.</p>
        )}
        <p className="text-sm text-ink-muted">Billed is the value on the supplier's documents. Damaged and rejected goods don't change the balance until a credit is agreed.</p>
      </section>
      <nav aria-label="Supplier sections" className="flex flex-wrap gap-1.5">
        {tabs.map((t) => (
          <Link key={t.key} to={nav(t.key)} aria-current={tab === t.key ? "page" : undefined} className={`chip px-4 py-2 text-sm ${tab === t.key ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:bg-subtle"}`}>
            {t.label}
          </Link>
        ))}
      </nav>
      <section className="card space-y-4 p-6" aria-label={tabs.find((t) => t.key === tab)?.label}>
        {tab === "ledger" && <DataGrid rows={led.entries} columns={eCols} rowKey={(e) => `${e.kind}-${e.id}`} caption="Supplier ledger" empty={<EmptyState title="Nothing on the ledger yet" />} />}
        {tab === "shipments" && <DataGrid rows={led.shipments} columns={sCols} rowKey={(s) => s.id} caption="Shipments from this supplier" empty={<EmptyState title="No posted shipments yet" />} />}
        {tab === "payments" && (
          <PaymentsGrid
            rows={led.payments}
            canRecord={canRecord}
            showSupplier={false}
            onVoided={(m) => {
              setMsg(m);
              void load();
            }}
          />
        )}
      </section>
    </div>
  );
}

/* ---------------------------------------------------------------- all supplier payments */

export function SupplierPaymentsPage() {
  const { session } = useAuth();
  const [rows, setRows] = useState<SupplierPayment[] | null>(null);
  const [suppliers, setSuppliers] = useState<SupplierBalance[]>([]);
  const [error, setError] = useState(false);
  const [recording, setRecording] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const allowed = !!session && hasCompanyWide(session, "supplier.ledger.read");
  const canRecord = !!session && hasCompanyWide(session, "supplier_payment.record");
  const load = useCallback(async () => {
    if (!allowed) return;
    try {
      const [p, s] = await Promise.all([api.supplierPayments({ limit: 500 }), api.supplierBalances()]);
      setRows(p);
      setSuppliers(s);
      setError(false);
    } catch {
      setError(true);
    }
  }, [allowed]);
  useEffect(() => void load(), [load]);
  if (!session) return null;
  if (!allowed) return <NoAccess title="Supplier payments" />;
  return (
    <div className="space-y-5">
      <PageHeader
        title="Supplier payments"
        subtitle="Money we paid suppliers, newest first. Each supplier's page shows what they billed and what is still owed."
        actions={
          canRecord && !recording ? (
            <button type="button" className="btn-accent" onClick={() => setRecording(true)}>
              Record payment
            </button>
          ) : undefined
        }
      />
      <Notice msg={msg} />
      {recording && (
        <RecordPayment
          suppliers={suppliers.filter((s) => s.active).map((s) => ({ id: s.vendor_id, name: s.name, currency: s.currency }))}
          onCancel={() => setRecording(false)}
          onDone={(p) => {
            setRecording(false);
            setMsg({ tone: "ok", text: `${p.payment_no} recorded for ${p.vendor_name}: ${amountIn(p.currency, p.amount)}.` });
            void load();
          }}
        />
      )}
      {error ? (
        <ErrorState title="Couldn't load supplier payments" onRetry={() => void load()} />
      ) : rows === null ? (
        <LoadingRows cols={6} />
      ) : (
        <PaymentsGrid
          rows={rows}
          canRecord={canRecord}
          showSupplier
          onVoided={(m) => {
            setMsg(m);
            void load();
          }}
        />
      )}
    </div>
  );
}
