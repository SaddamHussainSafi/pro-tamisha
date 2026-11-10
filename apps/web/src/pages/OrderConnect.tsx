import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { ApiError, api, has, hasCompanyWide, type Warehouse } from "../api";
import { useAuth } from "../auth";
import { useTrashChanges } from "../lib/trash";
import { useOcLive } from "../lib/useOcLive";
import { useDebounced } from "../lib/useDebounced";
import {
  INBOX_FILTERS,
  STATUS,
  actionLabel,
  announceChange,
  movePermission,
  normalizeHost,
  oc,
  pct,
  REQUEST_LABEL,
  qtyText,
  relTime,
  sender,
  wordpressSteps,
  type ConvertLine,
  type CustomerMatch,
  type Domain,
  type EmbedInfo,
  type InboxFilter,
  type OcForm,
  type OcSettings,
  type Overview,
  type SubDetail,
  type SubStatus,
  type SubSummary,
} from "../lib/orderConnect";
import { DeleteAction } from "../ui/DeleteAction";
import { Icon } from "../ui/Icon";
import { CardTitle, ConfirmDialog, EmptyState, ErrorState, FilterBar, KpiCard, LoadingRows, PageHeader, PageSkeleton, StatusChip } from "../ui/primitives";

const errText = (e: unknown) => (e instanceof ApiError ? e.message : "Something went wrong. Try again.");
const when = (iso: string) => new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
const link = "font-medium text-brand-primary hover:underline";

function useAccess() {
  const { session } = useAuth();
  const cw = (p: string) => !!session && hasCompanyWide(session, p);
  return {
    read: cw("order_connect.read"),
    manage: cw("order_connect.manage"),
    review: cw("order_connect.review"),
    approve: cw("order_connect.approve"),
    customers: cw("catalog.manage"),
    orders: !!session && has(session, "sales_order.manage"),
  };
}

function NoAccess({ title }: { title: string }) {
  return (
    <div className="space-y-5">
      <PageHeader title={title} />
      <div className="card"><EmptyState title="You don't have access to Online Orders" hint="Ask an administrator for the Online Orders permissions." /></div>
    </div>
  );
}

function Status({ s }: { s: SubStatus }) {
  return <StatusChip tone={STATUS[s].tone}>{STATUS[s].label}</StatusChip>;
}

function Notice({ msg }: { msg: { tone: "ok" | "error"; text: string } | null }) {
  if (!msg) return null;
  return <p role={msg.tone === "error" ? "alert" : "status"} className={msg.tone === "error" ? "alert-danger" : "alert-success"}>{msg.text}</p>;
}

/** How a website entry is named in the Trash and the delete dialog. */
const entryLabel = (s: Pick<SubSummary, "reference" | "business_name" | "customer_name">) => {
  const who = s.business_name || s.customer_name;
  return who ? `${s.reference} — ${who}` : s.reference;
};

function SubRow({ s, deletable = false }: { s: SubSummary; deletable?: boolean }) {
  return (
    <li className="flex items-center gap-1">
      <Link to={`/online-orders/requests/${s.id}`} className="flex min-w-0 flex-1 items-center gap-4 rounded-ctl px-3 py-3 transition-colors duration-150 hover:bg-subtle" data-testid={`oc-row-${s.reference}`}>
        <span aria-hidden className={`size-2.5 shrink-0 rounded-full ${s.unread ? "bg-brand-primary" : "bg-transparent"}`} />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-2">
            <span className={`truncate ${s.unread ? "font-semibold" : "font-medium"}`}>{sender(s)}</span>
            {s.unread && <span className="sr-only">(unread)</span>}
            {s.request_type === "QUOTE" && <StatusChip tone="info">Quote request</StatusChip>}
            {s.possible_duplicate && <StatusChip tone="warning">Possible duplicate</StatusChip>}
          </span>
          <span className="block truncate text-sm text-ink-muted">
            {s.reference} · {s.item_count} product{s.item_count === 1 ? "" : "s"} · {s.form_name}
          </span>
        </span>
        <span className="hidden text-right text-sm text-ink-muted sm:block">{relTime(s.created_at)}</span>
        <Status s={s.status} />
      </Link>
      {/* Beside the link, not inside it (no buttons inside links). A converted entry can't be deleted. */}
      {deletable && s.status !== "CONVERTED" && <DeleteAction compact kind="website_entry" id={s.id} label={entryLabel(s)} />}
    </li>
  );
}

/* ================================================================ overview */

