import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, canInWarehouse, has, scanDocumentUrl, type OcrExtraction, type ScanJob, type ScanJobType, type Warehouse } from "../api";
import { useAuth } from "../auth";
import { UploadQueue } from "./ScanProgress";
import { IsoScanner } from "../components/scanner/IsoScanner";
import { ScanProcessing } from "../components/scanner/ScanProcessing";
import { ACCEPT, AUTO_READ, checkFile, failHint, fileSize, isOpen, JOB_STATUS, JOB_TYPE, scanMessage } from "../lib/scans";
import { DataGrid, type Column } from "../ui/DataGrid";
import { OcrReviewPage, ReviewQueue, ScanReading } from "./OcrReview";
import { OCR_STATUS } from "../lib/ocr";
import { gmail, gmailMessage, previousOf, type GmailAttachment, type GmailStatus } from "../lib/gmail";
import { GmailBrowser } from "../components/gmail/GmailBrowser";
import { Modal } from "../components/gmail/PagePreview";
import { useTrashChanges } from "../lib/trash";
import { DeleteAction } from "../ui/DeleteAction";
import { Icon, type IconName } from "../ui/Icon";
import { ConfirmDialog, EmptyState, ErrorState, FilterBar, LoadingRows, PageHeader, PageSkeleton, StatusChip } from "../ui/primitives";

const link = "font-medium text-brand-primary-hover underline-offset-2 hover:underline";
const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—";
const POLL_MS = 3000;

const JobChip = ({ j }: { j: Pick<ScanJob, "status"> }) => <StatusChip tone={JOB_STATUS[j.status].tone}>{JOB_STATUS[j.status].label}</StatusChip>;

function Notice({ tone, children }: { tone: "error" | "ok"; children: React.ReactNode }) {
  return (
    <p role={tone === "error" ? "alert" : "status"} className={`rounded-ctl border px-3 py-2 text-sm ${tone === "error" ? "border-danger/40 bg-danger/5 text-danger" : "border-success/40 bg-success/5 text-ink"}`}>
      {children}
    </p>
  );
}

/* ================================ Scan Center ================================ */

// The network-scanner bridge is not used: no Scanners tab (paired devices stay on the server).
type Tab = "scans" | "review";
const TAB_LABEL: Record<Tab, string> = { scans: "New scan", review: "To review" };

export function ScansPage() {
  const { session } = useAuth();
  const [params, setParams] = useSearchParams();
  const canReview = !!session && has(session, "scan.review");
  const tabs: Tab[] = ["scans", ...(canReview ? (["review"] as const) : [])];
  const want = params.get("tab") as Tab | null;
  const tab: Tab = want && tabs.includes(want) ? want : "scans";
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  useEffect(() => {
    api.warehouses().then((w) => setWarehouses(w.filter((x) => x.active)), () => setWarehouses([]));
  }, []);
  if (!session) return null;
  return (
    <div className="space-y-5">
      <PageHeader title="Scan Center" subtitle="Scans never change stock by themselves." />
      {tabs.length > 1 && (
        <div role="tablist" aria-label="Scan Center sections" className="flex gap-2">
          {tabs.map((t) => (
            <button
              key={t}
              role="tab"
              aria-selected={tab === t}
              className={`cursor-pointer border-b-[3px] px-4 pb-2.5 pt-1 text-base font-semibold ${tab === t ? "border-brand-accent text-ink" : "border-transparent text-ink-muted hover:text-ink"}`}
              onClick={() => setParams(t === "scans" ? {} : { tab: t })}
            >
              {TAB_LABEL[t]}
            </button>
          ))}
        </div>
      )}
      {tab === "scans" ? <ScansTab warehouses={warehouses} /> : <ReviewQueue warehouses={warehouses} />}
    </div>
  );
}

/* ---------------------------- Scans tab ---------------------------- */

