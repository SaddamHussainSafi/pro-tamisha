import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  api,
  canInWarehouse,
  has,
  scanDocumentUrl,
  type LandedCostKind,
  type Product,
  type ProductSummary,
  type Shipment,
  type ShipmentDetail,
  type ShipmentDocKind,
  type UnitCode,
  type Vendor,
  type Warehouse, hasCompanyWide, type SupplierBalance } from "../api";
import { useAuth } from "../auth";
import { amountIn, balanceWords } from "../lib/suppliers";
import { useViewMode } from "../lib/viewMode";
import { CardGrid, EntityCard, Tile } from "../ui/CardGrid";
import { ViewToggle } from "../ui/ViewToggle";
import { stockUnits, qtyText } from "../lib/inventory";
import { COMPARE, COST_KIND, countTotal, diffText, DOC_KIND, moneyText, receivingMessage, saleableSummary, SHIPMENT_STATUS, unitPlaces } from "../lib/receiving";
import { ACCEPT, checkFile, fileSize } from "../lib/scans";
import { Icon } from "../ui/Icon";
import { DataGrid, type Column } from "../ui/DataGrid";
import { NumericInput } from "../ui/NumericInput";
import { ConfirmDialog, EmptyState, ErrorState, FilterBar, LoadingRows, PageHeader, StatusChip } from "../ui/primitives";
import { ReasonDialog } from "./Orders";

const link = "font-medium text-brand-primary-hover underline-offset-2 hover:underline";
const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—";
const day = (iso: string | null) => (iso ? iso.slice(0, 10) : "—");
const ShipChip = ({ s }: { s: Pick<Shipment, "status"> }) => <StatusChip tone={SHIPMENT_STATUS[s.status].tone}>{SHIPMENT_STATUS[s.status].label}</StatusChip>;

function Notice({ tone, children }: { tone: "error" | "ok"; children: React.ReactNode }) {
  return (
    <p role={tone === "error" ? "alert" : "status"} className={`rounded-ctl border px-3 py-2 text-sm ${tone === "error" ? "border-danger/40 bg-danger/5 text-danger" : "border-success/40 bg-success/5 text-ink"}`}>
      {children}
    </p>
  );
}

/** Products with their unit list, fetched on demand (unit list needs the full product). */
function useCatalog() {
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [full, setFull] = useState<Record<string, Product>>({});
  useEffect(() => {
    api.products({ active: true, limit: 200 }).then((p) => setProducts(p.items), () => setProducts([]));
  }, []);
  const load = useCallback(async (id: string) => {
    if (!id || full[id]) return full[id];
    const p = await api.product(id);
    setFull((f) => ({ ...f, [id]: p }));
    return p;
  }, [full]);
  const units = (id: string): UnitCode[] => (full[id] ? stockUnits(full[id]) : []);
  return { products, full, load, units };
}

/* ================================ List ================================ */

type View = "active" | "COUNTED" | "POSTED" | "CANCELLED" | "all";
const VIEWS: { value: View; label: string }[] = [
  { value: "active", label: "In progress" },
  { value: "COUNTED", label: "Waiting for approval" },
  { value: "POSTED", label: "Posted" },
  { value: "CANCELLED", label: "Cancelled" },
  { value: "all", label: "All" },
];
const VIEW_STATUS: Record<View, string | undefined> = { active: "OPEN,COUNTED", COUNTED: "COUNTED", POSTED: "POSTED", CANCELLED: "CANCELLED", all: undefined };

export function ReceivingPage() {
  const { session } = useAuth();
  const nav = useNavigate();
  const [view, setView] = useState<View>("active");
  const [search, setSearch] = useState("");
  const [rows, setRows] = useState<Shipment[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [creating, setCreating] = useState(false);
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);

  const load = useCallback(async () => {
    try {
      const p = await api.shipments({ status: VIEW_STATUS[view], q: search.trim() || undefined, limit: 100 });
      setRows(p.items);
      setLoadError(false);
    } catch {
      setLoadError(true);
    }
  }, [view, search]);
  useEffect(() => {
    const t = setTimeout(() => void load(), 200);
    return () => clearTimeout(t);
  }, [load]);
  useEffect(() => {
    api.warehouses().then((w) => setWarehouses(w.filter((x) => x.active)), () => setWarehouses([]));
  }, []);

  if (!session) return null;
  const recordable = warehouses.filter((w) => canInWarehouse(session, "grn.record", w.id));

  const columns: Column<Shipment>[] = [
    { key: "no", header: "Shipment", cell: (s) => <Link className={link} to={`/receiving/${s.id}`}>{s.shipment_no}</Link>, sort: (s) => s.shipment_no },
    { key: "vendor", header: "Supplier", cell: (s) => <span>{s.vendor_name} <span className="text-xs text-ink-muted">{s.vendor_code}</span></span>, sort: (s) => s.vendor_name },
    { key: "refs", header: "Invoice / waybill", cell: (s) => <span className="text-sm">{[s.vendor_invoice_no, s.awb_no].filter(Boolean).join(" · ") || "—"}</span> },
    { key: "docs", header: "Docs", align: "right", cell: (s) => <span className="num">{s.document_count}</span> },
    {
      key: "diff",
      header: "Differences",
      cell: (s) => (s.discrepancy_count > 0 ? <StatusChip tone="warning">{s.discrepancy_count} to explain</StatusChip> : <span className="text-ink-muted">None</span>),
    },
    { key: "status", header: "Status", cell: (s) => <ShipChip s={s} />, sort: (s) => s.status },
    { key: "created", header: "Created", cell: (s) => when(s.created_at), sort: (s) => s.created_at },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title="Receiving"
        subtitle="Check supplier shipments against their documents. Stock is added only when a manager approves the count."
        actions={recordable.length > 0 && !creating && <button className="btn-primary" onClick={() => setCreating(true)}>New shipment</button>}
      />
      {creating && <NewShipment warehouses={recordable} onCancel={() => setCreating(false)} onCreated={(s) => nav(`/receiving/${s.id}`)} />}
      <FilterBar search={search} onSearch={setSearch} options={VIEWS} value={view} onChange={setView} placeholder="Search shipment, invoice, waybill" />
      {loadError ? (
        <ErrorState title="Couldn't load shipments" onRetry={() => void load()} />
      ) : rows === null ? (
        <LoadingRows cols={8} />
      ) : (
        <DataGrid
          rows={rows}
          columns={columns}
          rowKey={(s) => s.id}
          caption="Supplier shipments"
          onOpen={(s) => nav(`/receiving/${s.id}`)}
          empty={<EmptyState title="No shipments here" hint={view === "active" ? "Start one with New shipment when a delivery arrives." : undefined} />}
        />
      )}
    </div>
  );
}

