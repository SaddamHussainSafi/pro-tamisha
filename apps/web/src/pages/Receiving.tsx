import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import {
  api,
  has,
  scanDocumentUrl,
  type LandedCostKind,
  type ScanJob,
  type ShipmentDetail,
  type ShipmentDocKind,
  type Vendor,
  hasCompanyWide, type SupplierBalance } from "../api";
import { useAuth } from "../auth";
import { GmailBrowser } from "../components/gmail/GmailBrowser";
import { Modal } from "../components/gmail/PagePreview";
import { gmail, gmailMessage, type GmailAttachment, type GmailStatus } from "../lib/gmail";
import { amountIn, balanceWords } from "../lib/suppliers";
import { useViewMode } from "../lib/viewMode";
import { CardGrid, CardGridSkeleton, EntityCard, Tile } from "../ui/CardGrid";
import { ViewToggle } from "../ui/ViewToggle";
import { COST_KIND, DOC_KIND, moneyText, receivingMessage } from "../lib/receiving";
import { ACCEPT, checkFile, fileSize } from "../lib/scans";
import { useTrashChanges } from "../lib/trash";
import { DeleteAction } from "../ui/DeleteAction";
import { Icon } from "../ui/Icon";
import { DataGrid, type Column } from "../ui/DataGrid";
import { NumericInput } from "../ui/NumericInput";
import { EmptyState, ErrorState, LoadingRows, PageHeader, StatusChip } from "../ui/primitives";

// Suppliers, plus the supplier-document and landed-cost panels the purchase page uses.
// (The old count-then-approve screens were replaced by pages/Purchases.tsx, owner 2026-11-12.)

const link = "font-medium text-brand-primary-hover underline-offset-2 hover:underline";
const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—";

export function Notice({ tone, children }: { tone: "error" | "ok"; children: React.ReactNode }) {
  return (
    <p role={tone === "error" ? "alert" : "status"} className={`rounded-ctl border px-3 py-2 text-sm ${tone === "error" ? "border-danger/40 bg-danger/5 text-danger" : "border-success/40 bg-success/5 text-ink"}`}>
      {children}
    </p>
  );
}

export type Run = (fn: () => Promise<ShipmentDetail>, done?: string) => Promise<boolean>;

/* ---------------------------------------------------------------- documents */