export function OcOverviewPage() {
  const a = useAccess();
  const [o, setO] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    oc.overview().then((v) => { setO(v); setError(""); }, (e) => setError(errText(e)));
  }, []);
  useEffect(() => {
    if (a.read) load();
  }, [a.read, load]);
  useOcLive(() => a.read && load());

  if (!a.read) return <NoAccess title="Online Orders" />;
  return (
    <div className="space-y-5">
      <PageHeader
        title="Online Orders"
        subtitle="Orders customers send from your website. Approved ones become draft sales orders — never invoices or stock moves."
        actions={
          <>
            <Link to="/online-orders/forms" className="btn-ghost">Form builder</Link>
            <Link to="/online-orders/requests" className="btn-primary">Open requests</Link>
          </>
        }
      />
      {error ? (
        <div className="card"><ErrorState title="Couldn't load Online Orders" detail={error} onRetry={load} /></div>
      ) : !o ? (
        <PageSkeleton label="Loading Online Orders" />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <KpiCard accent label="Waiting for review" value={o.open} note={o.unread_for_me ? `${o.unread_for_me} unread for you` : undefined} />
            <KpiCard label="New" value={o.new} note="not opened by anyone yet" />
            <KpiCard label="Received, last 30 days" value={o.received_30d} note={`${o.received_24h} in the last 24 hours`} />
            <KpiCard label="Converted, last 30 days" value={pct(o.conversion_rate_30d)} note={`${o.converted_30d} draft orders created`} />
          </div>
          <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
            <section className="card p-5 sm:p-6" aria-labelledby="oc-latest">
              <CardTitle id="oc-latest" title="Latest requests" to="/online-orders/requests" />
              {o.latest.length ? (
                <ul className="mt-3 divide-y divide-line">{o.latest.map((s) => <SubRow key={s.id} s={s} />)}</ul>
              ) : (
                <EmptyState icon="inbox" title="No requests yet" hint="Publish a form and add it to your website; orders appear here the moment they are sent." />
              )}
            </section>
            <section className="card p-5 sm:p-6" aria-labelledby="oc-status">
              <CardTitle id="oc-status" title="By status" />
              <ul className="mt-3 space-y-2">
                {(Object.keys(STATUS) as SubStatus[]).map((k) => (
                  <li key={k} className="flex items-center justify-between gap-3">
                    <Link to={`/online-orders/requests?status=${k}`} className="hover:underline"><Status s={k} /></Link>
                    <span className="num font-semibold">{o.by_status[k] ?? 0}</span>
                  </li>
                ))}
              </ul>
              <div className="mt-5 rounded-ctl bg-subtle p-4 text-sm">
                <div className="font-medium">{o.forms_live} of {o.forms_total} forms live</div>
                <Link to="/online-orders/embed" className={link}>Get the website code</Link>
              </div>
            </section>
          </div>
        </>
      )}
    </div>
  );
}

/* ================================================================ inbox */