function NewShipment({ warehouses, onCancel, onCreated }: { warehouses: Warehouse[]; onCancel: () => void; onCreated: (s: ShipmentDetail) => void }) {
  const [vendors, setVendors] = useState<Vendor[]>([]);
  // No warehouse field: shipments are received at the first warehouse this user may record in.
  const wh = warehouses[0]?.id ?? "";
  const [vendor, setVendor] = useState("");
  const [inv, setInv] = useState("");
  const [awb, setAwb] = useState("");
  const [expected, setExpected] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.vendors({ active: true }).then(setVendors, () => setVendors([]));
  }, []);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!vendor) return setError("Choose the supplier.");
    setBusy(true);
    try {
      onCreated(
        await api.createShipment({
          warehouse_id: wh,
          vendor_id: vendor,
          vendor_invoice_no: inv.trim() || null,
          awb_no: awb.trim() || null,
          expected_on: expected || null,
          note: note.trim() || null,
        }),
      );
    } catch (err) {
      setError(receivingMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="card grid gap-3 p-6 md:grid-cols-3" aria-label="New shipment" onSubmit={submit}>
      <h2 className="font-semibold md:col-span-3">New shipment</h2>
      <div>
        <label className="label" htmlFor="ns-v">Supplier</label>
        <select id="ns-v" className="input" value={vendor} onChange={(e) => setVendor(e.target.value)}>
          <option value="">Choose…</option>
          {vendors.map((v) => <option key={v.id} value={v.id}>{v.name} ({v.code})</option>)}
        </select>
      </div>
      <div>
        <label className="label" htmlFor="ns-exp">Expected on (optional)</label>
        <input id="ns-exp" type="date" className="input" value={expected} onChange={(e) => setExpected(e.target.value)} />
      </div>
      <div>
        <label className="label" htmlFor="ns-inv">Supplier invoice no. (optional)</label>
        <input id="ns-inv" className="input" value={inv} maxLength={64} onChange={(e) => setInv(e.target.value)} />
      </div>
      <div>
        <label className="label" htmlFor="ns-awb">Airway bill no. (optional)</label>
        <input id="ns-awb" className="input" value={awb} maxLength={64} onChange={(e) => setAwb(e.target.value)} />
      </div>
      <div>
        <label className="label" htmlFor="ns-note">Note (optional)</label>
        <input id="ns-note" className="input" value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} />
      </div>
      {error && <div className="md:col-span-3"><Notice tone="error">{error}</Notice></div>}
      <div className="flex justify-end gap-2 md:col-span-3">
        <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
        <button className="btn-primary" disabled={busy}>Create shipment</button>
      </div>
    </form>
  );
}

/* ================================ Detail ================================ */

type Dialog =
  | { kind: "withdrawDeclared"; id: string }
  | { kind: "withdrawCost"; id: string }
  | { kind: "reopen" }
  | { kind: "cancel" }
  | { kind: "post" }
  | null;