export function DocumentsStrip({ s, editable, busy, run }: { s: ShipmentDetail; editable: boolean; busy: boolean; run: Run }) {
  const { session } = useAuth();
  const [kind, setKind] = useState<ShipmentDocKind>("INVOICE");
  const [ref, setRef] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [viewing, setViewing] = useState<string | null>(null);
  const [from, setFrom] = useState<"gmail" | "scans" | null>(null);
  const current = s.documents.find((d) => d.id === viewing) ?? null;
  const gmailOk = !!session && has(session, "gmail.read") && has(session, "gmail.attachment.read");

  async function added(p: Promise<ShipmentDetail>, what: string) {
    if (await run(() => p, `${DOC_KIND[kind]} added${what}.`)) {
      setRef("");
      setFrom(null);
    }
  }
  // Show a newly added document straight away.
  const count = s.documents.length;
  const [seen, setSeen] = useState(count);
  useEffect(() => {
    if (count > seen) setViewing(s.documents[count - 1]?.id ?? null);
    setSeen(count);
  }, [count, seen, s.documents]);

  async function onFile(file: File | undefined, input: HTMLInputElement) {
    setMsg(null);
    if (!file) return;
    const bad = checkFile(file);
    input.value = "";
    if (bad) return setMsg(bad);
    await added(api.uploadShipmentDoc(s.id, file, { version: s.version, kind, doc_ref: ref.trim() || undefined }), "");
  }

  return (
    <section className="card min-w-0 overflow-hidden" aria-labelledby="docs-h">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3 sm:px-5">
        <h2 id="docs-h" className="font-semibold">Supplier documents</h2>
        <span className="text-sm text-ink-muted">Kept exactly as received. More can be added at any time.</span>
      </div>
      {s.documents.length > 0 && (
        <div className="flex flex-wrap gap-2 border-t border-line px-4 py-3 sm:px-5" role="list" aria-label="Documents">
          {s.documents.map((d) => (
            <span role="listitem" key={d.id}>
              <button
                type="button"
                aria-expanded={viewing === d.id}
                onClick={() => setViewing(viewing === d.id ? null : d.id)}
                className={`inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-sm ${viewing === d.id ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card hover:bg-subtle"}`}
              >
                <Icon name="file" size={14} />
                {DOC_KIND[d.kind]}{d.doc_ref ? ` ${d.doc_ref}` : ""}
              </button>
            </span>
          ))}
        </div>
      )}
      {current && (
        <div className="border-t border-line bg-canvas">
          <div className="flex flex-wrap gap-x-4 px-4 py-2 text-xs text-ink-muted sm:px-5">
            <span>{current.filename}</span>
            <span>{fileSize(current.byte_size)}</span>
            <span>Added by {current.added_by_name}</span>
            <a className={link} href={scanDocumentUrl(current.document_id)} target="_blank" rel="noreferrer">Open in new tab</a>
          </div>
          {current.mime_type === "application/pdf" ? (
            <iframe title={`Original ${DOC_KIND[current.kind]}`} src={scanDocumentUrl(current.document_id)} className="h-[28rem] w-full border-0 bg-card" />
          ) : current.mime_type === "image/tiff" ? (
            <p className="p-4 text-sm">TIFF files can't be shown in the browser. <a className={link} href={scanDocumentUrl(current.document_id)}>Download it</a>.</p>
          ) : (
            <img alt={`Original ${DOC_KIND[current.kind]}`} src={scanDocumentUrl(current.document_id)} className="mx-auto max-h-[28rem] w-auto" />
          )}
        </div>
      )}
      {editable && (
        <div className="flex flex-wrap items-end gap-2 border-t border-line bg-canvas px-4 py-3 sm:px-5" role="group" aria-label="Add a document">
          <div className="w-full sm:w-48">
            <label className="label" htmlFor="doc-kind">Document type</label>
            <select id="doc-kind" className="input" value={kind} onChange={(e) => setKind(e.target.value as ShipmentDocKind)}>
              {(Object.keys(DOC_KIND) as ShipmentDocKind[]).map((k) => <option key={k} value={k}>{DOC_KIND[k]}</option>)}
            </select>
          </div>
          <div className="w-full sm:w-44">
            <label className="label" htmlFor="doc-ref">Number on it</label>
            <input id="doc-ref" className="input" value={ref} maxLength={64} placeholder="Optional" onChange={(e) => setRef(e.target.value)} />
          </div>
          <div className="flex flex-wrap gap-2">
            <label className={`btn-ghost cursor-pointer gap-2 ${busy ? "pointer-events-none opacity-60" : ""}`}>
              <Icon name="upload" size={16} />
              Upload file
              <input data-testid="doc-upload" type="file" accept={ACCEPT} className="sr-only" onChange={(e) => void onFile(e.target.files?.[0], e.target)} />
            </label>
            {gmailOk && (
              <button type="button" className="btn-ghost gap-2" disabled={busy} onClick={() => setFrom("gmail")}>
                <Icon name="mail" size={16} />
                From Gmail
              </button>
            )}
            <button type="button" className="btn-ghost gap-2" disabled={busy} onClick={() => setFrom("scans")}>
              <Icon name="scan" size={16} />
              From Scan Center
            </button>
          </div>
          {msg && <div className="w-full"><Notice tone="error">{msg}</Notice></div>}
        </div>
      )}
      {from === "gmail" && <GmailDocPicker onClose={() => setFrom(null)} onPick={(a) => void added(api.attachShipmentGmail(s.id, { version: s.version, attachment_id: a.id, kind, doc_ref: ref.trim() || null }), ` from Gmail`)} />}
      {from === "scans" && <ScanDocPicker s={s} onClose={() => setFrom(null)} onPick={(j) => void added(api.attachShipmentScan(s.id, { version: s.version, scan_job_id: j.id, kind, doc_ref: ref.trim() || null }), ` from the Scan Center`)} />}
    </section>
  );
}