export function OcSubmissionsPage() {
  const a = useAccess();
  const [params, setParams] = useSearchParams();
  const status = (params.get("status") as InboxFilter | null) ?? "OPEN";
  const formId = params.get("form") ?? "";
  const kind = (params.get("type") as "ORDER" | "QUOTE" | null) ?? undefined;
  const [q, setQ] = useState("");
  const dq = useDebounced(q, 250);
  const [items, setItems] = useState<SubSummary[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [forms, setForms] = useState<OcForm[]>([]);

  const load = useCallback(() => {
    oc.submissions({ status, type: kind, q: dq, form_id: formId || undefined, limit: 50 }).then(
      (r) => { setItems(r.items); setNext(r.next_before); setError(""); },
      (e) => setError(errText(e)),
    );
  }, [status, kind, dq, formId]);
  useEffect(() => {
    if (a.read) load();
  }, [a.read, load]);
  useEffect(() => {
    if (a.read) oc.forms().then(setForms, () => undefined);
  }, [a.read]);
  useOcLive(() => a.read && load(), 30_000);
  useTrashChanges(() => a.read && load());

  const more = async () => {
    if (!next) return;
    const r = await oc.submissions({ status, type: kind, q: dq, form_id: formId || undefined, before: next, limit: 50 });
    setItems((cur) => [...(cur ?? []), ...r.items]);
    setNext(r.next_before);
  };
  const set = (k: string, v: string) => {
    const p = new URLSearchParams(params);
    if (v) p.set(k, v);
    else p.delete(k);
    setParams(p, { replace: true });
  };

  if (!a.read) return <NoAccess title="Order & quote requests" />;
  return (
    <div className="space-y-5">
      <PageHeader title="Order & quote requests" subtitle="Orders and quote requests from your website appear here live. Open one to review it." />
      <FilterBar
        search={q}
        onSearch={setQ}
        placeholder="Search name, email, phone, reference"
        options={INBOX_FILTERS}
        value={status}
        onChange={(v) => set("status", v === "OPEN" ? "" : v)}
      >
        <label htmlFor="oc-type-filter" className="sr-only">Request type</label>
        <select id="oc-type-filter" className="input w-auto rounded-full py-2" value={kind ?? ""} onChange={(e) => set("type", e.target.value)}>
          <option value="">Orders and quotes</option>
          <option value="ORDER">Orders only</option>
          <option value="QUOTE">Quote requests only</option>
        </select>
        {forms.length > 1 && (
          <>
            <label htmlFor="oc-form-filter" className="sr-only">Form</label>
            <select id="oc-form-filter" className="input w-auto rounded-full py-2" value={formId} onChange={(e) => set("form", e.target.value)}>
              <option value="">All forms</option>
              {forms.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
            </select>
          </>
        )}
      </FilterBar>
      <section className="card p-3 sm:p-4" aria-label="Request list" aria-live="polite" aria-busy={items === null}>
        {error ? (
          <ErrorState title="Couldn't load requests" detail={error} onRetry={load} />
        ) : items === null ? (
          <LoadingRows rows={6} cols={3} />
        ) : items.length === 0 ? (
          <EmptyState icon="inbox" title={dq ? "Nothing matches your search" : "No requests here"} hint={status === "OPEN" ? "New website orders will appear here the moment they arrive." : undefined} />
        ) : (
          <>
            <ul className="divide-y divide-line">{items.map((s) => <SubRow key={s.id} s={s} deletable />)}</ul>
            {next && <div className="p-3 text-center"><button className="btn-ghost" onClick={() => void more()}>Load more</button></div>}
          </>
        )}
      </section>
    </div>
  );
}

/* ================================================================ submission */

export function OcSubmissionPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const a = useAccess();
  const [d, setD] = useState<SubDetail | null>(null);
  const [error, setError] = useState("");
  const [msg, setMsg] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [confirm, setConfirm] = useState<SubStatus | "ERASE" | null>(null);
  const [reason, setReason] = useState("");

  const load = useCallback(() => {
    oc.submission(id).then((v) => { setD(v); setError(""); }, (e) => setError(errText(e)));
  }, [id]);
  useEffect(() => {
    if (!a.read) return;
    load();
    // Opening it clears this person's alert for it.
    oc.markRead(id).then(announceChange, () => undefined);
  }, [a.read, id, load]);

  const act = async (fn: () => Promise<SubDetail>, ok: string) => {
    setBusy(true);
    setMsg(null);
    try {
      setD(await fn());
      setMsg({ tone: "ok", text: ok });
      announceChange();
    } catch (e) {
      setMsg({ tone: "error", text: e instanceof ApiError && e.code === "VERSION_CONFLICT" ? "Someone else just changed this request. It has been reloaded." : errText(e) });
      load();
    } finally {
      setBusy(false);
    }
  };
  const move = (to: SubStatus) => d && act(() => oc.setStatus(d.id, d.version, to, note.trim()), `Marked ${STATUS[to].label.toLowerCase()}.`).then(() => setNote(""));
  const addNote = (e: FormEvent) => {
    e.preventDefault();
    if (d && note.trim()) void act(() => oc.addNote(d.id, note.trim()), "Note added.").then(() => setNote(""));
  };

  if (!a.read) return <NoAccess title="Request" />;
  if (error) return <div className="card"><ErrorState title="Couldn't load this request" detail={error} onRetry={load} /></div>;
  if (!d) return <PageSkeleton label="Loading request" />;

  const can = (to: SubStatus) => (movePermission(d.status, to) === "order_connect.approve" ? a.approve : a.review);
  // Side moves first, the forward step (start review / approve) last and primary.
  const ORDER: SubStatus[] = ["SPAM", "DUPLICATE", "REJECTED", "REVIEWING", "APPROVED"];
  const moves = ORDER.filter((to) => d.allowed.includes(to) && can(to));
  const forward = moves.includes("APPROVED") ? "APPROVED" : d.status === "NEW" && moves.includes("REVIEWING") ? "REVIEWING" : null;
  const destructive: SubStatus[] = ["REJECTED", "SPAM", "DUPLICATE"];

  return (
    <div className="space-y-5">
      <Link to="/online-orders/requests" className="inline-flex items-center gap-1 text-sm text-ink-muted hover:text-ink"><Icon name="chevronLeft" size={16} />Order & quote requests</Link>
      <PageHeader
        title={sender(d)}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <Status s={d.status} />
            <StatusChip tone={d.request_type === "QUOTE" ? "info" : "neutral"}>{REQUEST_LABEL[d.request_type]}</StatusChip>
            <span>{d.reference} · received {when(d.created_at)} · {d.form_name} (version {d.form_version_no})</span>
          </span>
        }
        actions={
          <>
            {d.status !== "CONVERTED" && <DeleteAction kind="website_entry" id={d.id} label={entryLabel(d)} onDone={() => navigate("/online-orders/requests")} />}
            {moves.map((to) => (
              <button
                key={to}
                className={to === forward ? "btn-primary" : "btn-ghost"}
                disabled={busy}
                onClick={() => (destructive.includes(to) ? setConfirm(to) : void move(to))}
              >
                {actionLabel(d.status, to)}
              </button>
            ))}
          </>
        }
      />
      <Notice msg={msg} />
      {d.possible_duplicate_of && (
        <p className="alert-warning">
          Looks like a repeat of <Link className={link} to={`/online-orders/requests/${d.possible_duplicate_of.id}`}>{d.possible_duplicate_of.reference}</Link> (same contact and products within a day). Check before converting.
        </p>
      )}
      {d.spam_reasons.length > 0 && (
        <p className="alert-warning">Flagged as possible spam: {d.spam_reasons.map((r) => (r === "honeypot" ? "a hidden field was filled in" : r === "too_fast" ? "sent faster than a person can type" : r)).join(", ")}.</p>
      )}
      {d.sales_order && (
        <p className="alert-success">
          Converted to draft sales order <Link className={link} to={`/orders/${d.sales_order.id}`}>{d.sales_order.order_no}</Link>
          {d.converted_by_name ? ` by ${d.converted_by_name}` : ""}{d.converted_at ? `, ${when(d.converted_at)}` : ""}. No invoice, stock movement or reservation was made.
        </p>
      )}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div className="min-w-0 space-y-5">
          <section className="card p-5 sm:p-6" aria-labelledby="oc-items">
            <CardTitle id="oc-items" title={`Products (${d.items.length})`} />
            {d.items.length ? (
              <table className="mt-3 w-full text-sm">
                <caption className="sr-only">Products as the customer sent them</caption>
                <thead><tr><th className="th px-3">Product</th><th className="th px-3 text-right">Quantity</th></tr></thead>
                <tbody>
                  {d.items.map((i) => (
                    <tr key={i.line_no}>
                      <td className="td px-3"><span className="font-medium">{i.product_name}</span> <span className="text-ink-muted">{i.sku}</span></td>
                      <td className="td num px-3 text-right">{qtyText(i.qty, i.unit_code)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="mt-3 text-sm text-ink-muted">No products were accepted with this request.</p>
            )}
            <p className="mt-2 text-xs text-ink-muted">As submitted. Prices are never taken from the website; they are set when the order is confirmed.</p>
          </section>
          {d.status === "APPROVED" && a.approve && <ConvertPanel d={d} onDone={(v) => { setD(v); setMsg(null); announceChange(); }} />}
          {d.status === "APPROVED" && !a.approve && <p className="alert-warning">Someone with approval rights converts approved requests into draft orders.</p>}
          <section className="card p-5 sm:p-6" aria-labelledby="oc-timeline">
            <CardTitle id="oc-timeline" title="Timeline" />
            <ol className="mt-3 space-y-3">
              {d.events.map((e, i) => (
                <li key={i} className="flex gap-3 text-sm">
                  <span aria-hidden className="mt-1.5 size-2 shrink-0 rounded-full bg-brand-primary" />
                  <div className="min-w-0">
                    <div>
                      {e.kind === "RECEIVED" ? "Received from the website" : e.kind === "STATUS" ? `${STATUS[e.from_status as SubStatus]?.label ?? e.from_status} → ${STATUS[e.to_status as SubStatus]?.label ?? e.to_status}` : e.kind === "CONVERTED" ? "Converted" : e.kind === "ERASED" ? "Personal data erased" : "Note"}
                      {e.actor_name && <span className="text-ink-muted"> · {e.actor_name}</span>}
                      <span className="text-ink-muted"> · {when(e.created_at)}</span>
                    </div>
                    {e.note && <p className="whitespace-pre-line break-words text-ink-muted">{e.note}</p>}
                  </div>
                </li>
              ))}
            </ol>
            {a.review && (
              <form onSubmit={addNote} className="mt-4 flex flex-wrap gap-2">
                <label htmlFor="oc-note" className="sr-only">Note</label>
                <input id="oc-note" className="input min-w-0 flex-1" value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} placeholder="Add a note (also saved with the next status change)" />
                <button className="btn-ghost" disabled={busy || !note.trim()}>Add note</button>
              </form>
            )}
          </section>
        </div>

        <aside className="min-w-0 space-y-5">
          <section className="card p-5 sm:p-6" aria-labelledby="oc-details">
            <CardTitle id="oc-details" title="Customer details" />
            {d.erased ? (
              <p className="mt-3 text-sm text-ink-muted">Personal data was erased {d.erased_at ? when(d.erased_at) : ""}.</p>
            ) : (
              <dl className="mt-3 grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-4 gap-y-2 text-sm">
                <Row label="Name" value={d.customer_name} />
                <Row label="Business" value={d.business_name} />
                <Row label="Email" value={d.email} />
                <Row label="Phone" value={d.phone} />
                <Row label="Delivery address" value={d.delivery_address} />
                <Row label="Delivery date" value={d.requested_date ? new Date(`${d.requested_date}T12:00:00`).toLocaleDateString(undefined, { dateStyle: "medium" }) : ""} />
                <Row label="Notes" value={d.notes} />
                {d.custom.map((c) => <Row key={c.key} label={c.label} value={typeof c.value === "boolean" ? (c.value ? "Yes" : "No") : c.value} />)}
                <Row label="Sent from" value={d.source_host || "Direct link"} />
                <Row label="Privacy consent" value={d.consent_at ? when(d.consent_at) : ""} />
              </dl>
            )}
          </section>
          {d.spam_reasons.length > 0 && !d.erased && (
            <section className="card p-5 sm:p-6" aria-labelledby="oc-raw">
              <CardTitle id="oc-raw" title="Values as received" />
              <p className="mt-2 text-sm text-ink-muted">Not checked, because this looked like spam. Reopen it if it is a real order.</p>
              <dl className="mt-3 grid grid-cols-[minmax(7rem,auto)_1fr] gap-x-4 gap-y-2 text-sm">
                {Object.entries((d.raw.values ?? {}) as Record<string, unknown>).map(([k, v]) => (
                  <Row key={k} label={k} value={typeof v === "boolean" ? (v ? "Yes" : "No") : String(v ?? "")} />
                ))}
              </dl>
            </section>
          )}
          {a.manage && !d.erased && (
            <section className="card p-5 sm:p-6" aria-labelledby="oc-privacy">
              <CardTitle id="oc-privacy" title="Privacy" />
              <p className="mt-2 text-sm text-ink-muted">Erase this person's details (for example, on request). Products, status and any linked order stay.</p>
              <button className="btn-ghost mt-3 text-danger" onClick={() => setConfirm("ERASE")}>Erase personal data</button>
            </section>
          )}
        </aside>
      </div>

      <ConfirmDialog
        open={confirm !== null}
        title={confirm === "ERASE" ? "Erase personal data?" : confirm ? `${actionLabel(d.status, confirm)}?` : ""}
        confirmLabel={confirm === "ERASE" ? "Erase" : confirm ? actionLabel(d.status, confirm) : "OK"}
        danger
        onClose={() => { setConfirm(null); setReason(""); }}
        onConfirm={() => {
          const c = confirm;
          setConfirm(null);
          if (c === "ERASE") {
            if (reason.trim().length >= 3) void act(() => oc.erase(d.id, d.version, reason.trim()), "Personal data erased.");
            else setMsg({ tone: "error", text: "Write a short reason (at least 3 characters) to erase personal data." });
            setReason("");
          } else if (c) void move(c);
        }}
      >
        {confirm === "ERASE" ? (
          <div className="space-y-2">
            <p>The name, contact details, address, notes and custom answers are blanked. This can't be undone.</p>
            <label htmlFor="oc-erase-reason" className="label">Reason</label>
            <input id="oc-erase-reason" className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Customer asked us to delete their details" />
          </div>
        ) : (
          <p>{note.trim() ? `Your note will be saved: “${note.trim()}”.` : "You can reopen it later."}</p>
        )}
      </ConfirmDialog>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <>
      <dt className="text-ink-muted">{label}</dt>
      <dd className="min-w-0 whitespace-pre-line break-words">{value}</dd>
    </>
  );
}

/* ---------------------------------------------------------------- convert */

function ConvertPanel({ d, onDone }: { d: SubDetail; onDone: (d: SubDetail) => void }) {
  const a = useAccess();
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [wh, setWh] = useState(d.default_warehouse_id ?? "");
  const [matches, setMatches] = useState<CustomerMatch[] | null>(null);
  const [choice, setChoice] = useState<string>("");
  const [newName, setNewName] = useState(d.business_name || d.customer_name);
  const [search, setSearch] = useState("");
  const [found, setFound] = useState<{ id: string; name: string }[]>([]);
  const [lines, setLines] = useState<(ConvertLine & { label: string; keep: boolean })[]>(
    d.items.map((i) => ({ product_id: i.product_id, unit_code: i.unit_code, qty: i.qty, item_line_no: i.line_no, label: `${i.product_name} (${i.sku})`, keep: true })),
  );
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const dsearch = useDebounced(search, 250);

  useEffect(() => {
    api.warehouses().then((w) => {
      const act = w.filter((x) => x.active);
      setWarehouses(act);
      setWh((cur) => cur || (act.length === 1 ? (act[0]?.id ?? "") : ""));
    }, () => undefined);
    oc.matches(d.id).then((m) => { setMatches(m); setChoice(m[0] && m[0].score >= 3 ? m[0].id : a.customers ? "new" : ""); }, () => setMatches([]));
  }, [d.id, a.customers]);
  useEffect(() => {
    if (!dsearch.trim()) return setFound([]);
    api.customers({ q: dsearch.trim(), active: true, limit: 8 }).then((c) => setFound(c.map((x) => ({ id: x.id, name: x.name }))), () => setFound([]));
  }, [dsearch]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setErr("");
    const keep = lines.filter((l) => l.keep);
    if (!wh) return setErr("Choose the warehouse the order ships from.");
    if (!choice) return setErr("Choose a customer.");
    if (choice === "new" && !newName.trim()) return setErr("Give the new customer a name.");
    if (!keep.length) return setErr("Keep at least one product line.");
    if (keep.some((l) => !(Number(l.qty) > 0))) return setErr("Every quantity must be more than zero.");
    setBusy(true);
    try {
      const out = await oc.convert(d.id, {
        version: d.version,
        warehouse_id: wh,
        customer_id: choice === "new" ? null : choice,
        new_customer: choice === "new" ? { name: newName.trim(), email: d.email || null, phone: d.phone || null } : null,
        lines: keep.map(({ product_id, unit_code, qty, item_line_no }) => ({ product_id, unit_code, qty, item_line_no })),
      });
      onDone(out);
    } catch (e2) {
      setErr(errText(e2));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="card space-y-4 p-5 sm:p-6" aria-labelledby="oc-convert">
      <CardTitle id="oc-convert" title="Create draft sales order" />
      <p className="text-sm text-ink-muted">Creates one DRAFT order. It is not confirmed, priced, reserved or invoiced; that happens in Orders as usual. The request keeps what the customer sent.</p>
      {!a.orders && <p className="alert-warning">You also need permission to manage sales orders for the chosen warehouse.</p>}
      <fieldset className="space-y-2">
        <legend className="label">Customer</legend>
        {matches === null ? (
          <div className="skeleton h-10 w-full" />
        ) : (
          <>
            {matches.map((m) => (
              <label key={m.id} className={`flex cursor-pointer items-start gap-3 rounded-ctl border p-3 text-sm ${choice === m.id ? "border-brand-primary bg-brand-accent/8" : "border-line"}`}>
                <input type="radio" name="oc-cust" className="mt-1" checked={choice === m.id} onChange={() => setChoice(m.id)} disabled={!m.active} />
                <span className="min-w-0">
                  <span className="font-medium">{m.name}</span>{!m.active && <span className="text-ink-muted"> (inactive)</span>}
                  <span className="block text-ink-muted">{[m.email, m.phone, m.external_ref].filter(Boolean).join(" · ")}</span>
                  <span className="mt-1 flex flex-wrap gap-1">{m.reasons.map((r) => <StatusChip key={r} tone="info">{r}</StatusChip>)}</span>
                </span>
              </label>
            ))}
            {matches.length === 0 && <p className="text-sm text-ink-muted">No existing customer looks like this sender.</p>}
            <div className="rounded-ctl border border-line p-3 text-sm">
              <label htmlFor="oc-cust-search" className="label">Or find another customer</label>
              <input id="oc-cust-search" className="input" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search customers" />
              {found.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {found.map((c) => (
                    <li key={c.id}>
                      <label className="flex cursor-pointer items-center gap-2"><input type="radio" name="oc-cust" checked={choice === c.id} onChange={() => setChoice(c.id)} />{c.name}</label>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            {a.customers && (
              <div className={`rounded-ctl border p-3 text-sm ${choice === "new" ? "border-brand-primary bg-brand-accent/8" : "border-line"}`}>
                <label className="flex cursor-pointer items-start gap-3">
                  <input type="radio" name="oc-cust" className="mt-1" checked={choice === "new"} onChange={() => setChoice("new")} />
                  <span className="min-w-0 flex-1">
                    <span className="font-medium">New customer</span>
                    <span className="block text-ink-muted">Created with the email and phone from this request.</span>
                  </span>
                </label>
                {choice === "new" && (
                  <div className="mt-2 pl-7">
                    <label htmlFor="oc-new-cust" className="sr-only">New customer name</label>
                    <input id="oc-new-cust" className="input" value={newName} maxLength={200} onChange={(e) => setNewName(e.target.value)} />
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </fieldset>
      <div>
        <label htmlFor="oc-wh" className="label">Ships from</label>
        <select id="oc-wh" className="input" value={wh} onChange={(e) => setWh(e.target.value)}>
          <option value="">Choose a warehouse</option>
          {warehouses.map((w) => <option key={w.id} value={w.id}>{w.name} ({w.code})</option>)}
        </select>
      </div>
      <fieldset>
        <legend className="label">Lines</legend>
        <ul className="space-y-2">
          {lines.map((l, i) => (
            <li key={i} className="flex flex-wrap items-center gap-2 text-sm">
              <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2">
                <input type="checkbox" checked={l.keep} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, keep: e.target.checked } : x)))} />
                <span className="truncate">{l.label}</span>
              </label>
              <label className="sr-only" htmlFor={`oc-l-${i}`}>Quantity for {l.label}</label>
              <input id={`oc-l-${i}`} className="input num w-24 text-right" inputMode="decimal" value={l.qty} disabled={!l.keep} onChange={(e) => setLines(lines.map((x, j) => (j === i ? { ...x, qty: e.target.value.trim() } : x)))} />
              <span className="w-14 text-ink-muted">{l.unit_code}</span>
            </li>
          ))}
        </ul>
      </fieldset>
      {err && <p role="alert" className="alert-danger">{err}</p>}
      <button className="btn-primary" disabled={busy || !a.orders}>{busy ? "Creating…" : "Create draft order"}</button>
    </form>
  );
}

/* ================================================================ embed */

function Copy({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      className="btn-ghost"
      onClick={() => {
        void navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 2000); }, () => undefined);
      }}
    >
      <Icon name={done ? "check" : "file"} size={16} />{done ? "Copied" : label}
      <span className="sr-only" aria-live="polite">{done ? "Copied to clipboard" : ""}</span>
    </button>
  );
}

function DomainList({ formId, manage, onChange }: { formId: string | null; manage: boolean; onChange?: () => void }) {
  const [list, setList] = useState<Domain[] | null>(null);
  const [host, setHost] = useState("");
  const [err, setErr] = useState("");
  const load = useCallback(() => {
    oc.domains(formId ?? undefined).then(setList, (e) => setErr(errText(e)));
  }, [formId]);
  useEffect(load, [load]);
  const add = async (e: FormEvent) => {
    e.preventDefault();
    const n = normalizeHost(host);
    if ("error" in n) return setErr(n.error);
    try {
      await oc.addDomain(n.host, formId);
      setHost("");
      setErr("");
      load();
      onChange?.();
    } catch (e2) {
      setErr(errText(e2));
    }
  };
  const remove = async (d: Domain) => {
    try {
      await oc.removeDomain(d.id);
      load();
      onChange?.();
    } catch (e2) {
      setErr(errText(e2));
    }
  };
  const mine = (list ?? []).filter((d) => (formId ? d.form_id === formId : d.form_id === null));
  const inherited = formId ? (list ?? []).filter((d) => d.form_id === null) : [];
  return (
    <div className="space-y-3">
      {list === null ? (
        <div className="skeleton h-10 w-full" />
      ) : (
        <ul className="space-y-1.5">
          {inherited.map((d) => (
            <li key={d.id} className="flex items-center gap-2 rounded-ctl bg-subtle px-3 py-2 text-sm"><Icon name="globe" size={16} /><span className="flex-1">{d.host}</span><span className="text-xs text-ink-muted">all forms (Settings)</span></li>
          ))}
          {mine.map((d) => (
            <li key={d.id} className="flex items-center gap-2 rounded-ctl border border-line px-3 py-2 text-sm">
              <Icon name="globe" size={16} /><span className="flex-1">{d.host}</span>
              {manage && <button type="button" className="grid size-9 cursor-pointer place-items-center rounded-full hover:bg-subtle" aria-label={`Remove ${d.host}`} onClick={() => void remove(d)}><Icon name="x" size={16} /></button>}
            </li>
          ))}
          {!inherited.length && !mine.length && <li className="text-sm text-ink-muted">No websites yet. Until you add one, the form only opens as a direct link.</li>}
        </ul>
      )}
      {manage && (
        <form onSubmit={add} className="flex flex-wrap gap-2">
          <label htmlFor={`oc-host-${formId ?? "all"}`} className="sr-only">Website address</label>
          <input id={`oc-host-${formId ?? "all"}`} className="input min-w-0 flex-1" value={host} onChange={(e) => { setHost(e.target.value); setErr(""); }} placeholder="tamishatrading.com or *.example.com" />
          <button className="btn-ghost"><Icon name="plus" size={16} />Allow website</button>
        </form>
      )}
      {err && <p role="alert" className="text-sm text-danger">{err}</p>}
    </div>
  );
}

export function OcEmbedPage() {
  const a = useAccess();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const [forms, setForms] = useState<OcForm[] | null>(null);
  const [info, setInfo] = useState<EmbedInfo | null>(null);
  const [error, setError] = useState("");
  const formId = params.get("form") ?? forms?.find((f) => f.published)?.id ?? forms?.[0]?.id ?? "";
  const form = forms?.find((f) => f.id === formId);

  useEffect(() => {
    if (a.read) oc.forms().then(setForms, (e) => setError(errText(e)));
  }, [a.read]);
  const loadInfo = useCallback(() => {
    if (formId) oc.embed(formId).then(setInfo, (e) => setError(errText(e)));
  }, [formId]);
  useEffect(loadInfo, [loadInfo]);

  const toggleStandalone = async () => {
    if (!form) return;
    const f = await oc.patchForm(form.id, { version: form.version, standalone_link: !form.standalone_link });
    setForms((cur) => (cur ?? []).map((x) => (x.id === f.id ? f : x)));
    loadInfo();
  };

  if (!a.read) return <NoAccess title="Embed & share" />;
  if (error) return <div className="card"><ErrorState title="Couldn't load embed details" detail={error} /></div>;
  if (!forms) return <PageSkeleton label="Loading" />;
  if (!forms.length)
    return (
      <div className="space-y-5">
        <PageHeader title="Embed & share" />
        <div className="card"><EmptyState icon="globe" title="Create a form first" action={<button className="btn-primary" onClick={() => navigate("/online-orders/forms")}>Go to Form builder</button>} /></div>
      </div>
    );

  return (
    <div className="space-y-5">
      <PageHeader
        title="Embed & share"
        subtitle="Put the order form on your WordPress site with one line of code. No plugin and no password in the code."
        actions={
          forms.length > 1 && (
            <>
              <label htmlFor="oc-embed-form" className="sr-only">Form</label>
              <select id="oc-embed-form" className="input w-auto" value={formId} onChange={(e) => setParams({ form: e.target.value }, { replace: true })}>
                {forms.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
              </select>
            </>
          )
        }
      />
      {form && !form.published && (
        <p className="alert-warning">
          “{form.name}” is not published yet, so the code below shows “not available”. <Link className={link} to={`/online-orders/forms/${form.id}`}>Publish it</Link> first.
        </p>
      )}
      {info && form && (
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
          <section className="card space-y-4 p-5 sm:p-6" aria-labelledby="oc-code">
            <CardTitle id="oc-code" title="Website code" action={<Copy text={info.iframe} label="Copy code" />} />
            <p className="text-sm text-ink-muted">The form has no header or footer of its own, so it sits inside your page between your site's header and footer. The short script makes the form as tall as its content (no box that scrolls inside the page) and keeps the cart in view.</p>
            <label htmlFor="oc-iframe" className="sr-only">Iframe code</label>
            <textarea id="oc-iframe" readOnly className="input min-h-36 font-mono text-xs" value={info.iframe} onFocus={(e) => e.currentTarget.select()} />
            <ol className="list-decimal space-y-1 pl-5 text-sm text-ink-muted">{wordpressSteps.map((s) => <li key={s}>{s}</li>)}</ol>
            <details className="text-sm">
              <summary className="cursor-pointer font-medium">Site doesn't allow scripts?</summary>
              <p className="mt-2 text-ink-muted">Use only the iframe: the form then has a fixed height and scrolls inside its box.</p>
              <div className="mt-2 flex items-start gap-2">
                <label htmlFor="oc-iframe-only" className="sr-only">Iframe only</label>
                <textarea id="oc-iframe-only" readOnly className="input min-h-20 flex-1 font-mono text-xs" value={info.iframe_only} onFocus={(e) => e.currentTarget.select()} />
                <Copy text={info.iframe_only} label="Copy" />
              </div>
            </details>
            <p className="text-sm text-ink-muted">Not tested against your live WordPress site from here: after pasting, open the page and send a test order.</p>
          </section>
          <section className="card space-y-4 p-5 sm:p-6" aria-labelledby="oc-link">
            <CardTitle id="oc-link" title="Direct link" action={info.standalone_link ? <Copy text={info.public_url} label="Copy link" /> : undefined} />
            <p className="break-all rounded-ctl bg-subtle px-3 py-2 font-mono text-sm">{info.public_url}</p>
            {a.manage && (
              <label className="flex cursor-pointer items-start gap-3 text-sm">
                <input type="checkbox" className="mt-0.5 size-5" checked={form.standalone_link} onChange={() => void toggleStandalone()} />
                <span>Allow opening the form on its own (for email or social links). When off, it only shows inside your website.</span>
              </label>
            )}
          </section>
          <section className="card space-y-4 p-5 sm:p-6 xl:col-span-2" aria-labelledby="oc-sites">
            <CardTitle id="oc-sites" title="Websites allowed to show this form" />
            <p className="text-sm text-ink-muted">
              Browsers refuse to show the form inside any other site. {info.https_only ? "Only https:// sites are allowed." : "In this development setup, http://localhost is also allowed for testing."}
            </p>
            <DomainList formId={form.id} manage={a.manage} onChange={loadInfo} />
          </section>
        </div>
      )}
    </div>
  );
}

/* ================================================================ settings */

export function OcSettingsPage() {
  const a = useAccess();
  const [s, setS] = useState<OcSettings | null>(null);
  const [msg, setMsg] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (a.read) oc.settings().then(setS, (e) => setMsg({ tone: "error", text: errText(e) }));
  }, [a.read]);
  const set = (p: Partial<OcSettings>) => setS((cur) => (cur ? { ...cur, ...p } : cur));
  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!s) return;
    setBusy(true);
    try {
      setS(await oc.saveSettings(s));
      setMsg({ tone: "ok", text: "Settings saved." });
    } catch (e2) {
      setMsg({ tone: "error", text: errText(e2) });
    } finally {
      setBusy(false);
    }
  };
  if (!a.read) return <NoAccess title="Online Orders settings" />;
  if (!s) return msg ? <Notice msg={msg} /> : <PageSkeleton label="Loading settings" />;
  const ro = !a.manage;
  const num = (v: string, min: number, max: number) => Math.min(max, Math.max(min, Math.round(Number(v) || 0)));

  return (
    <div className="space-y-5">
      <PageHeader title="Online Orders settings" subtitle="Rules for every online order and quote form." />
      <section className="card space-y-4 p-5 sm:p-6" aria-labelledby="oc-sites-all">
        <CardTitle id="oc-sites-all" title="Allowed websites (all forms)" />
        <p className="text-sm text-ink-muted">Forms can be shown inside these sites. Add a site for one form only on Embed & share.</p>
        <DomainList formId={null} manage={a.manage} />
      </section>
      <form onSubmit={save} className="space-y-5">
        <fieldset disabled={ro} className="grid grid-cols-1 gap-5 xl:grid-cols-3">
          <Section title="Notifications">
            <Check checked={s.notify_staff} onChange={(v) => set({ notify_staff: v })} label="Alert staff about new requests" hint="Everyone with Order Connect access gets an in-app alert (also when they were offline)." />
            <Check checked={s.ack_email} onChange={(v) => set({ ack_email: v })} label="Email the customer a receipt" hint="Sent through the outbox after the request is saved. It says the order is not confirmed yet." />
          </Section>
          <Section title="Spam protection">
            <Num id="oc-min" label="Minimum seconds to fill the form" value={s.min_fill_seconds} onChange={(v) => set({ min_fill_seconds: num(v, 0, 120) })} hint="Faster requests are filed as spam (bots). 0 turns this off." />
            <Num id="oc-ip" label="Orders per connection per hour" value={s.ip_limit_per_hour} onChange={(v) => set({ ip_limit_per_hour: num(v, 1, 1000) })} />
            <Num id="oc-form" label="Orders per form per hour" value={s.form_limit_per_hour} onChange={(v) => set({ form_limit_per_hour: num(v, 1, 10000) })} />
            <p className="text-sm text-ink-muted">A hidden “honeypot” field also catches bots. Captcha services are not used (see open decisions).</p>
          </Section>
          <Section title="Privacy & retention">
            <Check checked={s.consent_required} onChange={(v) => set({ consent_required: v })} label="Ask customers to agree to a privacy notice" />
            {s.consent_required && (
              <div>
                <label htmlFor="oc-consent" className="label">Privacy notice text</label>
                <textarea id="oc-consent" className="input min-h-24" maxLength={1000} value={s.consent_text} onChange={(e) => set({ consent_text: e.target.value })} placeholder="I agree that Tamisha Trading Inc. uses my details to handle this order." />
              </div>
            )}
            <div>
              <label htmlFor="oc-ret" className="label">Erase personal data after</label>
              <select id="oc-ret" className="input" value={s.retention_days ?? ""} onChange={(e) => set({ retention_days: e.target.value ? Number(e.target.value) : null })}>
                <option value="">Keep (no automatic erasure)</option>
                {[30, 90, 180, 365, 730, 1095, 2555].map((d) => <option key={d} value={d}>{d < 365 ? `${d} days` : `${Math.round(d / 365)} year${d >= 730 ? "s" : ""}`}</option>)}
              </select>
              <p className="mt-1 text-sm text-ink-muted">Only finished requests (converted, rejected, duplicate, spam). Products and linked orders stay.</p>
            </div>
          </Section>
        </fieldset>
        <Notice msg={msg} />
        {!ro && <button className="btn-primary" disabled={busy}>{busy ? "Saving…" : "Save settings"}</button>}
      </form>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="card space-y-4 p-5 sm:p-6">
      <h2 className="text-lg font-semibold">{title}</h2>
      {children}
    </section>
  );
}
function Check({ checked, onChange, label, hint }: { checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string }) {
  return (
    <label className="flex cursor-pointer items-start gap-3 text-sm">
      <input type="checkbox" className="mt-0.5 size-5" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span><span className="font-medium">{label}</span>{hint && <span className="block text-ink-muted">{hint}</span>}</span>
    </label>
  );
}
function Num({ id, label, value, onChange, hint }: { id: string; label: string; value: number; onChange: (v: string) => void; hint?: string }) {
  return (
    <div>
      <label htmlFor={id} className="label">{label}</label>
      <input id={id} className="input num w-32" inputMode="numeric" value={value} onChange={(e) => onChange(e.target.value)} />
      {hint && <p className="mt-1 text-sm text-ink-muted">{hint}</p>}
    </div>
  );
}