export function ShipmentPage() {
  const { id = "" } = useParams();
  const { session } = useAuth();
  const [s, setS] = useState<ShipmentDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [doc, setDoc] = useState(0);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [reason, setReason] = useState("");
  const [ack, setAck] = useState(false);
  const [ackNote, setAckNote] = useState("");
  const catalog = useCatalog();

  const load = useCallback(async () => {
    try {
      setS(await api.shipment(id));
      setLoadError(null);
    } catch (err) {
      setLoadError(receivingMessage(err));
    }
  }, [id]);
  useEffect(() => void load(), [load]);

  /** Run a mutation; on success show the returned shipment, on conflict reload. */
  const run = useCallback(
    async (fn: () => Promise<ShipmentDetail>, done?: string) => {
      setError(null);
      setOk(null);
      setBusy(true);
      try {
        setS(await fn());
        if (done) setOk(done);
        return true;
      } catch (err) {
        setError(receivingMessage(err));
        if (err instanceof Error && "code" in err && (err.code === "VERSION_CONFLICT" || err.code === "INVALID_STATE")) await load();
        return false;
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  if (!session) return null;
  if (loadError) return <ErrorState title="Couldn't load this shipment" detail={loadError} onRetry={() => void load()} />;
  if (!s) return <LoadingRows cols={5} />;

  const canRecord = canInWarehouse(session, "grn.record", s.warehouse_id);
  const canApprove = canInWarehouse(session, "grn.approve", s.warehouse_id);
  const open = s.status === "OPEN";
  const editable = open && canRecord;
  const costsEditable = s.status !== "CANCELLED" && (canRecord || canApprove);
  const current = s.documents[doc] ?? s.documents[0];
  const differences = s.comparison.filter((r) => r.status !== "MATCH");
  const docName = (docId: string | null) => {
    const d = s.documents.find((x) => x.id === docId);
    return d ? `${DOC_KIND[d.kind]}${d.doc_ref ? ` ${d.doc_ref}` : ""}` : "No document";
  };

  async function confirmDialog() {
    if (!s || !dialog) return;
    const r = reason.trim();
    let done = false;
    if (dialog.kind === "withdrawDeclared") done = await run(() => api.withdrawDeclared(s.id, dialog.id, s.version, r), "Line withdrawn. It stays on record.");
    else if (dialog.kind === "withdrawCost") done = await run(() => api.withdrawLandedCost(s.id, dialog.id, s.version, r), "Cost withdrawn. It stays on record.");
    else if (dialog.kind === "reopen") done = await run(() => api.reopenShipment(s.id, s.version, r), "Count reopened for changes.");
    else if (dialog.kind === "cancel") done = await run(() => api.cancelShipment(s.id, s.version, r), "Shipment cancelled. No stock was changed.");
    if (done) {
      setDialog(null);
      setReason("");
    }
  }

  async function post() {
    if (!s) return;
    const done = await run(
      () => api.postShipment(s.id, { version: s.version, acknowledge_discrepancies: ack, discrepancy_note: ackNote.trim() || null }),
      "Approved. Accepted stock is now available; damaged goods were put aside.",
    );
    if (done) setDialog(null);
  }

  return (
    <div className="space-y-4">
      <PageHeader
        title={`Receive ${s.shipment_no}`}
        subtitle={
          <>
            {s.vendor_name}
            {s.vendor_invoice_no && <> · Invoice {s.vendor_invoice_no}</>}
            {s.awb_no && <> · Waybill {s.awb_no}</>}
            {s.expected_on && <> · Expected {day(s.expected_on)}</>} · {s.currency}
          </>
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <ShipChip s={s} />
            {open && canRecord && (
              <button className="btn-primary" disabled={busy || s.counted.length === 0} onClick={() => void run(() => api.submitShipmentCount(s.id, s.version), "Count sent for approval.")}>
                Send count for approval
              </button>
            )}
            {s.status === "COUNTED" && canRecord && <button className="btn-ghost" onClick={() => setDialog({ kind: "reopen" })}>Reopen count</button>}
            {s.status === "COUNTED" && canApprove && <button className="btn-primary" onClick={() => setDialog({ kind: "post" })}>Approve &amp; post stock</button>}
            {(open || s.status === "COUNTED") && canApprove && <button className="btn-ghost" onClick={() => setDialog({ kind: "cancel" })}>Cancel shipment</button>}
          </div>
        }
      />
      <p className="text-sm"><Link className={link} to="/receiving">← All shipments</Link></p>
      {error && <Notice tone="error">{error}</Notice>}
      {ok && <Notice tone="ok">{ok}</Notice>}
      {s.status === "POSTED" && (
        <Notice tone="ok">
          {s.grn_no} posted {when(s.posted_at)} by {s.posted_by_name}. {s.discrepancy_note && <>Difference note: “{s.discrepancy_note}”</>}
        </Notice>
      )}
      {s.status === "CANCELLED" && <Notice tone="error">Cancelled: {s.cancel_reason}. No stock was changed.</Notice>}
      {s.status === "COUNTED" && !canApprove && <Notice tone="ok">Count submitted by {s.counted_by_name}. A manager will approve it before stock changes.</Notice>}

      {/* ---------- comparison ---------- */}
      <section className="card min-w-0 overflow-hidden" aria-labelledby="cmp-h">
        <div className="flex flex-wrap items-center gap-4 border-b border-line p-5 sm:p-6">
          <span className="grid size-14 shrink-0 place-items-center rounded-ctl bg-brand-primary text-white"><Icon name="receiving" size={26} /></span>
          <div className="mr-auto min-w-0">
            <h2 id="cmp-h" className="text-[22px] font-semibold tracking-tight">Documents vs. what arrived</h2>
            <p className="text-sm text-ink-muted">Declared on the supplier documents, compared with your count.</p>
          </div>
          {s.comparison.length === 0 ? null : differences.length > 0 ? (
            <StatusChip tone="warning">{differences.length} difference{differences.length === 1 ? "" : "s"}</StatusChip>
          ) : (
            <StatusChip tone="success">Everything matches</StatusChip>
          )}
        </div>
        {s.comparison.length === 0 ? (
          <p className="px-4 py-3 text-sm text-ink-muted">Enter the document lines and your count below. The comparison appears here.</p>
        ) : (
          <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table">
            <table className="w-full min-w-[40rem] border-separate border-spacing-0">
              <caption className="sr-only">Declared versus counted, per product and unit</caption>
              <thead>
                <tr>
                  <th className="th">Product</th>
                  <th className="th text-right">Declared</th>
                  <th className="th text-right">Accepted</th>
                  <th className="th text-right">Damaged</th>
                  <th className="th text-right">Refused</th>
                  <th className="th text-right">Difference</th>
                  <th className="th">Result</th>
                </tr>
              </thead>
              <tbody>
                {s.comparison.map((r) => {
                  const c = COMPARE[r.status];
                  const docs = Object.entries(r.declared_by_document);
                  return (
                    <tr key={`${r.product_id}-${r.unit_code}`} className={`align-middle ${r.status === "MATCH" ? "" : "bg-danger/[0.04]"}`}>
                      <td className="td min-w-[14rem]">
                        <div className="flex items-center gap-3">
                          <span className="grid size-12 shrink-0 place-items-center rounded-ctl border border-line bg-subtle text-ink-muted"><Icon name="inventory" size={22} /></span>
                          <div className="min-w-0">
                            <div className="text-base font-semibold">{r.product_name}</div>
                            <div className="text-sm text-ink-muted">{r.sku} · {r.unit_code.toLowerCase()}</div>
                          </div>
                        </div>
                        {r.status === "DOC_CONFLICT" && (
                          <ul className="mt-1 text-xs text-danger">
                            {docs.map(([d, q]) => <li key={d}>{docName(d === "manual" ? null : d)}: {qtyText(q)}</li>)}
                          </ul>
                        )}
                      </td>
                      <td className="td num text-right">{r.declared_qty ? qtyText(r.declared_qty) : "—"}</td>
                      <td className="td num text-right font-semibold">{r.accepted_qty ? qtyText(r.accepted_qty) : "—"}</td>
                      <td className="td num text-right">{r.damaged_qty ? qtyText(r.damaged_qty) : "—"}</td>
                      <td className="td num text-right">{r.rejected_qty ? qtyText(r.rejected_qty) : "—"}</td>
                      <td className="td num text-right">{diffText(r.difference, r.unit_code)}</td>
                      <td className="td">
                        <StatusChip tone={c.tone}>{c.label}</StatusChip>
                        <div className="mt-1 text-xs text-ink-muted">{c.hint}</div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        {/* ---------- documents ---------- */}
        <section className="card flex min-w-0 flex-col overflow-hidden" aria-labelledby="docs-h">
          <div className="flex items-center gap-2 border-b border-line px-4 py-3">
            <h2 id="docs-h" className="mr-auto font-semibold">Supplier documents</h2>
            <span className="text-xs text-ink-muted">Kept exactly as received</span>
          </div>
          {s.documents.length > 0 ? (
            <>
              <div className="flex gap-1 overflow-x-auto border-b border-line p-2" role="tablist" aria-label="Documents">
                {s.documents.map((d, i) => (
                  <button
                    key={d.id}
                    role="tab"
                    aria-selected={current?.id === d.id}
                    onClick={() => setDoc(i)}
                    className={`whitespace-nowrap rounded-[var(--radius-ctl)] px-2.5 py-1 text-xs ${current?.id === d.id ? "bg-brand-secondary text-white" : "text-ink-muted hover:bg-canvas"}`}
                  >
                    {DOC_KIND[d.kind]}{d.doc_ref ? ` ${d.doc_ref}` : ""}
                  </button>
                ))}
              </div>
              {current && (
                <div role="tabpanel" className="flex flex-1 flex-col bg-canvas">
                  <div className="flex flex-wrap gap-x-4 px-3 py-2 text-xs text-ink-muted">
                    <span>{current.filename}</span>
                    <span>{fileSize(current.byte_size)}</span>
                    <span>Added by {current.added_by_name}</span>
                    <a className={link} href={scanDocumentUrl(current.document_id)} target="_blank" rel="noreferrer">Open in new tab</a>
                  </div>
                  {current.mime_type === "application/pdf" ? (
                    <iframe title={`Original ${DOC_KIND[current.kind]}`} src={scanDocumentUrl(current.document_id)} className="min-h-[32rem] w-full flex-1 border-0 bg-card" />
                  ) : current.mime_type === "image/tiff" ? (
                    <p className="p-4 text-sm">TIFF files can't be shown in the browser. <a className={link} href={scanDocumentUrl(current.document_id)}>Download it</a>.</p>
                  ) : (
                    <img alt={`Original ${DOC_KIND[current.kind]}`} src={scanDocumentUrl(current.document_id)} className="mx-auto max-h-[40rem] w-auto" />
                  )}
                </div>
              )}
            </>
          ) : (
            <div className="p-4"><EmptyState title="No documents yet" hint="Upload the supplier invoice, airway bill or packing list." /></div>
          )}
          {editable && <DocUpload s={s} busy={busy} run={run} onAdded={() => setDoc(s.documents.length)} />}
        </section>

        <div className="min-w-0 space-y-4">
          <DeclaredSection s={s} editable={editable} busy={busy} run={run} catalog={catalog} docName={docName} onWithdraw={(lid) => setDialog({ kind: "withdrawDeclared", id: lid })} />
          <CountSection s={s} editable={editable} busy={busy} run={run} catalog={catalog} />
          <CostSection s={s} editable={costsEditable} busy={busy} run={run} docName={docName} onWithdraw={(cid) => setDialog({ kind: "withdrawCost", id: cid })} />
        </div>
      </div>

      <ReasonDialog
        open={dialog !== null && dialog.kind !== "post"}
        title={
          dialog?.kind === "withdrawDeclared"
            ? "Withdraw this document line?"
            : dialog?.kind === "withdrawCost"
              ? "Withdraw this cost?"
              : dialog?.kind === "reopen"
                ? "Reopen the count?"
                : "Cancel this shipment?"
        }
        body={
          dialog?.kind === "cancel"
            ? "Nothing is added to stock. The shipment and its documents stay on record."
            : dialog?.kind === "reopen"
              ? "The count goes back to the warehouse for changes. Nothing is posted."
              : "It is kept on record as withdrawn, with your reason."
        }
        confirmLabel={dialog?.kind === "cancel" ? "Cancel shipment" : dialog?.kind === "reopen" ? "Reopen" : "Withdraw"}
        reason={reason}
        setReason={setReason}
        onClose={() => {
          setDialog(null);
          setReason("");
        }}
        onConfirm={() => void confirmDialog()}
      />
      <ConfirmDialog
        open={dialog?.kind === "post"}
        title="Approve and post stock?"
        confirmLabel={busy ? "Posting…" : "Approve & post"}
        onClose={() => setDialog(null)}
        onConfirm={() => void post()}
      >
        <p>
          <strong>{saleableSummary(s.comparison)}</strong> will be added to stock as saleable. Damaged goods go to a separate damaged lot; refused goods are not added. Declared quantities are never added.
        </p>
        {differences.length > 0 && (
          <div className="mt-3 space-y-2 rounded-ctl border border-warning/40 bg-warning/5 p-3">
            <p className="font-medium">{differences.length} line{differences.length === 1 ? " differs" : "s differ"} from the supplier documents.</p>
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-1" />
              I checked the differences and want to post what was physically counted.
            </label>
            <div>
              <label className="label" htmlFor="ack-note">What happened? (required)</label>
              <input id="ack-note" className="input" value={ackNote} maxLength={1000} onChange={(e) => setAckNote(e.target.value)} />
            </div>
          </div>
        )}
        {error && dialog?.kind === "post" && <div className="mt-3"><Notice tone="error">{error}</Notice></div>}
      </ConfirmDialog>
    </div>
  );
}

type Run = (fn: () => Promise<ShipmentDetail>, done?: string) => Promise<boolean>;
type Catalog = ReturnType<typeof useCatalog>;

function DocUpload({ s, busy, run, onAdded }: { s: ShipmentDetail; busy: boolean; run: Run; onAdded: () => void }) {
  const [kind, setKind] = useState<ShipmentDocKind>("INVOICE");
  const [ref, setRef] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  async function onFile(file: File | undefined, input: HTMLInputElement) {
    setMsg(null);
    if (!file) return;
    const bad = checkFile(file);
    input.value = "";
    if (bad) return setMsg(bad);
    if (await run(() => api.uploadShipmentDoc(s.id, file, { version: s.version, kind, doc_ref: ref.trim() || undefined }), `${DOC_KIND[kind]} added.`)) {
      setRef("");
      onAdded();
    }
  }
  return (
    <div className="grid gap-2 border-t border-line p-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end" role="group" aria-label="Add a document">
      <div>
        <label className="label" htmlFor="doc-kind">Document type</label>
        <select id="doc-kind" className="input" value={kind} onChange={(e) => setKind(e.target.value as ShipmentDocKind)}>
          {(Object.keys(DOC_KIND) as ShipmentDocKind[]).map((k) => <option key={k} value={k}>{DOC_KIND[k]}</option>)}
        </select>
      </div>
      <div>
        <label className="label" htmlFor="doc-ref">Number on the document (optional)</label>
        <input id="doc-ref" className="input" value={ref} maxLength={64} onChange={(e) => setRef(e.target.value)} />
      </div>
      <label className={`btn-ghost cursor-pointer ${busy ? "pointer-events-none opacity-60" : ""}`}>
        Upload file
        <input data-testid="doc-upload" type="file" accept={ACCEPT} className="sr-only" onChange={(e) => void onFile(e.target.files?.[0], e.target)} />
      </label>
      {msg && <div className="sm:col-span-3"><Notice tone="error">{msg}</Notice></div>}
    </div>
  );
}

function ProductUnit({ catalog, product, unit, onProduct, onUnit, idp, optional }: { catalog: Catalog; product: string; unit: string; onProduct: (id: string) => void; onUnit: (u: UnitCode) => void; idp: string; optional?: boolean }) {
  const units = catalog.units(product);
  return (
    <>
      <div>
        <label className="label" htmlFor={`${idp}-p`}>Product{optional ? " (or leave blank to match the label)" : ""}</label>
        <select
          id={`${idp}-p`}
          className="input"
          value={product}
          onChange={(e) => {
            const id = e.target.value;
            onProduct(id);
            void catalog.load(id).then((p) => {
              const us = p ? stockUnits(p) : [];
              const first = p && us.includes(p.stock_unit) ? p.stock_unit : us[0];
              if (first) onUnit(first);
            });
          }}
        >
          <option value="">{optional ? "Match from label" : "Choose…"}</option>
          {catalog.products.map((p) => <option key={p.id} value={p.id}>{p.sku} — {p.name}</option>)}
        </select>
      </div>
      <div>
        <label className="label" htmlFor={`${idp}-u`}>Unit</label>
        <select id={`${idp}-u`} className="input" value={unit} onChange={(e) => onUnit(e.target.value as UnitCode)}>
          {product === "" ? (["BOX", "PIECE", "LB", "KG"] as UnitCode[]).map((u) => <option key={u} value={u}>{u}</option>) : units.length === 0 ? <option value="">—</option> : units.map((u) => <option key={u} value={u}>{u}</option>)}
        </select>
      </div>
    </>
  );
}

function DeclaredSection({ s, editable, busy, run, catalog, docName, onWithdraw }: { s: ShipmentDetail; editable: boolean; busy: boolean; run: Run; catalog: Catalog; docName: (id: string | null) => string; onWithdraw: (id: string) => void }) {
  const [adding, setAdding] = useState(false);
  const [docId, setDocId] = useState("");
  const [label, setLabel] = useState("");
  const [product, setProduct] = useState("");
  const [unit, setUnit] = useState<UnitCode>("BOX");
  const [q, setQ] = useState("");
  const [cost, setCost] = useState("");
  const [remember, setRemember] = useState(true);
  const live = s.declared.filter((d) => !d.removed_at);
  const gone = s.declared.filter((d) => d.removed_at);
  useEffect(() => {
    if (!docId && s.documents[0]) setDocId(s.documents[0].id);
  }, [docId, s.documents]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const ok = await run(
      () =>
        api.declareLine(s.id, {
          version: s.version,
          shipment_document_id: docId || null,
          raw_label: label.trim(),
          product_id: product || null,
          unit_code: unit,
          declared_qty: q,
          unit_cost: cost || null,
          remember_alias: !!product && remember,
        }),
      "Document line added.",
    );
    if (ok) {
      setLabel("");
      setProduct("");
      setQ("");
      setCost("");
    }
  }

  return (
    <section className="card min-w-0 overflow-hidden" aria-labelledby="dec-h">
      <div className="flex items-center gap-2 border-b border-line px-4 py-3">
        <h2 id="dec-h" className="mr-auto font-semibold">What the documents say</h2>
        {editable && !adding && <button className="btn-ghost" onClick={() => setAdding(true)}>Add document line</button>}
      </div>
      {live.length === 0 ? (
        <p className="px-4 py-3 text-sm text-ink-muted">No lines entered from the documents. These are for comparison only — they never change stock.</p>
      ) : (
        <ul className="divide-y divide-line">
          {live.map((d) => (
            <li key={d.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2 text-sm">
              <span className="min-w-0 flex-1">
                <span className="font-medium">{d.product_name}</span> <span className="text-xs text-ink-muted">{d.sku}</span>
                <span className="block text-xs text-ink-muted">“{d.raw_label}” · {docName(d.shipment_document_id)}</span>
              </span>
              <span className="num">{qtyText(d.declared_qty, d.unit_code)}</span>
              {d.unit_cost && <span className="num text-xs text-ink-muted">{moneyText(d.unit_cost, s.currency)} each</span>}
              {editable && <button className="btn-ghost text-xs" aria-label={`Withdraw ${d.sku} line`} onClick={() => onWithdraw(d.id)}>Withdraw</button>}
            </li>
          ))}
        </ul>
      )}
      {gone.length > 0 && (
        <details className="border-t border-line px-4 py-2 text-xs text-ink-muted">
          <summary>{gone.length} withdrawn line{gone.length === 1 ? "" : "s"}</summary>
          <ul className="mt-1 space-y-0.5">
            {gone.map((d) => <li key={d.id}>{d.sku} {qtyText(d.declared_qty, d.unit_code)} — “{d.removed_reason}”</li>)}
          </ul>
        </details>
      )}
      {adding && editable && (
        <form className="grid gap-2 border-t border-line bg-canvas p-3 md:grid-cols-3 md:items-end" aria-label="Add document line" onSubmit={submit}>
          <div>
            <label className="label" htmlFor="dl-doc">From document</label>
            <select id="dl-doc" className="input" value={docId} onChange={(e) => setDocId(e.target.value)}>
              <option value="">No document</option>
              {s.documents.map((d) => <option key={d.id} value={d.id}>{docName(d.id)}</option>)}
            </select>
          </div>
          <div className="md:col-span-2">
            <label className="label" htmlFor="dl-label">Description as printed</label>
            <input id="dl-label" className="input" value={label} maxLength={200} required onChange={(e) => setLabel(e.target.value)} />
          </div>
          <ProductUnit idp="dl" optional catalog={catalog} product={product} unit={unit} onProduct={setProduct} onUnit={setUnit} />
          <NumericInput label="Quantity on document" value={q} onChange={setQ} places={unitPlaces(unit)} invalidHint={unitPlaces(unit) === 0 ? "Whole numbers only" : undefined} />
          <NumericInput label={`Unit cost (${s.currency}, optional)`} value={cost} onChange={setCost} places={4} />
          {product && (
            <label className="flex items-center gap-2 text-sm md:col-span-2">
              <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
              Remember this description for {s.vendor_code}
            </label>
          )}
          <div className="flex justify-end gap-2 md:col-span-3">
            <button type="button" className="btn-ghost" onClick={() => setAdding(false)}>Close</button>
            <button className="btn-primary" disabled={busy || !label.trim() || !q}>Add line</button>
          </div>
        </form>
      )}
    </section>
  );
}

function CountSection({ s, editable, busy, run, catalog }: { s: ShipmentDetail; editable: boolean; busy: boolean; run: Run; catalog: Catalog }) {
  const [product, setProduct] = useState("");
  const [unit, setUnit] = useState<UnitCode>("BOX");
  const [accepted, setAccepted] = useState("");
  const [damaged, setDamaged] = useState("");
  const [rejected, setRejected] = useState("");
  const [batch, setBatch] = useState("");
  const [expires, setExpires] = useState("");
  const [note, setNote] = useState("");
  const tracks = !!catalog.full[product]?.tracks_expiry;
  const places = unitPlaces(unit);
  const total = countTotal(accepted, damaged, rejected);
  const totals = useMemo(() => saleableSummary(s.counted.map((c) => ({ accepted_qty: c.accepted_qty, unit_code: c.unit_code }))), [s.counted]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const ok = await run(
      () =>
        api.addCount(s.id, s.version, {
          product_id: product,
          unit_code: unit,
          accepted_qty: accepted || "0",
          damaged_qty: damaged || "0",
          rejected_qty: rejected || "0",
          batch_ref: batch.trim() || null,
          expires_at: expires || null,
          note: note.trim() || null,
        }),
      "Count line saved.",
    );
    if (ok) {
      setProduct("");
      setAccepted("");
      setDamaged("");
      setRejected("");
      setBatch("");
      setExpires("");
      setNote("");
    }
  }

  return (
    <section className="card min-w-0 overflow-hidden" aria-labelledby="cnt-h">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
        <h2 id="cnt-h" className="mr-auto font-semibold">What arrived (physical count)</h2>
        {s.counted.length > 0 && <span className="text-sm text-ink-muted">Saleable: <span className="num font-semibold text-ink">{totals}</span></span>}
      </div>
      {s.counted.length === 0 ? (
        <p className="px-4 py-3 text-sm text-ink-muted">Count each product as it comes off the truck. Split good, damaged and refused.</p>
      ) : (
        <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table">
          <table className="w-full min-w-[36rem] border-separate border-spacing-0">
            <caption className="sr-only">Counted lines</caption>
            <thead>
              <tr>
                <th className="th">Product / batch</th>
                <th className="th text-right">Accepted</th>
                <th className="th text-right">Damaged</th>
                <th className="th text-right">Refused</th>
                <th className="th"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody>
              {s.counted.map((c) => (
                <tr key={c.id}>
                  <td className="td">
                    <div className="font-medium">{c.product_name} <span className="text-xs text-ink-muted">{c.sku}</span></div>
                    <div className="text-xs text-ink-muted">
                      {c.unit_code.toLowerCase()}
                      {c.batch_ref && <> · Batch {c.batch_ref}</>}
                      {c.expires_at && <> · Expires {day(c.expires_at)}</>}
                      {c.lot_code && <> · Lot {c.lot_code}</>}
                      {c.note && <> · {c.note}</>}
                    </div>
                  </td>
                  <td className="td num text-right font-semibold">{qtyText(c.accepted_qty)}</td>
                  <td className="td num text-right">{qtyText(c.damaged_qty)}</td>
                  <td className="td num text-right">{qtyText(c.rejected_qty)}</td>
                  <td className="td text-right">
                    {editable && (
                      <button className="btn-ghost text-xs" aria-label={`Remove count line ${c.seq}`} disabled={busy} onClick={() => void run(() => api.removeCount(s.id, c.id, s.version), "Count line removed.")}>
                        Remove
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {editable && (
        <form className="grid gap-2 border-t border-line bg-canvas p-3 md:grid-cols-3 md:items-end" aria-label="Add count line" onSubmit={submit}>
          <ProductUnit idp="cl" catalog={catalog} product={product} unit={unit} onProduct={setProduct} onUnit={setUnit} />
          <NumericInput label="Accepted (good)" unit={unit.toLowerCase()} value={accepted} onChange={setAccepted} places={places} invalidHint={places === 0 ? "Whole numbers only" : undefined} />
          <NumericInput label="Damaged" unit={unit.toLowerCase()} value={damaged} onChange={setDamaged} places={places} invalidHint={places === 0 ? "Whole numbers only" : undefined} />
          <NumericInput label="Refused / sent back" unit={unit.toLowerCase()} value={rejected} onChange={setRejected} places={places} invalidHint={places === 0 ? "Whole numbers only" : undefined} />
          <div>
            <label className="label" htmlFor="cl-b">Batch (optional)</label>
            <input id="cl-b" className="input" value={batch} maxLength={64} onChange={(e) => setBatch(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="cl-x">Expiry date{tracks ? " (required)" : " (optional)"}</label>
            <input id="cl-x" type="date" className="input" value={expires} required={tracks} onChange={(e) => setExpires(e.target.value)} />
          </div>
          <div className="md:col-span-2">
            <label className="label" htmlFor="cl-n">Note (optional)</label>
            <input id="cl-n" className="input" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
          </div>
          <div className="flex items-center justify-end gap-3 md:col-span-3">
            <span className="text-sm text-ink-muted">Total counted: <span className="num font-semibold text-ink">{qtyText(total, unit)}</span></span>
            <button className="btn-primary" disabled={busy || !product || total === "0.0000"}>Save count line</button>
          </div>
        </form>
      )}
    </section>
  );
}

function CostSection({ s, editable, busy, run, docName, onWithdraw }: { s: ShipmentDetail; editable: boolean; busy: boolean; run: Run; docName: (id: string | null) => string; onWithdraw: (id: string) => void }) {
  const [kind, setKind] = useState<LandedCostKind>("FREIGHT");
  const [amount, setAmount] = useState("");
  const [cur, setCur] = useState(s.currency);
  const [docId, setDocId] = useState("");
  const [note, setNote] = useState("");
  const live = s.costs.filter((c) => !c.removed_at);
  const gone = s.costs.filter((c) => c.removed_at);
  const totals = Object.entries(s.cost_totals);

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
    <section className="card min-w-0 overflow-hidden" aria-labelledby="cost-h">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
        <h2 id="cost-h" className="mr-auto font-semibold">Freight, duty and other costs</h2>
        {totals.length > 0 && <span className="num text-sm font-semibold">{totals.map(([c, a]) => moneyText(a, c)).join(" + ")}</span>}
      </div>
      <p className="px-4 pt-3 text-xs text-ink-muted">Recorded against the shipment only. They are not spread over product costs until the allocation rule is decided.</p>
      {live.length > 0 && (
        <ul className="divide-y divide-line">
          {live.map((c) => (
            <li key={c.id} className="flex flex-wrap items-center gap-x-4 px-4 py-2 text-sm">
              <span className="flex-1">
                {COST_KIND[c.kind]}
                <span className="block text-xs text-ink-muted">{docName(c.shipment_document_id)}{c.note ? ` · ${c.note}` : ""} · {c.created_by_name}</span>
              </span>
              <span className="num">{moneyText(c.amount, c.currency)}</span>
              {editable && <button className="btn-ghost text-xs" aria-label={`Withdraw ${COST_KIND[c.kind]} cost`} onClick={() => onWithdraw(c.id)}>Withdraw</button>}
            </li>
          ))}
        </ul>
      )}
      {gone.length > 0 && (
        <details className="border-t border-line px-4 py-2 text-xs text-ink-muted">
          <summary>{gone.length} withdrawn cost{gone.length === 1 ? "" : "s"}</summary>
          <ul className="mt-1">{gone.map((c) => <li key={c.id}>{COST_KIND[c.kind]} {moneyText(c.amount, c.currency)} — “{c.removed_reason}”</li>)}</ul>
        </details>
      )}
      {editable ? (
        <form className="mt-2 grid gap-2 border-t border-line bg-canvas p-3 md:grid-cols-4 md:items-end" aria-label="Add a cost" onSubmit={submit}>
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
          <div className="md:col-span-3">
            <label className="label" htmlFor="lc-n">Note (optional)</label>
            <input id="lc-n" className="input" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
          </div>
          <div className="flex justify-end">
            <button className="btn-primary" disabled={busy || !amount || cur.trim().length !== 3}>Add cost</button>
          </div>
        </form>
      ) : (
        live.length === 0 && <p className="px-4 pb-3 text-sm text-ink-muted">No costs recorded.</p>
      )}
    </section>
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
  if (!session) return null;
  const canManage = has(session, "catalog.manage");

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
        <ErrorState title="Couldn't load suppliers" onRetry={() => void load()} />
      ) : rows === null ? (
        <LoadingRows cols={6} />
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
                  canManage ? (
                    <button className="btn-ghost px-3 py-1.5 text-xs" aria-label={`Edit ${v.name}`} onClick={() => setEditing({ ...v })}>
                      Edit
                    </button>
                  ) : undefined
                }
              />
            );
          }}
        />
      ) : (
        <DataGrid rows={rows} columns={columns} rowKey={(v) => v.id} caption="Suppliers" empty={<EmptyState title="No suppliers yet" />} />
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