function GmailDocPicker({ onClose, onPick }: { onClose: () => void; onPick: (a: GmailAttachment) => void }) {
  const [status, setStatus] = useState<GmailStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    gmail.status().then(setStatus, (err: unknown) => setError(gmailMessage(err)));
  }, []);
  return (
    <Modal open title="Attach a document from Gmail" onClose={onClose} wide>
      <div className="p-5">
        {error ? (
          <ErrorState title="Couldn't reach the Scan Box" detail={error} />
        ) : !status ? (
          <LoadingRows rows={4} cols={2} />
        ) : status.message_count === 0 ? (
          <EmptyState icon="mail" title="No emails on file" hint="Documents sent to the scanning mailbox show here once it is connected." />
        ) : (
          <GmailBrowser status={status} mode="picker" initialShow="attachments" onProcessed={() => undefined} onSelect={onPick} selectLabel="Attach to purchase" />
        )}
      </div>
    </Modal>
  );
}

function ScanDocPicker({ s, onClose, onPick }: { s: ShipmentDetail; onClose: () => void; onPick: (j: ScanJob) => void }) {
  const [rows, setRows] = useState<ScanJob[] | null>(null);
  const [q, setQ] = useState("");
  const [error, setError] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => {
      api.scanJobs({ status: "UPLOADED", warehouse_id: s.warehouse_id, q: q.trim() || undefined, limit: 50 }).then(
        (p) => {
          setRows(p.items.filter((j) => j.document_id));
          setError(false);
        },
        () => setError(true),
      );
    }, 200);
    return () => clearTimeout(t);
  }, [q, s.warehouse_id]);
  const onShipment = new Set(s.documents.map((d) => d.document_id));
  return (
    <Modal open title="Attach a document from the Scan Center" onClose={onClose}>
      <div className="space-y-3 p-5">
        <label className="relative block">
          <span className="sr-only">Search scans</span>
          <Icon name="search" size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-ink-muted" />
          <input type="search" className="input w-full rounded-full py-2 pl-10" placeholder="Search scan no., file or note" value={q} onChange={(e) => setQ(e.target.value)} />
        </label>
        {error ? (
          <ErrorState title="Couldn't load scans" />
        ) : rows === null ? (
          <LoadingRows rows={4} cols={2} />
        ) : rows.length === 0 ? (
          <EmptyState icon="scan" title="No scanned documents" hint="Scan the paper in the Scan Center first, or upload the file here instead." />
        ) : (
          <ul className="divide-y divide-line rounded-ctl border border-line" aria-label="Scanned documents">
            {rows.map((j) => {
              const already = !!j.document_id && onShipment.has(j.document_id);
              return (
                <li key={j.id} className="flex items-center gap-3 px-3 py-2.5">
                  <span aria-hidden className="grid size-9 shrink-0 place-items-center rounded-ctl bg-subtle text-ink-muted"><Icon name="file" size={18} /></span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">{j.document_filename ?? j.job_no}</span>
                    <span className="block truncate text-xs text-ink-muted">{j.job_no} · {when(j.completed_at ?? j.created_at)}{j.note ? ` · ${j.note}` : ""}</span>
                  </span>
                  <button type="button" className="btn-ghost px-3.5 py-1.5 text-xs" disabled={already} onClick={() => onPick(j)} aria-label={`Attach ${j.document_filename ?? j.job_no}`}>
                    {already ? "Attached" : "Attach"}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Modal>
  );
}

/* ---------------------------------------------------------------- costs */

export function CostSection({ s, editable, busy, run, docName, onWithdraw }: { s: ShipmentDetail; editable: boolean; busy: boolean; run: Run; docName: (id: string | null) => string; onWithdraw: (id: string) => void }) {
  const [kind, setKind] = useState<LandedCostKind>("FREIGHT");
  const [amount, setAmount] = useState("");
  const [cur, setCur] = useState(s.currency);
  const [docId, setDocId] = useState("");
  const [note, setNote] = useState("");
  const live = s.costs.filter((c) => !c.removed_at);
  const gone = s.costs.filter((c) => c.removed_at);
  const totals = Object.entries(s.cost_totals);
  if (!editable && live.length === 0 && gone.length === 0) return null;

  async function submit(e: FormEvent) {
    e.preventDefault();
    const ok = await run(
      () => api.addLandedCost(s.id, { version: s.version, kind, amount, currency: cur.trim().toUpperCase(), shipment_document_id: docId || null, note: note.trim() || null }),
      `${COST_KIND[kind]} recorded.`,
    );
    if (ok) {
      setAmount("");
      setNote("");
    }
  }

  return (
    <details className="card group/cost min-w-0 overflow-hidden">
      <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 sm:px-5 [&::-webkit-details-marker]:hidden">
        <Icon name="chevron" size={16} className="transition-transform group-open/cost:rotate-90" />
        <h2 className="font-semibold">Freight, duty and other costs</h2>
        <span className="mr-auto text-sm text-ink-muted">{live.length === 0 ? "Optional" : `${live.length} recorded`}</span>
        {totals.length > 0 && <span className="num text-sm font-semibold">{totals.map(([c, a]) => moneyText(a, c)).join(" + ")}</span>}
      </summary>
      <div className="border-t border-line">
        <p className="px-4 pt-3 text-xs text-ink-muted sm:px-5">Recorded against this airport shipment only. They are paid to other parties, stay out of the supplier's balance, and are not spread over product costs until the allocation rule is decided.</p>
        {live.length > 0 && (
          <ul className="divide-y divide-line">
            {live.map((c) => (
              <li key={c.id} className="flex flex-wrap items-center gap-x-4 px-4 py-2 text-sm sm:px-5">
                <span className="flex-1">
                  {COST_KIND[c.kind]}
                  <span className="block text-xs text-ink-muted">{docName(c.shipment_document_id)}{c.note ? ` · ${c.note}` : ""} · {c.created_by_name}</span>
                </span>
                <span className="num">{moneyText(c.amount, c.currency)}</span>
                {editable && <button className="btn-ghost px-3 py-1.5 text-xs" aria-label={`Withdraw ${COST_KIND[c.kind]} cost`} onClick={() => onWithdraw(c.id)}>Withdraw</button>}
              </li>
            ))}
          </ul>
        )}
        {gone.length > 0 && (
          <details className="border-t border-line px-4 py-2 text-xs text-ink-muted sm:px-5">
            <summary>{gone.length} withdrawn cost{gone.length === 1 ? "" : "s"}</summary>
            <ul className="mt-1">{gone.map((c) => <li key={c.id}>{COST_KIND[c.kind]} {moneyText(c.amount, c.currency)} — “{c.removed_reason}”</li>)}</ul>
          </details>
        )}
        {editable && (
          <form className="mt-2 grid gap-2 border-t border-line bg-canvas p-4 sm:px-5 md:grid-cols-[repeat(4,minmax(0,1fr))_minmax(0,2fr)_auto] md:items-end" aria-label="Add a cost" onSubmit={submit}>
            <div>
              <label className="label" htmlFor="lc-k">Cost type</label>
              <select id="lc-k" className="input" value={kind} onChange={(e) => setKind(e.target.value as LandedCostKind)}>
                {(Object.keys(COST_KIND) as LandedCostKind[]).map((k) => <option key={k} value={k}>{COST_KIND[k]}</option>)}
              </select>
            </div>
            <NumericInput label="Amount" value={amount} onChange={setAmount} places={2} />
            <div>
              <label className="label" htmlFor="lc-c">Currency</label>
              <input id="lc-c" className="input uppercase" value={cur} maxLength={3} onChange={(e) => setCur(e.target.value)} />
            </div>
            <div>
              <label className="label" htmlFor="lc-d">From document</label>
              <select id="lc-d" className="input" value={docId} onChange={(e) => setDocId(e.target.value)}>
                <option value="">No document</option>
                {s.documents.map((d) => <option key={d.id} value={d.id}>{docName(d.id)}</option>)}
              </select>
            </div>
            <div>
              <label className="label" htmlFor="lc-n">Note</label>
              <input id="lc-n" className="input" value={note} maxLength={500} placeholder="Optional" onChange={(e) => setNote(e.target.value)} />
            </div>
            <button className="btn-primary" disabled={busy || !amount || cur.trim().length !== 3}>Add cost</button>
          </form>
        )}
      </div>
    </details>
  );
}

/* ================================ Suppliers ================================ */

export function SuppliersPage() {
  const { session } = useAuth();
  const [rows, setRows] = useState<Vendor[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [country, setCountry] = useState("");
  const [email, setEmail] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [editing, setEditing] = useState<Vendor | null>(null);

  const [balances, setBalances] = useState<Record<string, SupplierBalance>>({});
  const canLedger = !!session && hasCompanyWide(session, "supplier.ledger.read");
  const [view, setView] = useViewMode("suppliers");
  const load = useCallback(async () => {
    try {
      setRows(await api.vendors());
      setLoadError(false);
    } catch {
      setLoadError(true);
    }
    if (canLedger) api.supplierBalances().then((b) => setBalances(Object.fromEntries(b.map((x) => [x.vendor_id, x]))), () => setBalances({}));
  }, [canLedger]);
  useEffect(() => void load(), [load]);
  useTrashChanges(load);
  if (!session) return null;
  const canManage = has(session, "catalog.manage");
  const deleted = (v: Vendor) => {
    setEditing((e) => (e?.id === v.id ? null : e));
    void load();
  };

  async function create(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setOk(null);
    try {
      const v = await api.createVendor({ code: code.trim().toUpperCase(), name: name.trim(), country_code: country.trim().toUpperCase() || null, email: email.trim() || null, currency: currency.trim().toUpperCase() });
      setOk(`${v.name} added.`);
      setCode("");
      setName("");
      setCountry("");
      setEmail("");
      await load();
    } catch (err) {
      setError(receivingMessage(err));
    }
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!editing) return;
    setError(null);
    try {
      await api.updateVendor(editing.id, {
        name: editing.name.trim(),
        country_code: editing.country_code?.trim().toUpperCase() || null,
        email: editing.email?.trim() || null,
        currency: editing.currency.trim().toUpperCase(),
        active: editing.active,
        version: editing.version,
      });
      setEditing(null);
      setOk("Supplier saved.");
      await load();
    } catch (err) {
      setError(receivingMessage(err));
      await load();
    }
  }

  const columns: Column<Vendor>[] = [
    { key: "code", header: "Code", cell: (v) => <span className="font-medium">{v.code}</span>, sort: (v) => v.code },
    {
      key: "name",
      header: "Name",
      cell: (v) =>
        canLedger ? (
          <Link className="font-medium text-brand-primary hover:underline" to={`/suppliers/${v.id}`}>
            {v.name}
          </Link>
        ) : (
          v.name
        ),
      sort: (v) => v.name,
    },
    { key: "country", header: "Country", cell: (v) => v.country_code ?? "—" },
    { key: "cur", header: "Currency", cell: (v) => v.currency },
    { key: "email", header: "Email", cell: (v) => v.email ?? "—" },
    ...(canLedger
      ? [
          {
            key: "bal",
            header: "Balance",
            align: "right" as const,
            cell: (v: Vendor) => {
              const tot = balances[v.id]?.totals ?? [];
              return tot.length ? <span className="num whitespace-nowrap">{tot.map((x) => amountIn(x.currency, x.balance)).join(" · ")}</span> : <span className="text-ink-muted">—</span>;
            },
          },
        ]
      : []),
    { key: "active", header: "Status", cell: (v) => (v.active ? <StatusChip tone="success">Active</StatusChip> : <StatusChip tone="neutral">Inactive</StatusChip>) },
    ...(canManage ? [{ key: "edit", header: "Actions", cell: (v: Vendor) => <button className="btn-ghost text-xs" aria-label={`Edit ${v.name}`} onClick={() => setEditing({ ...v })}>Edit</button> }] : []),
    { key: "delete", header: "", align: "right", cell: (v) => <DeleteAction compact kind="supplier" id={v.id} label={v.name} onDone={() => deleted(v)} /> },
  ];

  return (
    <div className="space-y-5">
      <PageHeader title="Suppliers" subtitle="Who you buy from. Their document descriptions are remembered per supplier." actions={<ViewToggle value={view} onChange={setView} />} />
      {error && <Notice tone="error">{error}</Notice>}
      {ok && <Notice tone="ok">{ok}</Notice>}
      {canManage && (
        <form className="card grid gap-3 p-6 md:grid-cols-6 md:items-end" aria-label="Add a supplier" onSubmit={create}>
          <div>
            <label className="label" htmlFor="v-code">Code</label>
            <input id="v-code" className="input uppercase" value={code} maxLength={32} required pattern="[A-Za-z0-9_\-]+" onChange={(e) => setCode(e.target.value)} />
          </div>
          <div className="md:col-span-2">
            <label className="label" htmlFor="v-name">Name</label>
            <input id="v-name" className="input" value={name} maxLength={200} required onChange={(e) => setName(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="v-cc">Country (2 letters)</label>
            <input id="v-cc" className="input uppercase" value={country} maxLength={2} onChange={(e) => setCountry(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="v-cur">Currency</label>
            <input id="v-cur" className="input uppercase" value={currency} maxLength={3} required onChange={(e) => setCurrency(e.target.value)} />
          </div>
          <button className="btn-primary">Add supplier</button>
          <div className="md:col-span-3">
            <label className="label" htmlFor="v-email">Email (optional)</label>
            <input id="v-email" type="email" className="input" value={email} maxLength={320} onChange={(e) => setEmail(e.target.value)} />
          </div>
        </form>
      )}
      {loadError ? (
        <div className="card">
          <ErrorState title="Couldn't load suppliers" onRetry={() => void load()} />
        </div>
      ) : rows === null ? (
        view === "grid" ? (
          <CardGridSkeleton />
        ) : (
          <div className="card overflow-clip">
            <LoadingRows cols={6} />
          </div>
        )
      ) : view === "grid" ? (
        <CardGrid
          items={rows}
          keyOf={(v) => v.id}
          caption="Suppliers"
          empty={<EmptyState title="No suppliers yet" />}
          render={(v) => {
            const b = balances[v.id];
            return (
              <EntityCard
                to={canLedger ? `/suppliers/${v.id}` : undefined}
                title={v.name}
                subtitle={[v.code, v.country_code, `pays in ${v.currency}`].filter(Boolean).join(" · ")}
                media={<Tile text={v.name} muted={!v.active} />}
                dim={!v.active}
                facts={[
                  ...(canLedger && b
                    ? [
                        ...(b.totals.length ? b.totals : [{ currency: v.currency, balance: "0.00" }]).map((t) => ({ label: `Balance ${t.currency}`, value: <span className="num font-semibold">{balanceWords(t.currency, t.balance)}</span> })),
                        { label: "Shipments", value: <span className="num">{b.shipments}</span> },
                        { label: "Last received", value: b.last_received_at ? new Date(b.last_received_at).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "—" },
                      ]
                    : []),
                  { label: "Email", value: v.email ?? <span className="text-ink-muted">—</span> },
                ]}
                chips={v.active ? <StatusChip tone="success">Active</StatusChip> : <StatusChip tone="neutral">Inactive</StatusChip>}
                actions={
                  <>
                    {canManage && (
                      <button className="btn-ghost px-3 py-1.5 text-xs" aria-label={`Edit ${v.name}`} onClick={() => setEditing({ ...v })}>
                        Edit
                      </button>
                    )}
                    <DeleteAction compact kind="supplier" id={v.id} label={v.name} onDone={() => deleted(v)} />
                  </>
                }
              />
            );
          }}
        />
      ) : (
        <section className="card overflow-clip">
          <DataGrid rows={rows} columns={columns} rowKey={(v) => v.id} caption="Suppliers" empty={<EmptyState title="No suppliers yet" />} />
        </section>
      )}
      {editing && (
        <form className="card grid gap-3 p-6 md:grid-cols-5 md:items-end" aria-label={`Edit ${editing.code}`} onSubmit={save}>
          <h2 className="font-semibold md:col-span-5">Edit {editing.code}</h2>
          <div className="md:col-span-2">
            <label className="label" htmlFor="ve-name">Name</label>
            <input id="ve-name" className="input" value={editing.name} required onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
          </div>
          <div>
            <label className="label" htmlFor="ve-cc">Country</label>
            <input id="ve-cc" className="input uppercase" value={editing.country_code ?? ""} maxLength={2} onChange={(e) => setEditing({ ...editing, country_code: e.target.value })} />
          </div>
          <div>
            <label className="label" htmlFor="ve-cur">Currency</label>
            <input id="ve-cur" className="input uppercase" value={editing.currency} maxLength={3} onChange={(e) => setEditing({ ...editing, currency: e.target.value })} />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={editing.active} onChange={(e) => setEditing({ ...editing, active: e.target.checked })} /> Active
          </label>
          <div className="md:col-span-3">
            <label className="label" htmlFor="ve-email">Email</label>
            <input id="ve-email" type="email" className="input" value={editing.email ?? ""} onChange={(e) => setEditing({ ...editing, email: e.target.value })} />
          </div>
          <div className="flex justify-end gap-2 md:col-span-2">
            <button type="button" className="btn-ghost" onClick={() => setEditing(null)}>Cancel</button>
            <button className="btn-primary">Save</button>
          </div>
        </form>
      )}
    </div>
  );
}
