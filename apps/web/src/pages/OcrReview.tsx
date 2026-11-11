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
import { canConfirm, confidencePct, FIELD_STATUS, needsReason, OCR_STATUS, ocrMessage, openLines, pctText, RESOLUTION, trimQty, warningText } from "../lib/ocr";
import { DataGrid, type Column } from "../ui/DataGrid";
import { NumericInput } from "../ui/NumericInput";
import { Icon } from "../ui/Icon";
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

/** Review page (route /scan/review/:id), or embedded under a finished scan in the Scan Center. */
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
    api.products({ active: true, limit: 200 }).then((p) => setProducts(p.items), () => setProducts([]));
    api.customers({ limit: 200 }).then((c) => setCustomers(c.filter((x) => x.active)), () => setCustomers([]));
  }, []);

  const product = async (pid: string): Promise<Product | undefined> => {
    if (!pid) return undefined;
    const have = details[pid];
    if (have) return have;
    const p = await api.product(pid);
    setDetails((d) => ({ ...d, [pid]: p }));
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
    if (f && needsReason(f, "CORRECT") && edit.reason.trim().length < 3) return setError("Say why the reading is wrong (at least 3 characters).");
    const r = await run(
      () =>
        api.resolveOcrField(ex.id, edit.fieldId, {
          version: ex.version,
          action: "CORRECT",
          product_id: edit.product_id,
          unit_code: edit.unit_code as UnitCode,
          qty,
          reason: edit.reason.trim() || undefined,
        }),
      `Line ${f?.seq ?? ""} corrected.`,
    );
    if (r) setEdit(null);
  };

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
      reasonNeeded={edit.fieldId === "new" || ex.fields.some((f) => f.id === edit.fieldId && needsReason(f, "CORRECT"))}
      busy={busy}
      onSave={() => void saveEdit()}
    />
  );

  const Info = embedded ? QuietNotice : Notice;
  const low = ex.fields.filter((f) => f.resolution === "NONE" && f.status === "LOW_CONFIDENCE").length;
  const unknown = ex.fields.filter((f) => f.resolution === "NONE" && (f.status === "UNMAPPED" || f.status === "UNREADABLE")).length;
  const custName = customers.find((c) => c.id === customer)?.name ?? ex.customer_name;

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

      <div className={embedded ? "space-y-5" : "grid gap-5 xl:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]"}>
        {!embedded && <section aria-label="Original scan" className="card min-h-[24rem] overflow-hidden xl:sticky xl:top-4 xl:h-[calc(100vh-8rem)]">
          <DocViewer documentId={ex.document_id} title={`Original scan ${ex.scan_job_no}`} highlight={lit ?? null} flags={reviewing ? flags : []} initialPage={startPage} onFlag={showLine} />
        </section>}

        <div className="space-y-4">
          {supplierDoc && <SupplierHeader ex={ex} threshold={0.9} editable={reviewing} onFixed={setEx} />}
          <section aria-labelledby="lines-h" className="card overflow-hidden">
            <div className="flex flex-wrap items-center gap-4 p-5 sm:p-6">
              <span className="grid size-14 shrink-0 place-items-center rounded-ctl bg-brand-primary text-white"><Icon name="scan" size={26} /></span>
              <div className="min-w-0 flex-1">
                <h2 id="lines-h" className="text-[22px] font-semibold tracking-tight">Recognized lines</h2>
                <p className="text-sm text-ink-muted">
                  Check each line against the scan and fix what was misread.{" "}
                  <span aria-live="polite">{reviewing ? (open.length ? `${open.length} of ${ex.fields.length} still to check` : "All lines checked") : `${ex.fields.length} lines`}</span>
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                {low > 0 && <span className="inline-flex items-center gap-2 rounded-full bg-warning/12 px-4 py-2 text-sm font-semibold text-ink [&_svg]:text-warning"><Icon name="clock" size={16} />{low} low-confidence</span>}
                {unknown > 0 && <span className="inline-flex items-center gap-2 rounded-full bg-danger/10 px-4 py-2 text-sm font-semibold text-danger"><Icon name="alert" size={16} />{unknown} need{unknown === 1 ? "s" : ""} fixing</span>}
              </div>
            </div>
            {ex.fields.length > 0 && (
              <div aria-hidden className="hidden grid-cols-[3.25rem_minmax(0,2.2fr)_minmax(0,1.2fr)_minmax(0,1.5fr)] gap-4 border-y border-line bg-brand-accent/8 px-6 py-3 text-sm font-semibold md:grid">
                <span />
                <span>Product / line</span>
                <span>Read as</span>
                <span>Status</span>
              </div>
            )}
            {ex.fields.length === 0 ? (
              <p className="px-6 pb-6 text-sm text-ink-muted">No lines were read.</p>
            ) : (
              <ol
                className="divide-y divide-line"
                onMouseOver={(e) => {
                  const li = (e.target as HTMLElement).closest("[data-testid^='ocr-line-']");
                  if (li) setFocusSeq(Number(li.getAttribute("data-testid")?.slice(9)));
                }}
                onFocus={(e) => {
                  const li = (e.target as HTMLElement).closest("[data-testid^='ocr-line-']");
                  if (li) setFocusSeq(Number(li.getAttribute("data-testid")?.slice(9)));
                }}
              >
                {ex.fields.map((f) => (
                  <FieldRow
                    key={f.id}
                    f={f}
                    reviewing={reviewing}
                    busy={busy}
                    editing={edit?.fieldId === f.id}
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
              </ol>
            )}
            {reviewing && (
              <div className="border-t border-line px-6 py-4">
                {edit?.fieldId === "new" ? (
                  editor
                ) : (
                  <button className="btn-ghost" disabled={busy} onClick={() => void startEdit(null)}>
                    <Icon name="plus" size={18} />Add a line the reading missed
                  </button>
                )}
              </div>
            )}
            <div className="m-4 mt-0 grid gap-4 rounded-ctl bg-brand-accent/8 p-5 sm:grid-cols-3">
              {([
                ["check", "Lines kept", kept.length],
                ["alert", "Still to check", open.length],
                ["users", "Customer", custName ?? "Not chosen yet"],
              ] as const).map(([icon, k, v], i) => (
                <div key={k} className={`flex items-center gap-4 ${i > 0 ? "sm:border-l sm:border-line sm:pl-4" : ""}`}>
                  <span className="grid size-12 shrink-0 place-items-center rounded-ctl bg-card text-brand-primary"><Icon name={icon} size={22} /></span>
                  <span className="min-w-0">
                    <span className="block text-sm text-ink-muted">{k}</span>
                    <span className="num block truncate text-[22px] font-semibold leading-tight">{v}</span>
                  </span>
                </div>
              ))}
            </div>
          </section>

          {supplierDoc && (reviewing || ex.shipment_id) && (
            <>
              <section aria-label="Prices as read" className="card p-6">
                <PriceList fields={ex.fields} />
              </section>
              <ToReceiving ex={ex} ready={open.length === 0 && kept.length > 0} canReceive={canReceive} onDone={setEx} />
            </>
          )}
          {reviewing && !supplierDoc && (
            <section aria-labelledby="accept-h" className="card space-y-3 p-6">
              <h2 id="accept-h" className="font-semibold">Create the draft order</h2>
              <p className="text-sm text-ink-muted">
                This only creates a <strong>draft</strong> sales order with {kept.length} line{kept.length === 1 ? "" : "s"}. Nothing is reserved, picked or invoiced until someone confirms it.
              </p>
              <div className="grid gap-3 md:grid-cols-2">
                <div>
                  <label className="label" htmlFor="ocr-cust">Customer</label>
                  <select id="ocr-cust" className="input" value={customer} onChange={(e) => setCustomer(e.target.value)}>
                    <option value="">Choose…</option>
                    {customers.map((c) => <option key={c.id} value={c.id}>{c.external_ref ? `${c.external_ref} — ` : ""}{c.name}</option>)}
                  </select>
                </div>
                <div>
                  <label className="label" htmlFor="ocr-note">Note (optional)</label>
                  <input id="ocr-note" className="input" value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} />
                </div>
              </div>
              <label className="flex items-start gap-2 text-sm">
                <input type="checkbox" className="mt-0.5" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
                I checked every line against the scan.
              </label>
              <div className="flex flex-wrap justify-end gap-2">
                <button className="btn-ghost" disabled={busy} onClick={() => { setReason(""); setRejecting(true); }}>Reject</button>
                <button className="btn-ghost" disabled={busy} onClick={() => setRereading(true)}>Read again</button>
                {canAccept ? (
                  <button className="btn-primary" disabled={busy || open.length > 0 || kept.length === 0 || !checked || !customer} onClick={() => void accept()}>
                    Create draft order
                  </button>
                ) : (
                  <span className="self-center text-sm text-ink-muted">Someone who can manage orders must create the draft.</span>
                )}
              </div>
              {open.length > 0 && <p className="text-right text-xs text-ink-muted">Check lines {open.join(", ")} first.</p>}
            </section>
          )}
          {ex.status === "FAILED" && (
            <div className="flex justify-end gap-2">
              <button className="btn-ghost" disabled={busy} onClick={() => { setReason(""); setRejecting(true); }}>Reject</button>
              <button className="btn-primary" disabled={busy} onClick={() => setRereading(true)}>Read again</button>
            </div>
          )}
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

function FieldRow({
  f,
  reviewing,
  busy,
  editing,
  editor,
  onConfirm,
  onCorrect,
  onRemove,
}: {
  f: OcrField;
  reviewing: boolean;
  busy: boolean;
  editing: boolean;
  editor: React.ReactNode;
  onConfirm: () => void;
  onCorrect: () => void;
  onRemove: () => void;
}) {
  const st = FIELD_STATUS[f.status];
  const res = RESOLUTION[f.resolution];
  const pct = confidencePct(f.confidence);
  const open = f.resolution === "NONE";
  const title = f.resolution !== "NONE" && f.resolution !== "REMOVED" && f.final_product_name ? f.final_product_name : f.kind === "MANUAL" ? "Added by hand" : f.label;
  const tint = open && (f.status === "UNMAPPED" || f.status === "UNREADABLE") ? "bg-danger/[0.04]" : open && f.status === "LOW_CONFIDENCE" ? "bg-warning/[0.06]" : "";
  const doubtful = pct !== undefined && (pct < 90 || f.status !== "OK");
  // What exactly needs a person: the product (not recognised) or the quantity (unclear).
  const badProduct = open && f.kind !== "MANUAL" && f.status === "UNMAPPED";
  const badQty = open && f.kind !== "MANUAL" && (f.status === "LOW_CONFIDENCE" || f.status === "UNREADABLE");
  return (
    <li className={`space-y-3 px-5 py-4 sm:px-6 ${tint} ${f.resolution === "REMOVED" ? "opacity-70" : ""}`} data-testid={`ocr-line-${f.seq}`}>
      <div className="grid grid-cols-[3.25rem_minmax(0,1fr)] gap-x-4 gap-y-3 md:grid-cols-[3.25rem_minmax(0,2.2fr)_minmax(0,1.2fr)_minmax(0,1.5fr)] md:items-center">
        <span className="relative grid size-[3.25rem] place-items-center rounded-ctl border border-line bg-subtle text-ink-muted">
          <Icon name="inventory" size={22} />
          <span className="num absolute -right-1.5 -top-1.5 grid size-6 place-items-center rounded-full bg-card text-xs font-semibold text-ink ring-1 ring-line">{f.seq}</span>
        </span>
        <div className="min-w-0 space-y-0.5">
          <p className="truncate text-base font-semibold">
            {badProduct ? (
              <span className="rounded bg-danger/8 px-1 underline decoration-danger decoration-dashed decoration-2 underline-offset-4" data-uncertain="product">
                {title || "Unknown product"}
                <span className="sr-only"> (product not recognised, check it)</span>
              </span>
            ) : (
              title
            )}
          </p>
          <p className="text-sm text-ink-muted">
            {f.kind === "MANUAL" ? "Added by hand" : <>Sheet says: <span className="text-ink">{`${f.label}${f.raw_text && f.raw_text !== f.label ? ` · “${f.raw_text}”` : ""}`}</span></>}
          </p>
          {f.resolution !== "NONE" && f.resolution !== "REMOVED" && (
            <p className="text-sm">
              <span className="text-ink-muted">On order: </span>
              <span className="num font-semibold">{f.final_sku} · {f.final_qty ? trimQty(f.final_qty) : ""} {f.final_unit_code}</span>
            </p>
          )}
          {open && f.status !== "OK" && <p className={`text-sm ${f.status === "LOW_CONFIDENCE" ? "text-ink" : "text-danger"}`}>{st.hint}</p>}
          {f.corrections.length > 0 && (
            <p className="text-xs text-ink-muted">
              {f.corrections.map((c) => `${c.action.toLowerCase()} by ${c.actor_name}${c.reason ? ` — “${c.reason}”` : ""}`).join("; ")}
            </p>
          )}
        </div>
        <div className="col-start-2 min-w-0 md:col-start-auto">
          {f.kind !== "MANUAL" && (
            <>
              <p className="text-sm">
                <span className="text-ink-muted md:hidden">Read as: </span>
                <span className={`num inline-block whitespace-nowrap rounded-ctl border bg-card px-3 py-1.5 font-semibold ${badQty ? (f.status === "UNREADABLE" ? "border-danger ring-2 ring-danger/20" : "border-warning ring-2 ring-warning/25") : badProduct ? "border-danger/60" : "border-line"}`} data-uncertain={badQty ? "quantity" : badProduct ? "product" : undefined}>
                  {f.read_sku ?? "?"} ·{" "}
                  <span className={badQty ? "rounded bg-warning/15 px-1 underline decoration-warning decoration-dashed decoration-2 underline-offset-4" : undefined}>{f.read_qty ? trimQty(f.read_qty) : "?"}</span> {f.read_unit_code ?? ""}
                  {badQty && <span className="sr-only"> (quantity unclear, check it)</span>}
                </span>
              </p>
              {pct !== undefined && (
                doubtful ? (
                  <p className="mt-2 inline-flex items-center gap-1.5 rounded-ctl bg-warning/12 px-2.5 py-1 text-xs font-medium text-ink [&_svg]:text-warning">
                    <Icon name="alert" size={14} />OCR {pct}% · check against original
                  </p>
                ) : (
                  <p className="mt-2 text-xs text-ink-muted">{pct}% sure</p>
                )
              )}
            </>
          )}
        </div>
        <div className="col-start-2 flex flex-wrap items-center gap-1.5 md:col-start-auto">
          {f.kind !== "MANUAL" && <StatusChip tone={st.tone}>{st.label}</StatusChip>}
          <StatusChip tone={res.tone}>{res.label}</StatusChip>
        </div>
      </div>
      {reviewing && !editing && (
        <div className="flex flex-wrap justify-end gap-2">
          {open && canConfirm(f) && (
            <button className="btn-accent py-2" disabled={busy} onClick={onConfirm} aria-label={`Confirm line ${f.seq}`}><Icon name="check" size={18} />Confirm</button>
          )}
          <button className="btn-ghost py-2" disabled={busy} onClick={onCorrect} aria-label={`${open && !canConfirm(f) ? "Enter" : "Correct"} line ${f.seq}`}>
            {open && !canConfirm(f) ? "Enter correct line" : "Correct"}
          </button>
          {f.resolution !== "REMOVED" && (
            <button className="btn-ghost py-2" disabled={busy} onClick={onRemove} aria-label={`Remove line ${f.seq}`}>Remove</button>
          )}
        </div>
      )}
      {editing && editor}
    </li>
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
      className="grid items-end gap-3 rounded-ctl border border-line bg-subtle p-4 md:grid-cols-[2fr_1fr_1fr]"
      aria-label={edit.fieldId === "new" ? "Add a missed line" : "Correct line"}
      onSubmit={(e) => {
        e.preventDefault();
        onSave();
      }}
    >
      <div>
        <label className="label" htmlFor="ed-p">Product</label>
        <select id="ed-p" className="input" value={edit.product_id} onChange={(e) => onProduct(e.target.value)}>
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
      {reasonNeeded && (
        <div className="md:col-span-3">
          <label className="label" htmlFor="ed-r">{edit.fieldId === "new" ? "Why was it missed? (required)" : "Why is the reading wrong? (required)"}</label>
          <input id="ed-r" className="input" value={edit.reason} onChange={(e) => set({ reason: e.target.value })} maxLength={500} />
        </div>
      )}
      <div className="flex justify-end gap-2 md:col-span-3">
        <button type="button" className="btn-ghost" onClick={() => setEdit(() => null)}>Cancel</button>
        <button className="btn-primary" disabled={busy}>{edit.fieldId === "new" ? "Add line" : "Save line"}</button>
      </div>
    </form>
  );
}