type View = "all" | "open" | "UPLOADED" | "FAILED" | "closed";
const VIEWS: { value: View; label: string }[] = [
  { value: "all", label: "All" },
  { value: "open", label: "In progress" },
  { value: "UPLOADED", label: "Received" },
  { value: "FAILED", label: "Failed" },
  { value: "closed", label: "Cancelled / expired" },
];
const VIEW_STATUS: Record<View, string | undefined> = {
  all: undefined,
  open: "REQUESTED,SCANNING",
  UPLOADED: "UPLOADED",
  FAILED: "FAILED",
  closed: "CANCELLED,EXPIRED",
};

function ScansTab({ warehouses }: { warehouses: Warehouse[] }) {
  const { session } = useAuth();
  const nav = useNavigate();
  const [view, setView] = useState<View>("all");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<ScanJob[] | null>(null);
  const [total, setTotal] = useState(0);
  const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState(false);

  const load = useCallback(
    async (cursor?: string) => {
      try {
        const r = await api.scanJobs({ status: VIEW_STATUS[view], q: q.trim() || undefined, cursor });
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
  useTrashChanges(load);
  useEffect(() => {
    const t = setTimeout(() => void load(), 200);
    return () => clearTimeout(t);
  }, [load]);
  // Keep in-progress scans fresh without a manual reload.
  const anyOpen = rows?.some(isOpen) ?? false;
  useEffect(() => {
    if (!anyOpen) return;
    const t = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(t);
  }, [anyOpen, load]);

  const createWh = warehouses.filter((w) => session && canInWarehouse(session, "scan.create", w.id));
  const columns: Column<ScanJob>[] = [
    { key: "n", header: "Scan", cell: (r) => <Link className={link} to={`/scan/${r.id}`}>{r.job_no}</Link>, sort: (r) => r.job_no },
    { key: "t", header: "Document", cell: (r) => JOB_TYPE[r.job_type] },
    { key: "c", header: "Source", cell: (r) => (r.channel === "BRIDGE" ? r.device_name ?? "Scanner" : "Upload") },
    { key: "by", header: "By", cell: (r) => r.requested_by_name },
    { key: "d", header: "Started", cell: (r) => when(r.created_at), sort: (r) => r.created_at },
    { key: "s", header: "Status", cell: (r) => <JobChip j={r} /> },
    {
      key: "delete",
      header: "",
      align: "right",
      // The server refuses while the scanner may still be working on it.
      cell: (r) => (isOpen(r) ? null : <DeleteAction compact kind="scan" id={r.id} label={`Scan ${r.job_no}`} warehouseId={r.warehouse_id} />),
    },
  ];
  return (
    <div className="space-y-5">
      {createWh.length > 0 && <ScanStudio warehouses={createWh} onChanged={() => void load()} />}
      <FilterBar search={q} onSearch={setQ} options={VIEWS} value={view} onChange={setView} placeholder="Scan no. or file name" />
      <section className="card overflow-clip">
        {error ? (
          <ErrorState title="Couldn't load scans" onRetry={() => void load()} />
        ) : rows === null ? (
          <LoadingRows cols={7} />
        ) : (
          <>
            <DataGrid
              rows={rows}
              columns={columns}
              rowKey={(r) => r.id}
              caption={`Scans (${total})`}
              onOpen={(r) => nav(`/scan/${r.id}`)}
              empty={<EmptyState title="No scans yet" hint="Scan a document from the office scanner, or upload a PDF or photo." />}
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

/* ---------------------------- Scan studio ---------------------------- */

/** While working, the animated scanner follows the browser's own upload bytes and then the
 * server's scan (bridge: waiting → scanning); nothing is estimated. */
type Phase =
  | { kind: "idle" }
  | { kind: "working"; mode: "upload" | "import"; label: string; jobId: string | null }
  | { kind: "done"; job: ScanJob; duplicate: boolean };

/** The studio's picture: the same isometric scanner that follows each scan, at rest. */
function ScannerArt() {
  return (
    <div className="sc-stage grid h-full min-h-[15rem] place-items-center overflow-hidden rounded-ctl px-2" aria-hidden>
      <div className="w-full max-w-[22rem]">
        <IsoScanner scene="idle" total={0} read={0} current={null} uploadPct={null} matched={null} duplicate={false} retrying={false} label="" decorative />
      </div>
    </div>
  );
}

function FileButton({ label, disabled, inputRef, onFile, onFiles, primary = false }: { label: string; disabled: boolean; inputRef: React.RefObject<HTMLInputElement | null>; onFile: (f: File) => void; onFiles?: (f: File[]) => void; primary?: boolean }) {
  return (
    <label className={`${primary ? "btn-accent" : "btn-ghost"} cursor-pointer px-6 py-3 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-brand-primary ${disabled ? "pointer-events-none opacity-50" : ""}`}>
      <Icon name="upload" />
      {label}
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPT}
        className="sr-only"
        data-testid="scan-upload"
        disabled={disabled}
        multiple={!!onFiles}
        onChange={(e) => {
          const all = Array.from(e.target.files ?? []);
          if (all.length > 1 && onFiles) onFiles(all);
          else if (all[0]) onFile(all[0]);
        }}
      />
    </label>
  );
}

function ScanStudio({ warehouses, onChanged }: { warehouses: Warehouse[]; onChanged: () => void }) {
  // No location field: scans go to the first warehouse this user may scan in.
  const wh = warehouses[0]?.id ?? "";
  const [type, setType] = useState<ScanJobType>("SALES_ORDER");
  const [note, setNote] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [msg, setMsg] = useState<{ tone: "error" | "ok"; text: string; jobId?: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [upPct, setUpPct] = useState<number | null>(null);
  const [queue, setQueue] = useState<File[] | null>(null);

  const [importing, setImporting] = useState(false);

  /** Scan Box › Import: the server fetches the emailed file and reads it, exactly like an upload. */
  async function importFrom(a: GmailAttachment) {
    setImporting(false);
    if (type === "OTHER") return;
    setPhase({ kind: "working", mode: "import", label: a.filename, jobId: null });
    setMsg(null);
    try {
      const out = await gmail.process(a.id, { warehouse_id: wh, category: type });
      const job = await api.scanJob(out.scan_job_id);
      setMsg({ tone: "ok", text: `${job.job_no} imported from the Scan Box.`, jobId: job.id });
      setPhase({ kind: "done", job, duplicate: false });
      setNote("");
    } catch (e) {
      const prev = previousOf(e);
      const p = prev[0];
      setMsg(
        p
          ? { tone: "error", text: `This file was already read as ${p.scan_job_no}.`, jobId: p.scan_job_id }
          : { tone: "error", text: gmailMessage(e) },
      );
      setPhase({ kind: "idle" });
    } finally {
      onChanged();
    }
  }

  async function upload(file: File) {
    const bad = checkFile(file);
    if (bad) {
      setMsg({ tone: "error", text: bad });
      if (fileRef.current) fileRef.current.value = "";
      return;
    }
    setPhase({ kind: "working", mode: "upload", label: file.name, jobId: null });
    setUpPct(0);
    setMsg(null);
    try {
      const j = note.trim()
        ? await api.uploadScan(file, { warehouse_id: wh, job_type: type, note: note.trim() })
        : await api.uploadScanProgress(file, { warehouse_id: wh, job_type: type }, setUpPct);
      setMsg({ tone: "ok", text: j.duplicate ? `This file was already uploaded as ${j.job_no}.` : `${j.job_no} uploaded.`, jobId: j.id });
      setPhase({ kind: "done", job: j, duplicate: j.duplicate });
      setNote("");
      onChanged();
    } catch (e) {
      setMsg({ tone: "error", text: scanMessage(e) });
      setPhase({ kind: "idle" });
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  const status = msg && (
    msg.tone === "error" ? (
      <p role="alert" className="flex items-start gap-2 rounded-ctl bg-danger/8 px-4 py-3 text-sm text-danger">
        <Icon name="alert" size={18} className="mt-0.5" />
        <span>{msg.text} {msg.jobId && <Link className={link} to={`/scan/${msg.jobId}`}>Open</Link>}</span>
      </p>
    ) : (
      <p role="status" className="text-base text-ink-muted">
        {msg.text} {msg.jobId && <Link className={link} to={`/scan/${msg.jobId}`}>Open</Link>}
      </p>
    )
  );

  if (phase.kind === "done") return <ScanDone job={phase.job} duplicate={phase.duplicate} status={status} fileRef={fileRef} onFile={(f) => void upload(f)} onNew={() => { setMsg(null); setPhase({ kind: "idle" }); }} />;
  if (phase.kind === "working")
    return (
      <ScanProcessing
        jobId={phase.jobId}
        local={phase.mode === "upload" ? { kind: "uploading", fileName: phase.label, fraction: upPct } : null}
        heading={phase.mode === "import" ? `Reading ${phase.label} from the Scan Box` : undefined}
        hideOpenLink
      />
    );

  return (
    <section className="card grid gap-6 p-6 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.6fr)]">
      <ScannerArt />
      <div className="flex min-w-0 flex-col gap-6 lg:border-l lg:border-line lg:pl-8">
        <div className="flex items-start gap-4">
          <span className="grid size-16 shrink-0 place-items-center rounded-ctl bg-brand-accent/12 text-brand-primary"><Icon name="upload" size={30} /></span>
          <div className="min-w-0">
            <h2 id="new-scan" className="text-[26px] font-semibold leading-tight tracking-tight">New scan</h2>
            <p className="mt-1 text-base text-ink-muted">Upload a file from this computer, or import one that arrived in the Scan Box.</p>
          </div>
        </div>

        <>
            <div className="grid gap-5 sm:grid-cols-2">
              <label>
                <span className="label">Document type</span>
                <select className="input py-3" value={type} onChange={(e) => setType(e.target.value as ScanJobType)}>
                  {(Object.keys(JOB_TYPE) as ScanJobType[]).map((t) => <option key={t} value={t}>{JOB_TYPE[t]}</option>)}
                </select>
              </label>
              <label className="sm:col-span-2">
                <span className="label">Note <span className="font-normal text-ink-muted">(optional)</span></span>
                <input className="input py-3" value={note} maxLength={500} placeholder="e.g. Nov 2 sales orders" onChange={(e) => setNote(e.target.value)} />
              </label>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <FileButton label="Upload files" disabled={!wh} inputRef={fileRef} onFile={(f) => void upload(f)} onFiles={(fs) => setQueue(fs)} primary />
              <button type="button" className="btn-ghost px-6 py-3" disabled={!wh || type === "OTHER"} title={type === "OTHER" ? "Choose what kind of document it is first" : undefined} onClick={() => setImporting(true)}>
                <Icon name="scanbox" />Import from Scan Box
              </button>
              <span className="basis-full text-sm text-ink-muted">PDF (any number of pages), PNG, JPEG or TIFF, up to 20 MB each. Pick several at once.</span>
            </div>
        </>
        {status}
        {importing && <ScanBoxImport onClose={() => setImporting(false)} onPick={(a) => void importFrom(a)} />}
        {queue && (
          <UploadQueue
            key={queue.map((f) => f.name).join("|")}
            files={queue}
            warehouseId={wh}
            jobType={type}
            onDone={() => {
              onChanged();
              if (fileRef.current) fileRef.current.value = "";
            }}
          />
        )}
      </div>
    </section>
  );
}

/** The Scan Box (the scanning mailbox) as a picker: choose one emailed document to read. */
function ScanBoxImport({ onClose, onPick }: { onClose: () => void; onPick: (a: GmailAttachment) => void }) {
  const { session } = useAuth();
  const allowed = !!session && has(session, "gmail.read");
  const [status, setStatus] = useState<GmailStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (allowed) gmail.status().then(setStatus, (err: unknown) => setError(gmailMessage(err)));
  }, [allowed]);
  return (
    <Modal open title="Import from Scan Box" onClose={onClose} wide>
      <div className="p-5">
        {!allowed ? (
          <EmptyState icon="scanbox" title="Your account can't open the Scan Box" hint="Ask an administrator, or upload the file instead." />
        ) : error ? (
          <ErrorState title="Couldn't reach the Scan Box" detail={error} />
        ) : !status ? (
          <LoadingRows rows={4} cols={2} />
        ) : status.message_count === 0 ? (
          <EmptyState icon="scanbox" title="The Scan Box is empty" hint="Documents emailed from the office scanner show here." />
        ) : !status.can_process ? (
          <EmptyState icon="scanbox" title="Your account can't read Scan Box documents" hint="Ask an administrator, or upload the file instead." />
        ) : (
          <GmailBrowser status={status} mode="picker" initialShow="attachments" onProcessed={() => undefined} onSelect={onPick} selectLabel="Import and read" />
        )}
      </div>
    </Modal>
  );
}

/** "Scan completed" card; for order sheets the recognized lines open underneath by themselves. A
 * scan that was never read (a re-upload of an older scan, an "Other" document) can be read here. */
function ScanDone({
  job,
  duplicate,
  status,
  fileRef,
  onFile,
  onNew,
}: {
  job: ScanJob;
  duplicate: boolean;
  status: React.ReactNode;
  fileRef: React.RefObject<HTMLInputElement | null>;
  onFile: (f: File) => void;
  onNew: () => void;
}) {
  const { session } = useAuth();
  const canRead = !!session && canInWarehouse(session, "scan.review", job.warehouse_id);
  const [ex, setEx] = useState<OcrExtraction | null>(null);
  const [checked, setChecked] = useState(false);
  const [starting, setStarting] = useState(false);
  const [readError, setReadError] = useState<string | null>(null);
  const below = useRef<HTMLDivElement>(null);
  const url = job.document_id ? scanDocumentUrl(job.document_id) : null;
  const reads = AUTO_READ.includes(job.job_type);
  // a fresh upload of a type that is read automatically always has a reading on the server
  const expected = reads && !duplicate;

  const load = useCallback(() => {
    if (!canRead) return;
    api.ocrExtractions({ scan_job_id: job.id, status: "QUEUED,NEEDS_REVIEW,FAILED,ACCEPTED,REJECTED" }).then(
      (r) => {
        setEx(r.items[0] ?? null);
        setChecked(true);
      },
      () => setChecked(true),
    );
  }, [canRead, job.id]);
  useEffect(() => {
    setEx(null);
    setChecked(false);
    setReadError(null);
    load();
  }, [load]);
  useEffect(() => {
    const waiting = ex ? ex.status === "QUEUED" : checked && expected;
    if (!waiting) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [ex, checked, expected, load]);
  async function readNow() {
    setStarting(true);
    setReadError(null);
    try {
      setEx(await api.readScan(job.id));
    } catch (e) {
      setReadError(scanMessage(e));
    } finally {
      setStarting(false);
    }
  }
  const readButton = (label: string) => (
    <button className="btn-primary mt-3" disabled={starting} onClick={() => void readNow()}>
      <Icon name="refresh" />
      {starting ? "Starting…" : label}
    </button>
  );
  const review = ex?.status === "NEEDS_REVIEW";
  useEffect(() => {
    if (review) below.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [review]);

  const details: [IconName, string, React.ReactNode][] = [
    ["file", "Document type", JOB_TYPE[job.job_type]],
    ["printer", "Source", job.channel === "BRIDGE" ? job.device_name ?? "Scanner" : "File upload"],
    ["layers", "Pages", ex?.page_count ?? job.page_count ?? "—"],
    ["documents", "File name", job.document_filename ?? "—"],
    ["user", "Scanned by", job.requested_by_name],
    ["calendar", "Scan date & time", when(job.completed_at ?? job.created_at)],
  ];

  return (
    <>
      {canRead && (ex || expected) && (
        <ScanProcessing
          jobId={job.id}
          duplicate={duplicate}
          hideOpenLink
          hideReview
          onProgress={(p) => {
            // the reading moved on: refresh the summary and, when ready, the review below
            if (p.reading?.id !== ex?.id || p.reading?.status !== ex?.status) load();
          }}
        />
      )}
      <section aria-labelledby="scan-done" className="card grid gap-6 p-6 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.6fr)]">
        <div className="flex min-h-[18rem] flex-col gap-3 rounded-ctl bg-brand-accent/10 p-3">
          <div className="flex items-center gap-2 rounded-ctl bg-card px-3 py-2.5 text-sm font-medium">
            <Icon name="file" size={18} className="shrink-0 text-ink-muted" />
            <span className="min-w-0 flex-1 truncate">{job.document_filename ?? job.job_no}</span>
            {url && <a className="grid size-8 place-items-center rounded-ctl hover:bg-subtle" href={url} target="_blank" rel="noreferrer" aria-label="Open the original in a new tab"><Icon name="arrowUpRight" size={16} /></a>}
          </div>
          <div className="min-h-0 flex-1 overflow-hidden rounded-ctl bg-card">
            {url && job.document_mime === "application/pdf" ? (
              <iframe title={`Preview of ${job.job_no}`} src={url} className="h-full min-h-[16rem] w-full" />
            ) : url && job.document_mime?.startsWith("image/") && job.document_mime !== "image/tiff" ? (
              <img alt={`Preview of ${job.job_no}`} src={url} className="mx-auto max-h-[22rem] object-contain" />
            ) : (
              <div className="grid h-full min-h-[16rem] place-items-center text-sm text-ink-muted">No preview for this file type.</div>
            )}
          </div>
        </div>

        <div className="flex min-w-0 flex-col gap-6 lg:border-l lg:border-line lg:pl-8">
          <div className="flex flex-wrap items-start gap-4">
            <span className="grid size-16 shrink-0 place-items-center rounded-ctl bg-brand-accent/12 text-brand-primary"><Icon name="check" size={32} /></span>
            <div className="min-w-0 flex-1">
              <h2 id="scan-done" className="text-[26px] font-semibold leading-tight tracking-tight">{duplicate ? "Already on file" : "Scan completed successfully"}</h2>
              <div className="mt-1">{status}</div>
            </div>
            <span className="inline-flex items-center gap-2 rounded-full bg-brand-accent/12 px-4 py-2 text-sm font-semibold text-brand-primary-hover">
              <Icon name="printer" size={16} />Scan no. {job.job_no}
            </span>
          </div>

          <div className="grid gap-6 xl:grid-cols-2">
            <div>
              <h3 className="mb-3 text-lg font-semibold">Document details</h3>
              <dl className="divide-y divide-line rounded-ctl border border-line">
                {details.map(([icon, k, v]) => (
                  <div key={k} className="flex items-center gap-3 px-4 py-2.5 text-sm">
                    <Icon name={icon} size={18} className="shrink-0 text-ink-muted" />
                    <dt className="text-ink-muted">{k}</dt>
                    <dd className="ml-auto min-w-0 truncate text-right font-medium">{v}</dd>
                  </div>
                ))}
              </dl>
            </div>
            <div>
              <h3 className="mb-3 text-lg font-semibold">OCR / extraction summary</h3>
              {!canRead ? (
                <p className="rounded-ctl bg-subtle p-4 text-sm text-ink-muted">
                  {reads ? "Someone who reviews scans will check the lines read from this scan." : "“Other” documents are kept but not read automatically."}
                </p>
              ) : !ex && checked && !expected ? (
                <div className="rounded-ctl bg-subtle p-4 text-sm" data-testid="scan-unread">
                  <p>
                    <strong>{reads ? `${job.job_no} hasn't been read yet.` : "“Other” documents aren't read automatically."}</strong> Read it to list the products and quantities for review. Nothing changes stock.
                  </p>
                  {readButton("Read this scan")}
                  {readError && <p className="mt-2 text-danger" role="alert">{readError}</p>}
                </div>
              ) : !ex || ex.status === "QUEUED" ? (
                <p className="rounded-ctl bg-subtle p-4 text-sm text-ink-muted">The recognized lines open below as soon as they're read.</p>
              ) : ex.status === "FAILED" ? (
                <p className="rounded-ctl bg-subtle p-4 text-sm text-ink-muted">Reading stopped. Read it again above, or enter the order by hand.</p>
              ) : (
                <dl className="divide-y divide-line rounded-ctl bg-brand-accent/10 text-sm">
                  {([
                    ["users", "Customer", ex.customer_name ?? "Not matched — choose below"],
                    ["orders", "Lines read", ex.field_count],
                    ["alert", "Still to check", ex.open_count],
                    ["check", "Reading", OCR_STATUS[ex.status].label],
                  ] as [IconName, string, React.ReactNode][]).map(([icon, k, v]) => (
                    <div key={k} className="flex items-center gap-3 px-4 py-2.5">
                      <Icon name={icon} size={18} className="shrink-0 text-brand-primary" />
                      <dt>{k}</dt>
                      <dd className="ml-auto truncate text-right font-semibold">{v}</dd>
                    </div>
                  ))}
                </dl>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3">
            {review && (
              <button className="btn-accent px-6 py-3 text-base" onClick={() => below.current?.scrollIntoView({ behavior: "smooth", block: "start" })}>
                <Icon name="check" />Review lines
              </button>
            )}
            {ex && !review && ex.status !== "QUEUED" && (
              <Link className="btn-ghost px-5 py-3" to={`/scan/review/${ex.id}`}><Icon name="orders" />Open reading</Link>
            )}
            <Link className="btn-ghost px-5 py-3" to={`/scan/${job.id}`}><Icon name="file" />Scan details</Link>
            {url && <a className="btn-ghost px-5 py-3" href={url} download={job.document_filename ?? undefined}><Icon name="download" />Download original</a>}
            <FileButton label="Upload another" disabled={false} inputRef={fileRef} onFile={onFile} />
            <button className="btn-ghost px-5 py-3" onClick={onNew}><Icon name="refresh" />New scan</button>
          </div>
        </div>
      </section>
      <div ref={below} className="scroll-mt-4">
        {review && ex && <OcrReviewPage id={ex.id} embedded />}
      </div>
    </>
  );
}

export function ScanJobPage() {
  const { id = "" } = useParams();
  const nav = useNavigate();
  const [job, setJob] = useState<ScanJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const load = useCallback(async () => {
    try {
      setJob(await api.scanJob(id));
      setError(null);
    } catch (e) {
      setError(scanMessage(e));
    }
  }, [id]);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    if (!job || !isOpen(job)) return;
    const t = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(t);
  }, [job, load]);

  if (error && !job) return <ErrorState title="Couldn't load this scan" detail={error} onRetry={() => void load()} />;
  if (!job) return <PageSkeleton />;
  const url = job.document_id ? scanDocumentUrl(job.document_id) : null;
  const inline = job.document_mime === "application/pdf" || job.document_mime === "image/png" || job.document_mime === "image/jpeg";
  return (
    <div className="space-y-5">
      <PageHeader
        title={`Scan ${job.job_no}`}
        subtitle={<><Link className={link} to="/scan">Scan Center</Link> · {JOB_TYPE[job.job_type]}</>}
        actions={
          <>
            {job.status === "REQUESTED" && <button className="btn-ghost" onClick={() => setConfirm(true)}>Cancel scan</button>}
            {!isOpen(job) && <DeleteAction kind="scan" id={job.id} label={`Scan ${job.job_no}`} warehouseId={job.warehouse_id} onDone={() => nav("/scan")} />}
          </>
        }
      />
      {error && <Notice tone="error">{error}</Notice>}
      <ScanProcessing
        jobId={job.id}
        hideOpenLink
        onProgress={(p) => {
          if (p.status !== job.status) void load();
        }}
        onRestart={() => nav("/scan")}
        restartLabel="New scan"
      />
      {AUTO_READ.includes(job.job_type) && <ScanReading jobId={job.id} warehouseId={job.warehouse_id} received={job.status === "UPLOADED"} />}
      <div className="grid gap-5 lg:grid-cols-[20rem_1fr]">
        <dl className="card grid h-fit grid-cols-[auto_1fr] gap-x-4 gap-y-2 p-6 text-sm">
          <dt className="text-ink-muted">Status</dt>
          <dd><JobChip j={job} /></dd>
          <dt className="text-ink-muted">Source</dt>
          <dd>{job.channel === "BRIDGE" ? `Scanner · ${job.device_name ?? "—"}` : "File upload"}</dd>
          <dt className="text-ink-muted">Requested by</dt>
          <dd>{job.requested_by_name}</dd>
          <dt className="text-ink-muted">Started</dt>
          <dd>{when(job.created_at)}</dd>
          <dt className="text-ink-muted">Finished</dt>
          <dd>{when(job.completed_at)}</dd>
          {job.note && (<><dt className="text-ink-muted">Note</dt><dd>{job.note}</dd></>)}
          {job.document_id && (
            <>
              <dt className="text-ink-muted">File</dt>
              <dd className="break-all">{job.document_filename} · {fileSize(job.document_size)}</dd>
              <dt className="text-ink-muted">SHA-256</dt>
              <dd className="num break-all text-xs" title="Fingerprint of the original file">{job.document_sha256}</dd>
            </>
          )}
        </dl>
        <section aria-label="Document" className="card min-h-[24rem] overflow-hidden">
          {job.status === "FAILED" ? (
            <div className="space-y-2 p-5" role="alert">
              <p className="font-medium text-danger">The scan failed{job.error_code ? ` (${job.error_code})` : ""}.</p>
              <p className="text-sm">{failHint(job.error_code)}</p>
              {job.error_detail && <p className="text-xs text-ink-muted">Scanner said: {job.error_detail}</p>}
              <Link className="btn-ghost" to="/scan">Back to Scan Center</Link>
            </div>
          ) : isOpen(job) ? (
            <div className="grid h-full place-items-center p-6 text-sm text-ink-muted" role="status">Waiting for the scanner…</div>
          ) : url && inline ? (
            job.document_mime === "application/pdf" ? (
              <iframe title={`Scanned document ${job.job_no}`} src={url} className="h-[70vh] w-full" />
            ) : (
              <img alt={`Scanned document ${job.job_no}`} src={url} className="mx-auto max-h-[70vh]" />
            )
          ) : url ? (
            <div className="grid h-full place-items-center gap-2 p-6 text-sm">
              <p>This file type can't be shown in the browser.</p>
              <a className="btn-ghost" href={url} download={job.document_filename ?? undefined}>Download original</a>
            </div>
          ) : (
            <div className="grid h-full place-items-center p-6 text-sm text-ink-muted">No document for this scan.</div>
          )}
        </section>
      </div>
      {url && <a className={link} href={url} target="_blank" rel="noreferrer">Open original in a new tab</a>}
      <ConfirmDialog
        open={confirm}
        title="Cancel this scan?"
        confirmLabel="Cancel scan"
        danger
        onClose={() => setConfirm(false)}
        onConfirm={() => {
          setConfirm(false);
          api.cancelScan(job.id, job.version).then(setJob, (e) => setError(scanMessage(e)));
        }}
      >
        The scanner will not pick this request up. You can start a new scan at any time.
      </ConfirmDialog>
    </div>
  );
}
