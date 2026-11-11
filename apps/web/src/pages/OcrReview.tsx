import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  api,
  canInWarehouse,
  orderSheetUrl,
  scanDocumentUrl,
  type Customer,
  type OcrAccuracy,
  type OcrExtraction,
  type OcrExtractionDetail,
  type OcrField,
  type Product,
  type ProductSummary,
  type UnitCode,
  type Warehouse,
} from "../api";
import { useAuth } from "../auth";
import { DocViewer, PriceList, SupplierHeader, ToReceiving, type Flag } from "./OcrSupplier";
import { ScanProcessing } from "../components/scanner/ScanProcessing";
import { stockUnits, wholeOnly } from "../lib/inventory";
import { canConfirm, confidencePct, FIELD_STATUS, needsReason, OCR_STATUS, ocrMessage, openLines, pctText, RESOLUTION, selectable, trimQty, warningText, engineText, lineNotes } from "../lib/ocr";
import { DataGrid, type Column } from "../ui/DataGrid";
import { NumericInput } from "../ui/NumericInput";
import { Icon } from "../ui/Icon";
import { ActionBar } from "../ui/gridParts";
import { ConfirmDialog, EmptyState, ErrorState, FilterBar, KpiCard, LoadingRows, PageHeader, PageSkeleton, StatusChip } from "../ui/primitives";
import { ReasonDialog } from "./Orders";
import { useDebounced } from "../lib/useDebounced";

const link = "font-medium text-brand-primary-hover underline-offset-2 hover:underline";
const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—";
const POLL_MS = 3000;

export const OcrChip = ({ s }: { s: OcrExtraction["status"] }) => <StatusChip tone={OCR_STATUS[s].tone}>{OCR_STATUS[s].label}</StatusChip>;

function Notice({ tone, children }: { tone: "error" | "ok" | "warn"; children: React.ReactNode }) {
  const cls =
    tone === "error" ? "border-danger/40 bg-danger/5 text-danger" : tone === "warn" ? "border-warning/50 bg-warning/10 text-ink" : "border-success/40 bg-success/5 text-ink";
  return (
    <p role={tone === "error" ? "alert" : "status"} className={`rounded-ctl border px-3 py-2 text-sm ${cls}`}>
      {children}
    </p>
  );
}

function QuietNotice({ tone, children }: { tone: "error" | "ok" | "warn"; children: React.ReactNode }) {
  const cls = tone === "error" ? "bg-danger/8 text-danger" : tone === "warn" ? "bg-warning/10 text-ink" : "bg-success/8 text-ink";
  return <p className={`rounded-ctl px-4 py-3 text-sm ${cls}`}>{children}</p>;
}

/* ============================ Review queue (Scan Center tab) ============================ */

type View = "todo" | "QUEUED" | "FAILED" | "done" | "all";
const VIEWS: { value: View; label: string }[] = [
  { value: "todo", label: "Needs review" },
  { value: "QUEUED", label: "Reading" },
  { value: "FAILED", label: "Couldn't read" },
  { value: "done", label: "Finished" },
  { value: "all", label: "All" },
];
const VIEW_STATUS: Record<View, string | undefined> = {
  todo: "NEEDS_REVIEW",
  QUEUED: "QUEUED",
  FAILED: "FAILED",
  done: "ACCEPTED,REJECTED",
  all: undefined,
};

