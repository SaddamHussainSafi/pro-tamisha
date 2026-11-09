import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  api,
  ApiError,
  has,
  type Customer,
  type InvoiceSummary,
  type Payment,
  type PaymentAllocation,
  type PaymentMethod,
  type PaymentPage,
  type PaymentStatus,
  type PaymentSummary,
  type Receivables,
} from "../api";
import { useAuth } from "../auth";
import { cad, dec } from "../lib/decimal";
import { checkPlan, INVOICE_STATE, METHOD, money2, moneyOrNull, PAYMENT_STATUS, paymentMessage, REF_LABEL, suggestAllocation } from "../lib/payments";
import { DataGrid, type Column } from "../ui/DataGrid";
import { ConfirmDialog, EmptyState, ErrorState, FilterBar, KpiCard, LoadingRows, PageHeader, PageSkeleton, StatusChip } from "../ui/primitives";

const link = "font-medium text-brand-primary-hover underline-offset-2 hover:underline";
const money = (v: string) => cad(dec(v));
const day = (iso: string) =>
  new Date(iso.length === 10 ? `${iso}T12:00:00` : iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
const today = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
type Msg = { tone: "error" | "ok"; text: string } | null;

export const PaymentChip = ({ status }: { status: PaymentStatus }) => (
  <StatusChip tone={PAYMENT_STATUS[status].tone}>{PAYMENT_STATUS[status].label}</StatusChip>
);

function Alert({ msg }: { msg: Msg }) {
  if (!msg) return null;
  return (
    <p className={msg.tone === "error" ? "alert-danger" : "alert-success"} role={msg.tone === "error" ? "alert" : "status"}>
      {msg.text}
    </p>
  );
}

function NoAccess({ title }: { title: string }) {
  return (
    <div className="space-y-5">
      <PageHeader title={title} />
      <EmptyState title="You don't have access to payments" hint="Ask a manager or finance if you need to see what a customer has paid." />
    </div>
  );
}

/* ================================ Payments list ================================ */

type View = "all" | PaymentStatus;

export function PaymentsPage() {
  const { session } = useAuth();
  const [view, setView] = useState<View>("REPORTED");
  const [q, setQ] = useState("");
  const [page, setPage] = useState<PaymentPage | null>(null);
  const [rows, setRows] = useState<PaymentSummary[] | null>(null);
  const [error, setError] = useState(false);
  const [recording, setRecording] = useState(false);
  const allowed = !!session && (has(session, "payment.record") || has(session, "payment.verify"));

  const load = useCallback(
    async (cursor?: string) => {
      if (!allowed) return;
      try {
        const r = await api.payments({
          status: view === "all" ? undefined : view,
          q: q.trim() || undefined,
          cursor,
        });
        setPage(r);
        setRows((prev) => (cursor && prev ? [...prev, ...r.items] : r.items));
        setError(false);
      } catch {
        setError(true);
      }
    },
    [allowed, q, view],
  );
  useEffect(() => {
    const t = setTimeout(() => void load(), 200);
    return () => clearTimeout(t);
  }, [load]);

  if (!session) return null;
  if (!allowed) return <NoAccess title="Payments" />;
  const canRecord = has(session, "payment.record");
  const counts = page?.counts;
  const views: { value: View; label: string; count?: number }[] = [
    { value: "REPORTED", label: "To confirm", count: counts?.REPORTED },
    { value: "VERIFIED", label: "Not applied", count: counts?.VERIFIED },
    { value: "APPLIED", label: "Applied", count: counts?.APPLIED },
    { value: "REJECTED", label: "Rejected", count: counts?.REJECTED },
    { value: "all", label: "All" },
  ];
  const columns: Column<PaymentSummary>[] = [
    {
      key: "n",
      header: "Payment",
      cell: (r) => (
        <Link className={link} to={`/payments/${r.id}`}>
          {r.payment_no}
        </Link>
      ),
      sort: (r) => r.payment_no,
    },
    {
      key: "c",
      header: "Customer",
      cell: (r) => (
        <>
          {r.customer_name}
          {r.customer_ref && <span className="text-ink-muted"> · {r.customer_ref}</span>}
        </>
      ),
      sort: (r) => r.customer_name,
    },
    { key: "m", header: "Method", cell: (r) => METHOD[r.method] },
    {
      key: "d",
      header: "Received",
      cell: (r) => day(r.received_on),
      sort: (r) => r.received_on,
    },
    {
      key: "i",
      header: "For invoice",
      cell: (r) => r.reported_invoice_no ?? "—",
    },
    {
      key: "a",
      header: "Amount",
      align: "right",
      cell: (r) => <span className="num">{money(r.amount)}</span>,
      sort: (r) => dec(r.amount),
    },
    {
      key: "u",
      header: "Not applied",
      align: "right",
      cell: (r) => <span className="num">{r.status === "REJECTED" ? "—" : money(r.unallocated_amount)}</span>,
    },
    {
      key: "s",
      header: "Status",
      cell: (r) => <PaymentChip status={r.status} />,
    },
  ];
  return (
    <div className="space-y-5">
      <PageHeader
        title="Payments"
        subtitle="A payment the customer says they sent stays “Reported” until someone checks the bank and confirms it. Only confirmed money reduces what a customer owes."
        actions={
          <>
            <Link className="btn-ghost" to="/receivables">
              Receivables
            </Link>
            {canRecord && !recording && (
              <button className="btn-primary" onClick={() => setRecording(true)}>
                Record payment
              </button>
            )}
          </>
        }
      />
      <div className="grid gap-4 sm:grid-cols-3">
        <KpiCard
          label="To confirm at the bank"
          value={counts?.REPORTED ?? 0}
          tone={counts?.REPORTED ? "warning" : "neutral"}
          note="Customer says sent"
        />
        <KpiCard label="Confirmed, not applied" value={counts?.VERIFIED ?? 0} tone="info" note="Money to apply to invoices" />
        <KpiCard label="Applied" value={counts?.APPLIED ?? 0} tone="success" note="Fully applied" />
      </div>
      {recording && <RecordPayment onCancel={() => setRecording(false)} />}
      <FilterBar search={q} onSearch={setQ} options={views} value={view} onChange={setView} placeholder="Payment no., reference or customer" />
      <section className="card overflow-clip">
        {error ? (
          <ErrorState title="Couldn't load payments" onRetry={() => void load()} />
        ) : rows === null ? (
          <LoadingRows cols={8} />
        ) : (
          <>
            <DataGrid
              rows={rows}
              columns={columns}
              rowKey={(r) => r.id}
              caption={`Payments (${page?.total ?? rows.length})`}
              empty={<EmptyState title="No payments here" hint="Record a payment when a customer tells you they paid." />}
            />
            {page?.next_cursor && (
              <div className="flex justify-center border-t border-line p-4">
                <button className="btn-ghost" onClick={() => void load(page.next_cursor ?? undefined)}>
                  Load more
                </button>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}

/* ================================ Record (customer says sent) ================================ */

function RecordPayment({ onCancel }: { onCancel: () => void }) {
  const nav = useNavigate();
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [invoices, setInvoices] = useState<InvoiceSummary[]>([]);
  const [f, setF] = useState({
    customer_id: "",
    invoice_id: "",
    method: "INTERAC" as PaymentMethod,
    amount: "",
    received_on: today(),
    payer_ref: "",
    note: "",
  });
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f, v: string) => setF((p) => ({ ...p, [k]: v }));

  useEffect(() => {
    api.customers({ limit: 200 }).then(setCustomers, () => setCustomers([]));
  }, []);
  useEffect(() => {
    if (!f.customer_id) return setInvoices([]);
    api.invoices({ customer_id: f.customer_id, open: true, limit: 200 }).then(
      (r) => setInvoices(r.items),
      () => setInvoices([]),
    );
  }, [f.customer_id]);

  const amount = moneyOrNull(f.amount);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!f.customer_id || amount === null) {
      setMsg({
        tone: "error",
        text: "Choose the customer and enter the amount (for example 700.00).",
      });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const p = await api.recordPayment({
        customer_id: f.customer_id,
        invoice_id: f.invoice_id || null,
        method: f.method,
        amount: money2(amount),
        received_on: f.received_on,
        payer_ref: f.payer_ref.trim() || null,
        note: f.note.trim() || null,
      });
      nav(`/payments/${p.id}`);
    } catch (err) {
      setMsg({ tone: "error", text: paymentMessage(err) });
      setBusy(false);
    }
  };

  return (
    <form className="card space-y-4 p-6 text-sm" aria-labelledby="rec-title" onSubmit={(e) => void submit(e)}>
      <div>
        <h2 id="rec-title" className="font-semibold">
          Record a payment the customer reported
        </h2>
        <p className="text-ink-muted">This does not mark anything paid. Someone with bank access confirms it next.</p>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        <label className="block">
          <span className="mb-1 block text-ink-muted">Customer</span>
          <select
            className="input w-full"
            required
            value={f.customer_id}
            onChange={(e) =>
              setF((p) => ({
                ...p,
                customer_id: e.target.value,
                invoice_id: "",
              }))
            }
          >
            <option value="">Choose…</option>
            {customers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
                {c.external_ref ? ` (${c.external_ref})` : ""}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="mb-1 block text-ink-muted">For invoice (optional)</span>
          <select className="input w-full" value={f.invoice_id} disabled={!f.customer_id} onChange={(e) => set("invoice_id", e.target.value)}>
            <option value="">Not specified</option>
            {invoices.map((i) => (
              <option key={i.id} value={i.id}>
                {i.invoice_no} · {money(i.balance)} open
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="mb-1 block text-ink-muted">Method</span>
          <select className="input w-full" value={f.method} onChange={(e) => set("method", e.target.value)}>
            {(Object.keys(METHOD) as PaymentMethod[]).map((m) => (
              <option key={m} value={m}>
                {METHOD[m]}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="mb-1 block text-ink-muted">Amount (CAD)</span>
          <input
            className="input num w-full"
            inputMode="decimal"
            required
            value={f.amount}
            placeholder="0.00"
            aria-invalid={f.amount !== "" && amount === null}
            onChange={(e) => set("amount", e.target.value)}
          />
        </label>
        <label className="block">
          <span className="mb-1 block text-ink-muted">Date sent</span>
          <input className="input w-full" type="date" required max={today()} value={f.received_on} onChange={(e) => set("received_on", e.target.value)} />
        </label>
        <label className="block">
          <span className="mb-1 block text-ink-muted">What the customer quoted (optional)</span>
          <input
            className="input w-full"
            maxLength={120}
            value={f.payer_ref}
            placeholder="e.g. e-Transfer message"
            onChange={(e) => set("payer_ref", e.target.value)}
          />
        </label>
      </div>
      <label className="block">
        <span className="mb-1 block text-ink-muted">Note (optional)</span>
        <input className="input w-full" maxLength={500} value={f.note} onChange={(e) => set("note", e.target.value)} />
      </label>
      <Alert msg={msg} />
      <div className="flex gap-2">
        <button className="btn-primary" type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save as reported"}
        </button>
        <button className="btn-ghost" type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

/* ================================ Payment detail ================================ */

export function PaymentDetailPage() {
  const { id = "" } = useParams();
  const { session } = useAuth();
  const [pay, setPay] = useState<Payment | null>(null);
  const [loadErr, setLoadErr] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const load = useCallback(async () => {
    try {
      setPay(await api.payment(id));
      setLoadErr(false);
    } catch {
      setLoadErr(true);
    }
  }, [id]);
  useEffect(() => void load(), [load]);

  if (!session) return null;
  if (!(has(session, "payment.record") || has(session, "payment.verify"))) return <NoAccess title="Payment" />;
  if (loadErr) return <ErrorState title="Couldn't load this payment" onRetry={() => void load()} />;
  if (!pay) return <PageSkeleton />;
  const canVerify = has(session, "payment.verify");
  const done = (p: Payment, text: string) => {
    setPay(p);
    setMsg({ tone: "ok", text });
  };
  const failed = (err: unknown) => {
    setMsg({ tone: "error", text: paymentMessage(err) });
    void load();
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title={`Payment ${pay.payment_no}`}
        subtitle={
          <>
            {pay.customer_name} · {METHOD[pay.method]} · {money(pay.amount)} · <PaymentChip status={pay.status} />
          </>
        }
        actions={
          <Link className="btn-ghost" to="/payments">
            All payments
          </Link>
        }
      />
      <p className="text-sm text-ink-muted">{PAYMENT_STATUS[pay.status].hint}</p>
      <Alert msg={msg} />
      <div className="grid gap-5 lg:grid-cols-[1fr_20rem]">
        <div className="space-y-5">
          {pay.status === "REPORTED" && canVerify && <Confirm pay={pay} onDone={done} onFail={failed} />}
          {pay.status === "VERIFIED" && canVerify && <Apply pay={pay} onDone={done} onFail={failed} />}
          <History pay={pay} canReverse={canVerify} onDone={done} onFail={failed} />
        </div>
        <Facts pay={pay} />
      </div>
    </div>
  );
}

function Facts({ pay }: { pay: Payment }) {
  const rows: [string, ReactNode][] = [
    ["Amount", money(pay.amount)],
    ["Applied", money(pay.allocated_amount)],
    ["Not applied", money(pay.unallocated_amount)],
    ["Date sent", day(pay.received_on)],
    [
      "For invoice",
      pay.reported_invoice_id ? (
        <Link className={link} to={`/invoices/${pay.reported_invoice_id}`}>
          {pay.reported_invoice_no}
        </Link>
      ) : (
        "—"
      ),
    ],
    ["Customer quoted", pay.payer_ref ?? "—"],
    ["Recorded by", `${pay.recorded_by_name ?? "—"}, ${day(pay.recorded_at)}`],
  ];
  if (pay.verified_at) {
    rows.push([REF_LABEL[pay.method], <span className="font-mono">{pay.verification_ref}</span>]);
    rows.push(["Confirmed by", `${pay.verified_by_name ?? "—"}, ${new Date(pay.verified_at).toLocaleString()}`]);
    if (pay.verification_note) rows.push(["Confirmation note", pay.verification_note]);
  }
  if (pay.rejected_at) {
    rows.push(["Rejected by", `${pay.rejected_by_name ?? "—"}, ${new Date(pay.rejected_at).toLocaleString()}`]);
    rows.push(["Reason", pay.rejected_reason]);
  }
  if (pay.note) rows.push(["Note", pay.note]);
  return (
    <aside className="card space-y-2 self-start p-6 text-sm" aria-labelledby="pay-facts">
      <h2 id="pay-facts" className="font-semibold">
        Details
      </h2>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-ink-muted">{k}</dt>
            <dd className={["Amount", "Applied", "Not applied"].includes(k) ? "num" : undefined}>{v}</dd>
          </div>
        ))}
      </dl>
    </aside>
  );
}

/** Open invoices of the customer with an amount box each, prefilled with a suggestion. */
function useOpenInvoices(pay: Payment, available: string) {
  const [invoices, setInvoices] = useState<InvoiceSummary[] | null>(null);
  const [plan, setPlan] = useState<Record<string, string>>({});
  useEffect(() => {
    api.invoices({ customer_id: pay.customer_id, open: true, limit: 200 }).then(
      (r) => {
        setInvoices(r.items);
        setPlan(suggestAllocation(available, r.items, pay.reported_invoice_id));
      },
      () => setInvoices([]),
    );
  }, [pay.customer_id, pay.reported_invoice_id, available]);
  return { invoices, plan, setPlan };
}

function PlanTable({
  invoices,
  plan,
  setPlan,
  available,
}: {
  invoices: InvoiceSummary[] | null;
  plan: Record<string, string>;
  setPlan: (p: Record<string, string>) => void;
  available: string;
}) {
  if (invoices === null) return <p className="text-ink-muted">Loading open invoices…</p>;
  if (invoices.length === 0) return <p className="text-ink-muted">This customer has no open invoices. The money stays as credit until an invoice is issued.</p>;
  const check = checkPlan(plan, invoices, available);
  return (
    <div className="space-y-2">
      <table className="w-full text-sm">
        <caption className="sr-only">Open invoices</caption>
        <thead>
          <tr className="text-left text-ink-muted">
            <th className="py-1 font-medium">Invoice</th>
            <th className="py-1 font-medium">Due</th>
            <th className="py-1 text-right font-medium">Open</th>
            <th className="py-1 text-right font-medium">Apply now</th>
            <th className="py-1 text-right font-medium">Left after</th>
          </tr>
        </thead>
        <tbody>
          {invoices.map((i) => {
            const v = moneyOrNull(plan[i.id] ?? "");
            return (
              <tr key={i.id} className="border-t border-line">
                <td className="py-1.5">
                  <Link className={link} to={`/invoices/${i.id}`}>
                    {i.invoice_no}
                  </Link>
                </td>
                <td className="py-1.5">{day(i.due_at)}</td>
                <td className="num py-1.5 text-right">{money(i.balance)}</td>
                <td className="py-1.5 text-right">
                  <input
                    className="input num w-32 text-right"
                    inputMode="decimal"
                    aria-label={`Amount to apply to ${i.invoice_no}`}
                    value={plan[i.id] ?? ""}
                    placeholder="0.00"
                    onChange={(e) => setPlan({ ...plan, [i.id]: e.target.value })}
                  />
                </td>
                <td className="num py-1.5 text-right" data-testid={`left-${i.invoice_no}`}>
                  {v === null ? money(i.balance) : cad(dec(i.balance) - v)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="text-ink-muted">
        Applying <span className="num font-medium text-ink">{cad(check.total)}</span> of <span className="num">{money(available)}</span> available.
      </p>
      {check.problems.length > 0 && (
        <ul className="alert-warning list-inside list-disc">
          {check.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

const toItems = (plan: Record<string, string>) =>
  Object.entries(plan)
    .map(([invoice_id, a]) => ({ invoice_id, v: moneyOrNull(a) }))
    .filter((x): x is { invoice_id: string; v: bigint } => x.v !== null)
    .map(({ invoice_id, v }) => ({ invoice_id, amount: money2(v) }));

type Act = {
  pay: Payment;
  onDone: (p: Payment, text: string) => void;
  onFail: (e: unknown) => void;
};

function Confirm({ pay, onDone, onFail }: Act) {
  const [ref, setRef] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const { invoices, plan, setPlan } = useOpenInvoices(pay, pay.amount);
  const check = invoices ? checkPlan(plan, invoices, pay.amount) : { problems: [], total: 0n };

  const confirm = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const p = await api.verifyPayment(pay.id, {
        version: pay.version,
        verification_ref: ref.trim(),
        verification_note: note.trim() || null,
        allocations: toItems(plan),
      });
      onDone(
        p,
        p.status === "APPLIED" ? `Confirmed and applied ${money(p.allocated_amount)}.` : `Confirmed. ${money(p.unallocated_amount)} is not applied yet.`,
      );
    } catch (err) {
      onFail(err);
    } finally {
      setBusy(false);
    }
  };
  const reject = async () => {
    setRejecting(false);
    if (reason.trim().length < 3) return onFail(new ApiError(422, "REASON_REQUIRED", "Enter why the payment is rejected (at least 3 characters)."));
    try {
      onDone(
        await api.rejectPayment(pay.id, {
          version: pay.version,
          reason: reason.trim(),
        }),
        "Payment marked as rejected. It stays on record.",
      );
    } catch (err) {
      onFail(err);
    }
  };

  // The reject dialog sits outside the form so its buttons can never submit the confirmation.
  return (
    <>
      <form className="card space-y-4 p-6 text-sm" aria-labelledby="confirm-title" onSubmit={(e) => void confirm(e)}>
        <div>
          <h2 id="confirm-title" className="font-semibold">
            Confirm at the bank
          </h2>
          <p className="text-ink-muted">Check the deposit in online banking yourself, then enter its reference. This app never logs in to the bank.</p>
        </div>
        <div className="grid gap-4 md:grid-cols-2">
          <label className="block">
            <span className="mb-1 block text-ink-muted">{REF_LABEL[pay.method]}</span>
            <input className="input w-full font-mono" required minLength={3} maxLength={120} value={ref} onChange={(e) => setRef(e.target.value)} />
          </label>
          <label className="block">
            <span className="mb-1 block text-ink-muted">Note (optional)</span>
            <input className="input w-full" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} />
          </label>
        </div>
        <PlanTable invoices={invoices} plan={plan} setPlan={setPlan} available={pay.amount} />
        <div className="flex flex-wrap gap-2">
          <button className="btn-primary" type="submit" disabled={busy || ref.trim().length < 3 || check.problems.length > 0}>
            {busy ? "Confirming…" : "Confirm payment"}
          </button>
          <button className="btn-ghost" type="button" onClick={() => setRejecting(true)}>
            Not found at the bank…
          </button>
        </div>
      </form>
      <ConfirmDialog
        open={rejecting}
        title={`Reject ${pay.payment_no}?`}
        confirmLabel="Reject payment"
        danger
        onClose={() => setRejecting(false)}
        onConfirm={() => void reject()}
      >
        <label className="block text-sm">
          <span className="mb-1 block text-ink-muted">Why (kept on record)</span>
          <input
            className="input w-full"
            minLength={3}
            maxLength={500}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="No matching deposit at the bank"
          />
        </label>
      </ConfirmDialog>
    </>
  );
}

function Apply({ pay, onDone, onFail }: Act) {
  const [busy, setBusy] = useState(false);
  const { invoices, plan, setPlan } = useOpenInvoices(pay, pay.unallocated_amount);
  const check = invoices ? checkPlan(plan, invoices, pay.unallocated_amount) : { problems: [], total: 0n };
  const items = toItems(plan);
  const apply = async () => {
    setBusy(true);
    try {
      const p = await api.allocatePayment(pay.id, {
        version: pay.version,
        allocations: items,
      });
      onDone(p, `Applied ${cad(check.total)}.`);
    } catch (err) {
      onFail(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card space-y-4 p-6 text-sm" aria-labelledby="apply-title">
      <h2 id="apply-title" className="font-semibold">
        Apply to invoices
      </h2>
      <PlanTable invoices={invoices} plan={plan} setPlan={setPlan} available={pay.unallocated_amount} />
      <button className="btn-primary" disabled={busy || items.length === 0 || check.problems.length > 0} onClick={() => void apply()}>
        {busy ? "Applying…" : "Apply payment"}
      </button>
    </section>
  );
}

function History({ pay, canReverse, onDone, onFail }: Act & { canReverse: boolean }) {
  const [target, setTarget] = useState<PaymentAllocation | null>(null);
  const [reason, setReason] = useState("");
  const reverse = async () => {
    const a = target;
    setTarget(null);
    if (!a) return;
    if (reason.trim().length < 3) return onFail(new ApiError(422, "REASON_REQUIRED", "Enter why the line is reversed (at least 3 characters)."));
    try {
      onDone(
        await api.reverseAllocation(pay.id, a.id, {
          version: pay.version,
          reason: reason.trim(),
        }),
        `Reversed ${money(a.amount)} on ${a.invoice_no}.`,
      );
      setReason("");
    } catch (err) {
      onFail(err);
    }
  };
  const columns: Column<PaymentAllocation>[] = [
    {
      key: "i",
      header: "Invoice",
      cell: (r) => (
        <Link className={link} to={`/invoices/${r.invoice_id}`}>
          {r.invoice_no}
        </Link>
      ),
    },
    {
      key: "a",
      header: "Amount",
      align: "right",
      cell: (r) => <span className="num">{money(r.amount)}</span>,
    },
    {
      key: "w",
      header: "When",
      cell: (r) => `${new Date(r.created_at).toLocaleString()} · ${r.created_by_name ?? "—"}`,
    },
    {
      key: "n",
      header: "Note",
      cell: (r) => (r.reverses_id ? `Reversal: ${r.reason ?? ""}` : r.reversed ? "Reversed" : "—"),
    },
    {
      key: "s",
      header: "Invoice now",
      cell: (r) => <StatusChip tone={INVOICE_STATE[r.invoice_state].tone}>{INVOICE_STATE[r.invoice_state].label}</StatusChip>,
    },
    {
      key: "x",
      header: "",
      cell: (r) =>
        canReverse && !r.reverses_id && !r.reversed ? (
          <button className="btn-ghost text-xs" onClick={() => setTarget(r)} aria-label={`Reverse ${money(r.amount)} on ${r.invoice_no}`}>
            Reverse…
          </button>
        ) : null,
    },
  ];
  return (
    <section className="card space-y-3 p-0 pb-2 text-sm" aria-labelledby="hist-title">
      <h2 id="hist-title" className="px-5 pt-5 font-semibold">
        Applied to invoices
      </h2>
      <DataGrid
        rows={pay.allocations}
        columns={columns}
        rowKey={(r) => r.id}
        caption="Allocation history"
        empty={<p className="px-5 text-ink-muted">Nothing applied yet.</p>}
      />
      <p className="px-5 text-xs text-ink-muted">History is never edited. A mistake is undone with a reversal line.</p>
      <ConfirmDialog
        open={target !== null}
        title={target ? `Reverse ${money(target.amount)} on ${target.invoice_no}?` : "Reverse"}
        confirmLabel="Reverse"
        danger
        onClose={() => setTarget(null)}
        onConfirm={() => void reverse()}
      >
        <label className="block text-sm">
          <span className="mb-1 block text-ink-muted">Why (kept on record)</span>
          <input
            className="input w-full"
            minLength={3}
            maxLength={500}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Applied to the wrong invoice"
          />
        </label>
      </ConfirmDialog>
    </section>
  );
}

/* ================================ Receivables (aging) ================================ */

const BUCKETS = [
  { key: "current", label: "Not yet due" },
  { key: "d1_30", label: "1–30 days late" },
  { key: "d31_60", label: "31–60 days late" },
  { key: "d61_90", label: "61–90 days late" },
  { key: "d90_plus", label: "Over 90 days late" },
] as const;

export function ReceivablesPage() {
  const { session } = useAuth();
  const [data, setData] = useState<Receivables | null>(null);
  const [error, setError] = useState(false);
  const allowed = !!session && has(session, "invoice.read") && (has(session, "payment.record") || has(session, "payment.verify"));
  const load = useCallback(async () => {
    if (!allowed) return;
    try {
      setData(await api.receivables());
      setError(false);
    } catch {
      setError(true);
    }
  }, [allowed]);
  useEffect(() => void load(), [load]);

  if (!session) return null;
  if (!allowed) return <NoAccess title="Receivables" />;
  type Row = Receivables["customers"][number];
  const columns: Column<Row>[] = [
    {
      key: "c",
      header: "Customer",
      cell: (r) => (
        <>
          {r.customer_name}
          {r.customer_ref && <span className="text-ink-muted"> · {r.customer_ref}</span>}
        </>
      ),
      sort: (r) => r.customer_name,
    },
    ...BUCKETS.map((b) => ({
      key: b.key,
      header: b.label,
      align: "right" as const,
      cell: (r: Row) => <span className="num">{money(r[b.key])}</span>,
      sort: (r: Row) => dec(r[b.key]),
    })),
    {
      key: "t",
      header: "Owed",
      align: "right",
      cell: (r) => <span className="num font-semibold">{money(r.total)}</span>,
      sort: (r) => dec(r.total),
    },
    {
      key: "u",
      header: "Confirmed, not applied",
      align: "right",
      cell: (r) => <span className="num">{money(r.unapplied_credit)}</span>,
    },
    {
      key: "p",
      header: "Reported, not confirmed",
      align: "right",
      cell: (r) => <span className="num">{money(r.reported_pending)}</span>,
    },
  ];
  return (
    <div className="space-y-5">
      <PageHeader
        title="Receivables"
        subtitle="What customers still owe on issued invoices, by how late it is. Only payments confirmed at the bank reduce these amounts."
        actions={
          <Link className="btn-ghost" to="/payments">
            Payments
          </Link>
        }
      />
      {error ? (
        <div className="card">
          <ErrorState title="Couldn't load receivables" onRetry={() => void load()} />
        </div>
      ) : !data ? (
        <div className="card overflow-clip">
          <LoadingRows cols={6} />
        </div>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <KpiCard label="Owed in total" value={money(data.total)} accent note={`As of ${day(data.as_of)}`} />
            <KpiCard label="Not yet due" value={money(data.current)} tone="success" />
            <KpiCard
              label="Late"
              value={cad(dec(data.d1_30) + dec(data.d31_60) + dec(data.d61_90) + dec(data.d90_plus))}
              tone="danger"
              note={`Over 90 days: ${money(data.d90_plus)}`}
            />
            <KpiCard label="Reported, not confirmed" value={money(data.reported_pending)} tone="warning" note="Check the bank" />
          </div>
          <section className="card overflow-clip">
            <DataGrid
              rows={data.customers}
              columns={columns}
              rowKey={(r) => r.customer_id}
              caption={`Customers with a balance (${data.customers.length})`}
              empty={<EmptyState title="Nothing is owed" hint="Every issued invoice is paid." />}
            />
          </section>
          <section className="card space-y-2 p-6 text-sm" aria-labelledby="pay-terms">
            <h2 id="pay-terms" className="font-semibold">
              Reminders and payment instructions
            </h2>
            <p>{data.payment_instructions}</p>
            <p className="text-ink-muted">
              Reminder emails go out automatically {data.reminder_offsets.map((d) => (d < 0 ? `${-d} days before` : `${d} days after`)).join(", ")} the due date
              while a balance is open. These days and the instructions are placeholders until the owner decides (D-16).
            </p>
          </section>
        </>
      )}
    </div>
  );
}