export function ReviewQueue({ warehouses }: { warehouses: Warehouse[] }) {
  const nav = useNavigate();
  const [view, setView] = useState<View>("todo");
  const [q, setQ] = useState("");
  const query = useDebounced(q.trim());
  const [rows, setRows] = useState<OcrExtraction[] | null>(null);
  const [acc, setAcc] = useState<OcrAccuracy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const r = await api.ocrExtractions({ status: VIEW_STATUS[view], q: query || undefined, limit: 100 });
      setRows(r.items);
      setError(null);
    } catch (e) {
      setError(ocrMessage(e));
    }
  }, [view, query]);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    api.ocrAccuracy().then(setAcc, () => setAcc(null));
  }, []);
  // New scans are read in the background, so keep the queue fresh (faster while something is reading).
  const reading = !!rows?.some((r) => r.status === "QUEUED");
  useEffect(() => {
    const t = setInterval(() => void load(), reading ? POLL_MS : POLL_MS * 2);
    return () => clearInterval(t);
  }, [reading, load]);

  const columns: Column<OcrExtraction>[] = [
    { key: "scan", header: "Scan", cell: (r) => <span className="num font-medium">{r.scan_job_no}</span>, sort: (r) => r.scan_job_no },
    { key: "status", header: "Status", cell: (r) => <OcrChip s={r.status} />, sort: (r) => r.status },
    { key: "customer", header: "Customer", cell: (r) => r.customer_name ?? <span className="text-ink-muted">Not found</span>, sort: (r) => r.customer_name ?? "" },
    {
      key: "lines",
      header: "Lines",
      align: "right",
      cell: (r) => (r.status === "NEEDS_REVIEW" && r.open_count > 0 ? `${r.open_count} of ${r.field_count} to check` : r.field_count),
    },
    { key: "order", header: "Order", cell: (r) => r.sales_order_no ?? "—" },
    { key: "when", header: "Scanned", cell: (r) => when(r.created_at), sort: (r) => r.created_at },
  ];
  const sampled = acc && acc.samples > 0;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 md:grid-cols-3">
        <KpiCard
          icon="check"
          label="Reading accuracy"
          value={sampled ? pctText(acc.accuracy) : "—"}
          note={
            !acc ? undefined : acc.samples < acc.minimum_samples ? `${acc.samples} of ${acc.minimum_samples} lines needed to measure` : acc.meets_target ? "Meets 95% target" : "Below 95% target"
          }
          tone={acc && acc.samples >= acc.minimum_samples ? (acc.meets_target ? "success" : "warning") : undefined}
        />
        <KpiCard icon="file" label="Lines checked" value={acc ? acc.samples : "—"} note={acc ? `${acc.correct} read correctly` : undefined} />
        <OrderSheetCard warehouses={warehouses} />
      </div>
      <FilterBar search={q} onSearch={setQ} options={VIEWS} value={view} onChange={setView} placeholder="Scan no. or customer" />
      <section className="card overflow-clip">
        {error && !rows ? (
          <ErrorState title="Couldn't load scans to review" detail={error} onRetry={() => void load()} />
        ) : !rows ? (
          <LoadingRows rows={4} cols={6} />
        ) : (
          <DataGrid
            rows={rows}
            columns={columns}
            rowKey={(r) => r.id}
            caption="Scanned orders to review"
            onOpen={(r) => nav(`/scan/review/${r.id}`)}
            empty={<EmptyState title={view === "todo" ? "Nothing to review" : "No scans here"} hint="Scanned sales orders are read automatically and appear here for checking." />}
          />
        )}
      </section>
    </div>
  );
}

function OrderSheetCard({ warehouses }: { warehouses: Warehouse[] }) {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [cust, setCust] = useState("");
  const wh = warehouses[0]?.id ?? "";
  useEffect(() => {
    api.customers({ limit: 200 }).then((c) => setCustomers(c.filter((x) => x.active)), () => setCustomers([]));
  }, []);
  return (
    <div className="card space-y-2 p-6">
      <div className="kicker">Printable order sheet</div>
      <div className="flex gap-2">
        <label className="min-w-0 flex-1">
          <span className="sr-only">Customer for order sheet</span>
          <select className="input py-1.5" value={cust} onChange={(e) => setCust(e.target.value)}>
            <option value="">Customer…</option>
            {customers.map((c) => <option key={c.id} value={c.id}>{c.external_ref ? `${c.external_ref} — ` : ""}{c.name}</option>)}
          </select>
        </label>
      </div>
      {cust && wh ? (
        <a className="btn-ghost w-full justify-center" href={orderSheetUrl(cust, wh)} download>
          Download sheet (PDF)
        </a>
      ) : (
        <p className="text-xs text-ink-muted">Sheets filled in by hand on this form are read most accurately.</p>
      )}
    </div>
  );
}

/* ============================ Reading status on a scan ============================ */

export function ScanReading({ jobId, warehouseId, received }: { jobId: string; warehouseId: string; received: boolean }) {
  const { session } = useAuth();
  const [rows, setRows] = useState<OcrExtraction[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();
  const can = !!session && canInWarehouse(session, "scan.review", warehouseId);
  const load = useCallback(() => {
    if (!can) return;
    api.ocrExtractions({ scan_job_id: jobId, status: "QUEUED,NEEDS_REVIEW,FAILED,ACCEPTED,REJECTED" }).then(
      (r) => setRows(r.items),
      () => setRows([]),
    );
  }, [jobId, can]);
  useEffect(() => load(), [load]);
  useEffect(() => {
    if (!rows?.some((r) => r.status === "QUEUED")) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [rows, load]);
  if (!can || !received || rows === null) return null;
  const cur = rows[0];
  const read = async () => {
    setBusy(true);
    try {
      const ex = await api.readScan(jobId);
      nav(`/scan/review/${ex.id}`);
    } catch (e) {
      setError(ocrMessage(e));
      setBusy(false);
    }
  };
  return (
    <section aria-label="Reading" className="card flex flex-wrap items-center gap-3 p-6 text-sm">
      <span className="font-medium">Reading:</span>
      {cur ? (
        <>
          <OcrChip s={cur.status} />
          {cur.status === "ACCEPTED" && cur.sales_order_id ? (
            <Link className={link} to={`/orders/${cur.sales_order_id}`}>Draft order {cur.sales_order_no}</Link>
          ) : null}
          <Link className={link} to={`/scan/review/${cur.id}`}>{cur.status === "NEEDS_REVIEW" ? "Review lines" : "Open reading"}</Link>
        </>
      ) : (
        <span className="text-ink-muted">Not read yet.</span>
      )}
      {(!cur || cur.status === "FAILED") && (
        <button className="btn-ghost ml-auto" disabled={busy} onClick={() => void read()}>
          {busy ? "Starting…" : cur ? "Try reading again" : "Read this scan"}
        </button>
      )}
      {error && <p className="w-full text-danger" role="alert">{error}</p>}
    </section>
  );
}

/* ============================ Review page ============================ */

type Editing = { fieldId: string | "new"; product_id: string; unit_code: UnitCode | ""; qty: string; reason: string };

/** Review page (route /scan/review/:id), or embedded under a finished scan in the Scan Center.
 * Laid out like Create invoice: header fields, then the lines as a compact grid (tick clear
 * lines and Confirm selected), then a sticky bar with the counts and the main actions. */
export function OcrReviewPage({ id: given, embedded = false }: { id?: string; embedded?: boolean } = {}) {
  const params = useParams();
  const id = given ?? params.id ?? "";
  const { session } = useAuth();
  const [ex, setEx] = useState<OcrExtractionDetail | null>(null);
  const [focusSeq, setFocusSeq] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [details, setDetails] = useState<Record<string, Product>>({});
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [customer, setCustomer] = useState("");
  const [note, setNote] = useState("");
  const [checked, setChecked] = useState(false);
  const [edit, setEdit] = useState<Editing | null>(null);
  const [removing, setRemoving] = useState<OcrField | null>(null);
  const [reason, setReason] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [rereading, setRereading] = useState(false);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const nav = useNavigate();
  const [search] = useSearchParams();
  const startPage = Math.max(1, Number(search.get("page")) || 1);

  const load = useCallback(async () => {
    try {
      const r = await api.ocrExtraction(id);
      setEx(r);
      setCustomer((c) => c || (r.customer_id ?? ""));
      setError(null);
    } catch (e) {
      setError(ocrMessage(e));
    }
  }, [id]);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (ex?.status !== "QUEUED") return;
    const t = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(t);
  }, [ex?.status, load]);
  useEffect(() => {
    api.products({ active: true, limit: 1000 }).then((p) => setProducts(p.items), () => setProducts([]));
    api.customers({ limit: 200 }).then((c) => setCustomers(c.filter((x) => x.active)), () => setCustomers([]));
  }, []);
  // Ticks only on lines that can still be confirmed as read.
  useEffect(() => {
    if (!ex) return;
    const ok = new Set(ex.fields.filter(selectable).map((f) => f.id));
    setPicked((cur) => (Array.from(cur).every((x) => ok.has(x)) ? cur : new Set(Array.from(cur).filter((x) => ok.has(x)))));
  }, [ex]);

  const product = async (pid: string): Promise<Product | undefined> => {
    if (!pid) return undefined;
    const have = details[pid];
    if (have) return have;
    // A product that can't be loaded (removed, no access) just leaves the editor to choose one.
    const p = await api.product(pid).catch(() => undefined);
    if (p) setDetails((d) => ({ ...d, [pid]: p }));
    return p;
  };

  const run = async (fn: () => Promise<OcrExtractionDetail>, ok?: string) => {
    setBusy(true);
    setNotice(null);
    try {
      const r = await fn();
      setEx(r);
      setError(null);
      if (ok) setNotice(ok);
      return r;
    } catch (e) {
      setError(ocrMessage(e));
      if (e instanceof Error && "code" in e && (e as { code: string }).code === "VERSION_CONFLICT") void load();
      return null;
    } finally {
      setBusy(false);
    }
  };

  if (error && !ex) return <ErrorState title="Couldn't load this reading" detail={error} onRetry={() => void load()} />;
  if (!ex || !session) return embedded ? <p className="card p-6 text-sm text-ink-muted">Loading the recognized lines…</p> : <PageSkeleton />;

  const reviewing = ex.status === "NEEDS_REVIEW";
  const canAccept = canInWarehouse(session, "sales_order.manage", ex.warehouse_id);
  const open = openLines(ex.fields);
  const kept = ex.fields.filter((f) => f.resolution === "CONFIRMED" || f.resolution === "CORRECTED");
  const supplierDoc = !!ex.header && Object.keys(ex.header).length > 0;
  const canReceive = canInWarehouse(session, "grn.record", ex.warehouse_id);
  const lit = ex.fields.find((f) => f.seq === focusSeq)?.bbox as { page: number; x0: string; y0: string; x1: string; y1: string } | undefined;
  const url = scanDocumentUrl(ex.document_id);
  const tickable = ex.fields.filter(selectable);
  const nPicked = tickable.filter((f) => picked.has(f.id)).length;
  // Every line still to check is outlined on the original: amber = unclear quantity, red = unknown
  // product or nothing readable. Clicking an outline brings its line into view.
  const flags: Flag[] = ex.fields.flatMap((f) => {
    const b = f.bbox as { page?: number; x0?: string; y0?: string; x1?: string; y1?: string } | null;
    if (f.resolution !== "NONE" || f.status === "OK" || !b?.page || !b.x0 || !b.y0 || !b.x1 || !b.y1) return [];
    return [{ page: b.page, x0: b.x0, y0: b.y0, x1: b.x1, y1: b.y1, seq: f.seq, tone: f.status === "LOW_CONFIDENCE" ? "warning" : "danger", label: FIELD_STATUS[f.status].label }];
  });
  const showLine = (seq: number) => {
    setFocusSeq(seq);
    document.querySelector(`[data-testid='ocr-line-${seq}']`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const startEdit = async (f: OcrField | null) => {
    const pid = f ? (f.final_product_id ?? f.read_product_id ?? "") : "";
    const p = await product(pid);
    const unit = (f?.final_unit_code ?? f?.read_unit_code ?? (p ? p.stock_unit : "")) as UnitCode | "";
    setEdit({ fieldId: f ? f.id : "new", product_id: pid, unit_code: unit, qty: trimQty(f?.final_qty ?? f?.read_qty ?? ""), reason: "" });
  };
  const saveEdit = async () => {
    if (!edit) return;
    if (!edit.product_id || !edit.unit_code || !edit.qty) return setError("Choose a product, unit and quantity.");
    const qty = edit.qty;
    if (edit.fieldId === "new") {
      if (edit.reason.trim().length < 3) return setError("Say why this line is being added (at least 3 characters).");
      const r = await run(() => api.addOcrLine(ex.id, { version: ex.version, product_id: edit.product_id, unit_code: edit.unit_code as UnitCode, qty, reason: edit.reason.trim() }), "Line added.");
      if (r) setEdit(null);
      return;
    }
    const f = ex.fields.find((x) => x.id === edit.fieldId);
    const r = await run(
      () => api.resolveOcrField(ex.id, edit.fieldId, { version: ex.version, action: "CORRECT", product_id: edit.product_id, unit_code: edit.unit_code as UnitCode, qty }),
      `Line ${f?.seq ?? ""} corrected.`,
    );
    if (r) setEdit(null);
  };
  const confirmPicked = async () => {
    const ids = tickable.filter((f) => picked.has(f.id)).map((f) => f.id);
    if (!ids.length) return;
    const r = await run(() => api.confirmOcrLines(ex.id, { version: ex.version, field_ids: ids }), `${ids.length} ${ids.length === 1 ? "line" : "lines"} confirmed.`);
    if (r) setPicked(new Set());
  };
  const togglePick = (fid: string, on: boolean) =>
    setPicked((cur) => {
      const next = new Set(cur);
      if (on) next.add(fid);
      else next.delete(fid);
      return next;
    });

  const accept = async () => {
    if (!customer) return setError("Choose the customer this order is for.");
    if (!checked) return setError("Tick the box to confirm you checked every line against the scan.");
    const r = await run(() => api.acceptOcr(ex.id, { version: ex.version, customer_id: customer, note, reviewed_all: true }));
    if (r?.sales_order_id) nav(`/orders/${r.sales_order_id}`);
  };

  const editor = edit && (
    <LineEditor
      edit={edit}
      setEdit={setEdit}
      products={products}
      product={details[edit.product_id]}
      onProduct={async (pid) => {
        const p = await product(pid);
        const units = p ? stockUnits(p) : [];
        setEdit((e) => e && { ...e, product_id: pid, unit_code: p ? (units.includes(p.stock_unit) ? p.stock_unit : units[0] ?? "") : "" });
      }}
      reasonNeeded={edit.fieldId === "new"}
      busy={busy}
      onSave={() => void saveEdit()}
    />
  );

  const Info = embedded ? QuietNotice : Notice;
  const low = ex.fields.filter((f) => f.resolution === "NONE" && f.status === "LOW_CONFIDENCE").length;
  const unknown = ex.fields.filter((f) => f.resolution === "NONE" && (f.status === "UNMAPPED" || f.status === "UNREADABLE")).length;
  const orderDoc = reviewing && !supplierDoc;
  const th = "sticky top-0 z-10 border-b border-line bg-subtle px-2.5 py-2 text-left text-xs font-semibold text-ink";

  return (
    <div className="space-y-5">
      {!embedded && <PageHeader
        title={`Review scan ${ex.scan_job_no}`}
        subtitle={
          <>
            <Link className={link} to="/scan?tab=review">Scans to review</Link> · read {when(ex.completed_at ?? ex.created_at)}
            {ex.run_no > 1 ? ` · reading ${ex.run_no}` : ""}
          </>
        }
        actions={<OcrChip s={ex.status} />}
      />}
      {error && <Notice tone="error">{error}</Notice>}
      {notice && <Notice tone="ok">{notice}</Notice>}
      {ex.warnings.map((w) => <Info key={w} tone="warn">{warningText(w)}</Info>)}
      {engineText(ex.engine) && <p className="text-xs text-ink-muted">{engineText(ex.engine)}</p>}
      {ex.status === "ACCEPTED" && ex.sales_order_id && (
        <Notice tone="ok">
          Draft order <Link className={link} to={`/orders/${ex.sales_order_id}`}>{ex.sales_order_no}</Link> was created by {ex.reviewed_by_name}. Confirm it from the order page.
        </Notice>
      )}
      {ex.status === "REJECTED" && <Notice tone="warn">Rejected by {ex.reviewed_by_name}: {ex.review_note}</Notice>}
      {ex.status === "FAILED" && (
        <Notice tone="error">
          The scan couldn't be read{ex.error_code ? ` (${ex.error_code})` : ""}. The scan itself is kept — read it again, or reject it and enter the order by hand.
        </Notice>
      )}
      {ex.status === "QUEUED" &&
        (embedded ? (
          <Info tone="ok">Reading the scan… this page updates by itself.</Info>
        ) : (
          <ScanProcessing
            jobId={ex.scan_job_id}
            hideReview
            onProgress={(p) => {
              if (p.reading?.id === ex.id && p.reading.status !== ex.status) void load();
            }}
          />
        ))}

      <div className={embedded ? "space-y-5" : "grid gap-5 xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]"}>
        {!embedded && <section aria-label="Original scan" className="card min-h-[24rem] overflow-hidden xl:sticky xl:top-4 xl:h-[calc(100vh-8rem)]">
          <DocViewer documentId={ex.document_id} title={`Original scan ${ex.scan_job_no}`} highlight={lit ?? null} flags={reviewing ? flags : []} initialPage={startPage} onFlag={showLine} />
        </section>}

        <div className="min-w-0 space-y-4">
          {supplierDoc && <SupplierHeader ex={ex} threshold={0.9} editable={reviewing} onFixed={setEx} />}
          <section aria-labelledby="lines-h" className="card space-y-5 p-6">
            <div className="space-y-1">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <h2 id="lines-h" className="text-xl font-semibold tracking-tight">Recognized lines</h2>
                <div className="flex flex-wrap gap-1.5">
                  {low > 0 && <StatusChip tone="warning">{`${low} unclear`}</StatusChip>}
                  {unknown > 0 && <StatusChip tone="danger">{`${unknown} to fix`}</StatusChip>}
                </div>
              </div>
              <p className="text-sm text-ink-muted">
                Check each line against the scan and fix what was misread. Tick the clear ones and confirm them together.{" "}
                <span aria-live="polite">{reviewing ? (open.length ? `${open.length} of ${ex.fields.length} still to check.` : "All lines checked.") : `${ex.fields.length} lines.`}</span>
              </p>
            </div>
            {orderDoc && (
              <div className="grid gap-x-5 gap-y-4 md:grid-cols-12">
                <div className="md:col-span-7">
                  <label className="label" htmlFor="ocr-cust">Customer</label>
                  <select id="ocr-cust" className="input" value={customer} onChange={(e) => setCustomer(e.target.value)}>
                    <option value="">Choose…</option>
                    {customers.map((c) => <option key={c.id} value={c.id}>{c.external_ref ? `${c.external_ref} — ` : ""}{c.name}</option>)}
                  </select>
                </div>
                <div className="md:col-span-5">
                  <label className="label" htmlFor="ocr-note">Note (optional)</label>
                  <input id="ocr-note" className="input" value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} />
                </div>
              </div>
            )}
            {ex.fields.length === 0 ? (
              <p className="text-sm text-ink-muted">No lines were read.</p>
            ) : (
              <div className="overflow-x-auto rounded-[var(--radius-ctl)] border border-line 2xl:overflow-visible">
                <table className="w-full min-w-[36rem] table-fixed border-collapse text-sm" aria-label="Recognized lines">
                  <colgroup>
                    {reviewing && <col style={{ width: "2.25rem" }} />}
                    <col style={{ width: "2.5rem" }} />
                    <col />
                    <col style={{ width: "5.25rem" }} />
                    <col style={{ width: "8.5rem" }} />
                    {reviewing && <col style={{ width: "8rem" }} />}
                  </colgroup>
                  <thead>
                    <tr>
                      {reviewing && (
                        <th scope="col" className={`${th} text-center`}>
                          <input
                            type="checkbox"
                            className="size-4 cursor-pointer accent-brand-primary align-middle"
                            aria-label="Select all clear lines"
                            disabled={!tickable.length || busy}
                            checked={tickable.length > 0 && nPicked === tickable.length}
                            ref={(el) => {
                              if (el) el.indeterminate = nPicked > 0 && nPicked < tickable.length;
                            }}
                            onChange={(e) => setPicked(e.target.checked ? new Set(tickable.map((f) => f.id)) : new Set())}
                          />
                        </th>
                      )}
                      <th scope="col" className={`${th} text-center`}>S/N</th>
                      <th scope="col" className={th}>Item</th>
                      <th scope="col" className={`${th} text-right`}>Qty</th>
                      <th scope="col" className={th} title="Status, and how sure the reader is">Status</th>
                      {reviewing && <th scope="col" className={th}><span className="sr-only">Actions</span></th>}
                    </tr>
                  </thead>
                  <tbody
                    onMouseOver={(e) => {
                      const tr = (e.target as HTMLElement).closest("[data-testid^='ocr-line-']");
                      if (tr) setFocusSeq(Number(tr.getAttribute("data-testid")?.slice(9)));
                    }}
                    onFocus={(e) => {
                      const tr = (e.target as HTMLElement).closest("[data-testid^='ocr-line-']");
                      if (tr) setFocusSeq(Number(tr.getAttribute("data-testid")?.slice(9)));
                    }}
                  >
                    {ex.fields.map((f, i) => (
                      <FieldRow
                        key={f.id}
                        f={f}
                        zebra={i % 2 === 1}
                        lit={f.seq === focusSeq}
                        reviewing={reviewing}
                        busy={busy}
                        picked={picked.has(f.id)}
                        onPick={(on) => togglePick(f.id, on)}
                        editor={edit?.fieldId === f.id ? editor : null}
                        onConfirm={() => void run(() => api.resolveOcrField(ex.id, f.id, { version: ex.version, action: "CONFIRM" }))}
                        onCorrect={() => void startEdit(f)}
                        onRemove={() => {
                          if (needsReason(f, "REMOVE")) {
                            setReason("");
                            setRemoving(f);
                          } else void run(() => api.resolveOcrField(ex.id, f.id, { version: ex.version, action: "REMOVE" }));
                        }}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {reviewing && (
              <div className="flex flex-wrap items-center gap-2">
                {edit?.fieldId === "new" ? (
                  <div className="w-full">{editor}</div>
                ) : (
                  <button className="btn-ghost" disabled={busy} onClick={() => void startEdit(null)}>
                    <Icon name="plus" size={18} />Add a line the reading missed
                  </button>
                )}
                <span className="ml-auto flex gap-2">
                  <button className="btn-ghost" disabled={busy} onClick={() => setRereading(true)}>Read again</button>
                  {orderDoc && <button className="btn-ghost" disabled={busy} onClick={() => { setReason(""); setRejecting(true); }}>Reject</button>}
                </span>
              </div>
            )}
            {supplierDoc && (reviewing || ex.shipment_id) && (
              <div className="border-t border-line pt-5">
                <PriceList fields={ex.fields} />
              </div>
            )}
            {(reviewing || ex.status === "FAILED") && (
              <ActionBar
                left={
                  <>
                    {reviewing ? (
                      <button className="btn-accent whitespace-nowrap" disabled={busy || nPicked === 0} onClick={() => void confirmPicked()}>
                        <Icon name="check" size={18} />
                        {nPicked ? `Confirm selected (${nPicked})` : "Confirm selected"}
                      </button>
                    ) : (
                      <>
                        <button className="btn-ghost" disabled={busy} onClick={() => setRereading(true)}>Read again</button>
                        <button className="btn-ghost" disabled={busy} onClick={() => { setReason(""); setRejecting(true); }}>Reject</button>
                      </>
                    )}
                  </>
                }
                figures={[
                  { label: "Lines kept", value: kept.length },
                  { label: "Still to check", value: open.length, green: reviewing && open.length === 0 },
                ]}
                status={orderDoc ? (open.length > 0 ? `Check ${open.length > 6 ? `the ${open.length} open lines` : `lines ${open.join(", ")}`} first.` : "Every line is checked. This only creates a draft order: nothing is reserved, picked or invoiced.") : undefined}
              >
                {orderDoc && (
                  <>
                    <label className="flex items-center gap-2.5 whitespace-nowrap text-sm">
                      <input type="checkbox" className="size-4 accent-brand-primary" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
                      I checked every line against the scan.
                    </label>
                    {canAccept ? (
                      <button className="btn-primary whitespace-nowrap" disabled={busy || open.length > 0 || kept.length === 0 || !checked || !customer} onClick={() => void accept()}>
                        Create draft order
                      </button>
                    ) : (
                      <span className="text-sm text-ink-muted">Someone who can manage orders must create the draft.</span>
                    )}
                  </>
                )}
              </ActionBar>
            )}
          </section>

          {supplierDoc && (reviewing || ex.shipment_id) && <ToReceiving ex={ex} ready={open.length === 0 && kept.length > 0} canReceive={canReceive} onDone={setEx} />}
          <p className="text-sm">
            <Link className={link} to={`/scan/${ex.scan_job_id}`}>Scan details</Link>
            {" · "}
            <a className={link} href={url} target="_blank" rel="noreferrer">Open original in a new tab</a>
          </p>
        </div>
      </div>

      <ReasonDialog
        open={!!removing}
        title={`Remove line ${removing?.seq ?? ""}?`}
        body="This line was read clearly. Say why it shouldn't be on the order."
        confirmLabel="Remove line"
        reason={reason}
        setReason={setReason}
        onClose={() => setRemoving(null)}
        onConfirm={() => {
          const f = removing;
          setRemoving(null);
          if (f) void run(() => api.resolveOcrField(ex.id, f.id, { version: ex.version, action: "REMOVE", reason: reason.trim() }));
        }}
      />
      <ReasonDialog
        open={rejecting}
        title="Reject this scan?"
        body="No order is created. The scan and what was read are kept on file."
        confirmLabel="Reject"
        reason={reason}
        setReason={setReason}
        onClose={() => setRejecting(false)}
        onConfirm={() => {
          setRejecting(false);
          void run(() => api.rejectOcr(ex.id, ex.version, reason.trim()), "Rejected.");
        }}
      />
      <ConfirmDialog
        open={rereading}
        title="Read this scan again?"
        confirmLabel="Read again"
        onClose={() => setRereading(false)}
        onConfirm={() => {
          setRereading(false);
          void run(() => api.readScan(ex.scan_job_id)).then((r) => r && nav(`/scan/review/${r.id}`));
        }}
      >
        The current reading and any checks you made on it are set aside (kept in history). A fresh reading starts from the original scan.
      </ConfirmDialog>
    </div>
  );
}

/** One recognized line as a grid row (same look as the Create invoice grid), plus its editor. */
function FieldRow({
  f,
  zebra,
  lit,
  reviewing,
  busy,
  picked,
  onPick,
  editor,
  onConfirm,
  onCorrect,
  onRemove,
}: {
  f: OcrField;
  zebra: boolean;
  lit: boolean;
  reviewing: boolean;
  busy: boolean;
  picked: boolean;
  onPick: (on: boolean) => void;
  editor: React.ReactNode;
  onConfirm: () => void;
  onCorrect: () => void;
  onRemove: () => void;
}) {
  const st = FIELD_STATUS[f.status];
  const res = RESOLUTION[f.resolution];
  const pct = confidencePct(f.confidence);
  const open = f.resolution === "NONE";
  const resolved = !open && f.resolution !== "REMOVED";
  const name = resolved && f.final_product_name ? f.final_product_name : f.kind === "MANUAL" ? "Added by hand" : f.label;
  const sku = resolved ? f.final_sku : f.read_sku;
  const qty = resolved ? f.final_qty : f.read_qty;
  const unit = resolved ? f.final_unit_code : f.read_unit_code;
  const doubtful = pct !== undefined && (pct < 90 || f.status !== "OK");
  // What exactly needs a person: the product (not recognised) or the quantity (unclear).
  const badProduct = open && f.kind !== "MANUAL" && f.status === "UNMAPPED";
  const badQty = open && f.kind !== "MANUAL" && (f.status === "LOW_CONFIDENCE" || f.status === "UNREADABLE");
  const notes = lineNotes(f.detail);
  const tint = lit ? "bg-apple-50/30" : open && (f.status === "UNMAPPED" || f.status === "UNREADABLE") ? "bg-danger/[0.04]" : open && f.status === "LOW_CONFIDENCE" ? "bg-warning/[0.06]" : zebra ? "bg-subtle/60" : "bg-card";
  const td = "border-b border-r border-line px-2.5 py-2 align-top last:border-r-0";
  const small = "btn px-3 py-1 text-xs";
  const canTick = selectable(f);
  return (
    <>
      <tr className={`${tint} ${f.resolution === "REMOVED" ? "text-ink-muted" : ""}`} data-testid={`ocr-line-${f.seq}`}>
        {reviewing && (
          <td className={`${td} text-center`}>
            {canTick && (
              <input
                type="checkbox"
                className="mt-0.5 size-4 cursor-pointer accent-brand-primary"
                aria-label={`Select line ${f.seq}`}
                checked={picked}
                disabled={busy}
                onChange={(e) => onPick(e.target.checked)}
              />
            )}
          </td>
        )}
        <td className={`${td} num text-center text-xs text-ink-muted`}>{f.seq}</td>
        <td className={`${td} min-w-0`}>
          <p className={f.resolution === "REMOVED" ? "line-through" : ""}>
            {sku && !name.startsWith(`${sku} `) && <span className="num mr-1.5 text-xs font-semibold text-ink-muted">{sku}</span>}
            {badProduct ? (
              <span className="rounded bg-danger/8 px-1 underline decoration-danger decoration-dashed decoration-2 underline-offset-4" data-uncertain="product">
                {name || "Unknown product"}
                <span className="sr-only"> (product not recognised, check it)</span>
              </span>
            ) : (
              name
            )}
          </p>
          {f.kind !== "MANUAL" && f.raw_text && (
            <p className="text-xs text-ink-muted">
              Sheet says: <span className="text-ink">{`“${f.raw_text}”`}</span>
            </p>
          )}
          {open && f.status !== "OK" && <p className={`text-xs ${f.status === "LOW_CONFIDENCE" ? "text-warning" : "text-danger"}`}>{st.hint}</p>}
          {notes.length > 0 && <p className="text-xs text-ink">{notes.join(" · ")}</p>}
          {f.corrections.length > 0 && (
            <p className="text-xs text-ink-muted">
              {f.corrections.map((c) => `${c.action.toLowerCase()} by ${c.actor_name}${c.reason ? ` — “${c.reason}”` : ""}`).join("; ")}
            </p>
          )}
        </td>
        <td className={`${td} num whitespace-nowrap text-right`}>
          <span
            className={`font-semibold ${badQty ? `rounded px-1 underline decoration-dashed decoration-2 underline-offset-4 ${f.status === "UNREADABLE" ? "bg-danger/10 decoration-danger" : "bg-warning/15 decoration-warning"}` : ""}`}
            data-uncertain={badQty ? "quantity" : undefined}
          >
            {qty ? trimQty(qty) : "?"}
          </span>
          {badQty && <span className="sr-only"> (quantity unclear, check it)</span>}
          <span className="ml-1 text-xs text-ink-muted">{unit ?? ""}</span>
        </td>
        <td className={`${td} overflow-hidden`}>
          {/* Waiting: how it was read (Clear / Unclear / Unknown product); decided: what was done. */}
          {open && f.kind !== "MANUAL" ? <StatusChip tone={st.tone}>{st.label}</StatusChip> : <StatusChip tone={res.tone}>{res.label}</StatusChip>}
          {pct !== undefined && f.kind !== "MANUAL" && open && (
            <span className={`mt-1 block text-xs ${doubtful ? "font-medium text-warning" : "text-ink-muted"}`} title={doubtful ? "Check against the original" : undefined}>
              {`${pct}% sure`}
            </span>
          )}
        </td>
        {reviewing && (
          <td className={td}>
            {!editor && (
              <span className="flex items-center justify-end gap-0.5">
                {open && canConfirm(f) && (
                  <button className="grid size-7 shrink-0 cursor-pointer place-items-center rounded-full bg-brand-primary text-white hover:bg-brand-primary-hover disabled:opacity-50" disabled={busy} onClick={onConfirm} aria-label={`Confirm line ${f.seq}`} title="Confirm as read">
                    <Icon name="check" size={15} />
                  </button>
                )}
                <button className={`${small} shrink-0 border border-line bg-card text-ink hover:bg-subtle`} disabled={busy} onClick={onCorrect} aria-label={`${open && !canConfirm(f) ? "Enter" : "Correct"} line ${f.seq}`}>
                  {open && !canConfirm(f) ? "Enter" : "Correct"}
                </button>
                {f.resolution !== "REMOVED" && (
                  <button className="grid size-7 shrink-0 cursor-pointer place-items-center rounded-full text-ink-muted hover:bg-line hover:text-ink disabled:opacity-50" disabled={busy} onClick={onRemove} aria-label={`Remove line ${f.seq}`} title="Remove this line">
                    <Icon name="trash" size={15} />
                  </button>
                )}
              </span>
            )}
          </td>
        )}
      </tr>
      {editor && (
        <tr>
          <td colSpan={reviewing ? 6 : 4} className="border-b border-line bg-subtle p-3">
            {editor}
          </td>
        </tr>
      )}
    </>
  );
}

function LineEditor({
  edit,
  setEdit,
  products,
  product,
  onProduct,
  reasonNeeded,
  busy,
  onSave,
}: {
  edit: Editing;
  setEdit: (fn: (e: Editing | null) => Editing | null) => void;
  products: ProductSummary[];
  product: Product | undefined;
  onProduct: (pid: string) => void;
  reasonNeeded: boolean;
  busy: boolean;
  onSave: () => void;
}) {
  const units = product ? stockUnits(product) : [];
  const whole = !!edit.unit_code && wholeOnly(edit.unit_code);
  const set = (v: Partial<Editing>) => setEdit((e) => e && { ...e, ...v });
  return (
    <form
      className="grid items-end gap-3 rounded-ctl border border-line bg-card p-3 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_auto]"
      aria-label={edit.fieldId === "new" ? "Add a missed line" : "Correct line"}
      onSubmit={(e) => {
        e.preventDefault();
        onSave();
      }}
    >
      <div>
        <label className="label" htmlFor="ed-p">Product</label>
        <select id="ed-p" className="input" value={edit.product_id} onChange={(e) => onProduct(e.target.value)} autoFocus={!edit.product_id}>
          <option value="">Choose…</option>
          {products.map((x) => <option key={x.id} value={x.id}>{x.sku} — {x.name}</option>)}
        </select>
      </div>
      <div>
        <label className="label" htmlFor="ed-u">Unit</label>
        <select id="ed-u" className="input" value={edit.unit_code} onChange={(e) => set({ unit_code: e.target.value as UnitCode })}>
          {units.length === 0 && <option value="">—</option>}
          {units.map((u) => <option key={u} value={u}>{u}</option>)}
        </select>
      </div>
      <NumericInput label="Quantity" value={edit.qty} onChange={(v) => set({ qty: v })} places={whole ? 0 : 4} invalidHint={whole ? "Whole numbers only" : undefined} />
      <div className="flex gap-2">
        <button type="button" className="btn-ghost" onClick={() => setEdit(() => null)}>Cancel</button>
        <button className="btn-primary" disabled={busy}>{edit.fieldId === "new" ? "Add line" : "Save line"}</button>
      </div>
      {reasonNeeded && (
        <div className="md:col-span-4">
          <label className="label" htmlFor="ed-r">Why was it missed? (required)</label>
          <input id="ed-r" className="input" value={edit.reason} onChange={(e) => set({ reason: e.target.value })} maxLength={500} />
        </div>
      )}
    </form>
  );
}
