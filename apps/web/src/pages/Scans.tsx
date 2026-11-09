import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, canInWarehouse, has, scanDocumentUrl, type OcrExtraction, type ScanDevice, type ScanJob, type ScanJobType, type Warehouse } from "../api";
import { useAuth } from "../auth";
import { StageSteps, UploadQueue } from "./ScanProgress";
import { ACCEPT, AUTO_READ, stageOf, bridgeScan, checkFile, failHint, fileSize, isOpen, JOB_STATUS, JOB_TYPE, SCANNER_STATE, scanMessage } from "../lib/scans";
import { DataGrid, type Column } from "../ui/DataGrid";
import { OcrReviewPage, ReviewQueue, ScanReading } from "./OcrReview";
import { OCR_STATUS } from "../lib/ocr";
import { Icon, type IconName } from "../ui/Icon";
import { ConfirmDialog, EmptyState, ErrorState, FilterBar, LoadingRows, PageHeader, StatusChip } from "../ui/primitives";

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

type Tab = "scans" | "review" | "scanners";
const TAB_LABEL: Record<Tab, string> = { scans: "New scan", review: "To review", scanners: "Scanners" };

export function ScansPage() {
  const { session } = useAuth();
  const [params, setParams] = useSearchParams();
  const canManage = !!session && has(session, "scan.device.manage");
  const canReview = !!session && has(session, "scan.review");
  const tabs: Tab[] = ["scans", ...(canReview ? (["review"] as const) : []), ...(canManage ? (["scanners"] as const) : [])];
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
      {tab === "scans" ? <ScansTab warehouses={warehouses} /> : tab === "review" ? <ReviewQueue warehouses={warehouses} /> : <ScannersTab warehouses={warehouses} />}
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
  ];
  return (
    <div className="space-y-5">
      {createWh.length > 0 && <ScanStudio warehouses={createWh} onChanged={() => void load()} />}
      <FilterBar search={q} onSearch={setQ} options={VIEWS} value={view} onChange={setView} placeholder="Scan no. or file name" />
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
            <div className="flex justify-center">
              <button className="btn-ghost" onClick={() => void load(next)}>Load more</button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ---------------------------- Scan studio ---------------------------- */

type Phase =
  | { kind: "idle" }
  | { kind: "working"; mode: "scan" | "upload"; step: number; label: string }
  | { kind: "done"; job: ScanJob; duplicate: boolean };

const STEPS: Record<"scan" | "upload", { title: string; hint: string }[]> = {
  scan: [
    { title: "Connected to scanner", hint: "Asking the scanner PC to start" },
    { title: "Scanning pages", hint: "Pulling pages from the feeder" },
    { title: "Saving the scan", hint: "Storing the original file" },
    { title: "Reading lines", hint: "Sales orders are read automatically" },
  ],
  upload: [
    { title: "Uploading the file", hint: "Sending it to Pro Trading" },
    { title: "Saving the scan", hint: "Storing the original file" },
    { title: "Reading lines", hint: "Sales orders are read automatically" },
  ],
};

/** Simple scanner illustration (inline SVG, brand colours). */
/** Office multifunction scanner/printer: document feeder on top, glass, touch panel, output
tray and paper drawers. While a scan runs, the light bar sweeps the glass. */
function ScannerArt({ working = false }: { working?: boolean }) {
  return (
    <div className="relative grid h-full min-h-[15rem] place-items-center overflow-hidden rounded-ctl bg-brand-accent/10" aria-hidden>
      <svg viewBox="0 0 240 220" className="w-[80%] max-w-[18rem]">
        <ellipse cx="120" cy="208" rx="90" ry="8" fill="currentColor" className="text-brand-accent/15" />
        {/* paper drawers */}
        <rect x="54" y="140" width="132" height="62" rx="8" strokeWidth="2" className="fill-card stroke-line" />
        <line x1="54" y1="171" x2="186" y2="171" strokeWidth="2" className="stroke-line" />
        <rect x="102" y="151" width="36" height="6" rx="3" fill="currentColor" className="text-ink-muted/40" />
        <rect x="102" y="182" width="36" height="6" rx="3" fill="currentColor" className="text-ink-muted/40" />
        {/* printer body with output slot and a printed page */}
        <rect x="46" y="80" width="148" height="62" rx="10" strokeWidth="2" className="fill-card stroke-line" />
        <rect x="64" y="100" width="96" height="8" rx="4" fill="currentColor" className="text-ink/70" />
        <path d="M70 104 H154 L149 128 H75 Z" strokeWidth="1.5" className="fill-card stroke-line" />
        <path d="M84 114 H140 M84 120 H126" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-ink-muted/40" />
        <circle cx="60" cy="128" r="3.5" fill="currentColor" className={`text-brand-accent ${working ? "animate-pulse" : ""}`} />
        {/* scanner glass and lid */}
        <rect x="42" y="64" width="156" height="18" rx="6" strokeWidth="2" className="fill-card stroke-line" />
        {working && <rect x="48" y="70" width="144" height="5" rx="2.5" fill="currentColor" className="animate-pulse text-brand-accent" />}
        {/* document feeder with pages waiting */}
        <path d="M58 64 L70 42 H168 L176 64 Z" strokeWidth="2" className="fill-card stroke-line" />
        <g className={working ? "animate-pulse" : ""}>
          <path d="M84 42 L94 16 H160 L154 42 Z" strokeWidth="1.5" fill="currentColor" className="stroke-line text-subtle" />
          <path d="M102 26 H148 M100 33 H138" stroke="currentColor" strokeWidth="2" strokeLinecap="round" className="text-ink-muted/40" />
        </g>
        {/* angled touch panel */}
        <path d="M170 68 L210 58 L214 78 L174 88 Z" strokeWidth="2" className="fill-card stroke-line" />
        <path d="M177 72 L205 65 L207.5 75 L179.5 82 Z" fill="currentColor" className="text-brand-accent/60" />
      </svg>
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
  const [devices, setDevices] = useState<ScanDevice[]>([]);
  const [device, setDevice] = useState("");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [msg, setMsg] = useState<{ tone: "error" | "ok"; text: string; jobId?: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const working = phase.kind === "working";
  const [upPct, setUpPct] = useState<number | null>(null);
  const [queue, setQueue] = useState<File[] | null>(null);

  useEffect(() => {
    if (!wh) return;
    api.scanDevices({ warehouse_id: wh }).then(
      (d) => {
        const active = d.filter((x) => x.status === "ACTIVE");
        setDevices(active);
        setDevice((cur) => (active.some((x) => x.id === cur) ? cur : active[0]?.id ?? ""));
      },
      () => setDevices([]),
    );
  }, [wh]);
  const dev = devices.find((d) => d.id === device);

  async function scan() {
    if (!device) return;
    setPhase({ kind: "working", mode: "scan", step: 0, label: dev?.name ?? "Scanner" });
    setMsg(null);
    try {
      const job = await api.requestScan({ warehouse_id: wh, job_type: type, device_id: device, note: note.trim() || undefined });
      onChanged();
      setPhase((p) => (p.kind === "working" ? { ...p, step: 1 } : p));
      try {
        const done = await bridgeScan(job);
        if (done.status === "UPLOADED") {
          setMsg({ tone: "ok", text: `${done.job_no} received.`, jobId: done.id });
          setPhase({ kind: "done", job: done, duplicate: false });
        } else {
          setMsg({ tone: "error", text: `${job.job_no}: ${failHint(done.error_code)}`, jobId: job.id });
          setPhase({ kind: "idle" });
        }
      } catch (e) {
        // The job stays REQUESTED and expires on its own; cancel it so nobody waits on it.
        await api.cancelScan(job.id, job.version).catch(() => undefined);
        setMsg({ tone: "error", text: scanMessage(e) });
        setPhase({ kind: "idle" });
      }
      setNote("");
    } catch (e) {
      setMsg({ tone: "error", text: scanMessage(e) });
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
    setPhase({ kind: "working", mode: "upload", step: 0, label: file.name });
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

  return (
    <section className="card grid gap-6 p-6 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.6fr)]">
      <ScannerArt working={working} />
      <div className="flex min-w-0 flex-col gap-6 lg:border-l lg:border-line lg:pl-8">
        <div className="flex items-start gap-4">
          <span className="grid size-16 shrink-0 place-items-center rounded-ctl bg-brand-accent/12 text-brand-primary"><Icon name="printer" size={30} /></span>
          <div className="min-w-0">
            <h2 id="new-scan" className="text-[26px] font-semibold leading-tight tracking-tight">
              {phase.kind === "working" ? (phase.mode === "scan" ? "Scanning in progress" : "Uploading the file") : "Start a scan from a network scanner"}
            </h2>
            <p className="mt-1 text-base text-ink-muted">
              {phase.kind === "working"
                ? phase.mode === "scan"
                  ? `Scanning with ${phase.label}. Keep the pages in the feeder.`
                  : `Sending ${phase.label}.`
                : "Scan documents straight from the office scanner, or upload a file."}
            </p>
          </div>
        </div>

        {phase.kind === "working" ? (
          <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,0.9fr)]">
            <div className="space-y-5">
              <div>
                <div className="flex items-baseline justify-between">
                  <span className="num text-[44px] font-semibold leading-none tracking-tight text-brand-primary">
                    {phase.mode === "upload" && upPct !== null ? Math.round(upPct * 100) : Math.round(((phase.step + 0.5) / STEPS[phase.mode].length) * 100)}%
                  </span>
                  <span className="rounded-full bg-subtle px-3 py-1 text-sm font-medium">Step {phase.step + 1} of {STEPS[phase.mode].length}</span>
                </div>
                <div className="mt-3 h-3 overflow-hidden rounded-full bg-subtle" role="progressbar" aria-label="Scan progress" aria-valuemin={0} aria-valuemax={STEPS[phase.mode].length} aria-valuenow={phase.step + 1} aria-valuetext={STEPS[phase.mode][phase.step]?.title}>
                  <div className="h-full rounded-full bg-brand-accent transition-[width] duration-300" style={{ width: `${phase.mode === "upload" && upPct !== null ? Math.max(3, upPct * 100) : ((phase.step + 0.5) / STEPS[phase.mode].length) * 100}%` }} />
                </div>
              </div>
              <p className="text-sm text-ink-muted">You can leave this page — the scan carries on and shows up in the list below.</p>
            </div>
            <ol className="space-y-1">
              {STEPS[phase.mode].map((s, i) => {
                const st = i < phase.step ? "done" : i === phase.step ? "now" : "next";
                return (
                  <li key={s.title} className={`flex items-start gap-3 rounded-ctl px-3 py-2.5 ${st === "now" ? "bg-brand-accent/10" : ""}`}>
                    <span className={`mt-0.5 grid size-7 shrink-0 place-items-center rounded-full ${st === "done" ? "bg-brand-accent text-white" : st === "now" ? "border-2 border-brand-accent" : "border-2 border-line"}`}>
                      {st === "done" ? <Icon name="check" size={16} /> : st === "now" ? <span className="size-3 rounded-full bg-brand-accent" /> : null}
                    </span>
                    <span className="min-w-0">
                      <span className={`block text-[15px] font-semibold ${st === "next" ? "text-ink-muted" : ""}`}>{s.title}</span>
                      <span className="block text-sm text-ink-muted">{s.hint}</span>
                    </span>
                  </li>
                );
              })}
            </ol>
          </div>
        ) : (
          <>
            <div className="grid gap-5 sm:grid-cols-2">
              <label>
                <span className="label">Document type</span>
                <select className="input py-3" value={type} onChange={(e) => setType(e.target.value as ScanJobType)}>
                  {(Object.keys(JOB_TYPE) as ScanJobType[]).map((t) => <option key={t} value={t}>{JOB_TYPE[t]}</option>)}
                </select>
              </label>
              <label>
                <span className="label">Scanner / device</span>
                <select className="input py-3" value={device} onChange={(e) => setDevice(e.target.value)} disabled={devices.length === 0}>
                  {devices.length === 0 && <option value="">No approved scanner here</option>}
                  {devices.map((d) => (
                    <option key={d.id} value={d.id}>{d.name}{d.online ? "" : " (offline)"}</option>
                  ))}
                </select>
                {dev && (
                  <span className="mt-1.5 flex items-center gap-1.5 text-sm text-ink-muted">
                    <span aria-hidden className={`size-2.5 rounded-full ${dev.online ? "bg-brand-accent" : "bg-brand-gray"}`} />
                    {dev.online ? "Online" : "Offline"}
                  </span>
                )}
              </label>
              <label className="sm:col-span-2">
                <span className="label">Note <span className="font-normal text-ink-muted">(optional)</span></span>
                <input className="input py-3" value={note} maxLength={500} placeholder="e.g. Nov 2 sales orders" onChange={(e) => setNote(e.target.value)} />
              </label>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <button className="btn-accent px-6 py-3 text-base" disabled={!device} onClick={() => void scan()}>
                <Icon name="scan" />Scan now
              </button>
              <span className="text-sm text-ink-muted">or</span>
              <FileButton label="Upload files" disabled={false} inputRef={fileRef} onFile={(f) => void upload(f)} onFiles={(fs) => setQueue(fs)} />
              <span className="text-sm text-ink-muted">PDF (any number of pages), PNG, JPEG or TIFF, up to 20 MB each. Pick several at once.</span>
            </div>
          </>
        )}
        {status}
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

/** "Scan completed" card; for sales orders the recognized lines open underneath by themselves. */
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
  const below = useRef<HTMLDivElement>(null);
  const url = job.document_id ? scanDocumentUrl(job.document_id) : null;
  const reads = AUTO_READ.includes(job.job_type);

  const load = useCallback(() => {
    if (!canRead || !reads) return;
    api.ocrExtractions({ scan_job_id: job.id, status: "QUEUED,NEEDS_REVIEW,FAILED,ACCEPTED,REJECTED" }).then(
      (r) => {
        setEx(r.items[0] ?? null);
        setChecked(true);
      },
      () => setChecked(true),
    );
  }, [canRead, reads, job.id]);
  useEffect(() => {
    setEx(null);
    setChecked(false);
    load();
  }, [load]);
  useEffect(() => {
    if (ex && ex.status !== "QUEUED") return;
    if (!checked && ex === null) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [ex, checked, load]);
  const review = ex?.status === "NEEDS_REVIEW";
  useEffect(() => {
    if (review) below.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [review]);

  const details: [IconName, string, React.ReactNode][] = [
    ["file", "Document type", JOB_TYPE[job.job_type]],
    ["printer", "Source", job.channel === "BRIDGE" ? job.device_name ?? "Scanner" : "File upload"],
    ["layers", "Pages", ex?.page_count ?? "—"],
    ["documents", "File name", job.document_filename ?? "—"],
    ["user", "Scanned by", job.requested_by_name],
    ["calendar", "Scan date & time", when(job.completed_at ?? job.created_at)],
  ];

  return (
    <>
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
              {reads && canRead && (
                <div className="mb-4">
                  <StageSteps stage={stageOf(true, ex?.status ?? (checked ? "QUEUED" : null))} error={ex?.error_detail ?? null} />
                </div>
              )}
              {!reads ? (
                <p className="rounded-ctl bg-subtle p-4 text-sm text-ink-muted">Picking sheets and “Other” documents are kept but not read automatically.</p>
              ) : !canRead ? (
                <p className="rounded-ctl bg-subtle p-4 text-sm text-ink-muted">Someone who reviews scans will check the lines read from this scan.</p>
              ) : !ex || ex.status === "QUEUED" ? (
                <div className="flex items-center gap-3 rounded-ctl bg-brand-accent/10 p-4 text-sm">
                  <span aria-hidden className="size-5 animate-spin rounded-full border-2 border-brand-accent border-t-transparent" />
                  <span><strong>Reading the lines…</strong> The recognized lines open below as soon as they're ready.</span>
                </div>
              ) : ex.status === "FAILED" ? (
                <div className="rounded-ctl bg-danger/8 p-4 text-sm text-danger">
                  <strong>This scan couldn't be read{ex.error_code ? ` (${ex.error_code})` : ""}.</strong> Open the scan to read it again, or enter the order by hand.
                </div>
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

/* ---------------------------- Scanners tab ---------------------------- */

type Dialog = { kind: "approve" | "revoke"; d: ScanDevice } | null;

function ScannersTab({ warehouses }: { warehouses: Warehouse[] }) {
  const [rows, setRows] = useState<ScanDevice[] | null>(null);
  const [error, setError] = useState(false);
  const [showRevoked, setShowRevoked] = useState(false);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [input, setInput] = useState("");
  const [msg, setMsg] = useState<{ tone: "error" | "ok"; text: string } | null>(null);
  const [pairing, setPairing] = useState<{ code: string; expires_at: string; name: string } | null>(null);
  const [finding, setFinding] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await api.scanDevices({ include_revoked: showRevoked }));
      setError(false);
    } catch {
      setError(true);
    }
  }, [showRevoked]);
  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 15000);
    return () => clearInterval(t);
  }, [load]);

  async function act() {
    if (!dialog) return;
    const { kind, d } = dialog;
    try {
      if (kind === "approve") await api.approveScanDevice(d.id, d.version, input.trim());
      else await api.revokeScanDevice(d.id, d.version, input.trim());
      setMsg({ tone: "ok", text: kind === "approve" ? `${d.name} approved.` : `${d.name} revoked. It can no longer scan.` });
      if (kind === "approve" && pairing) setPairing(null);
      setDialog(null);
    } catch (e) {
      setMsg({ tone: "error", text: scanMessage(e) });
      setDialog(null);
    }
    void load();
  }

  /** Ask the bridge which scanners its PC sees; it answers on its next heartbeat (≤ ~30 s). */
  async function find(d: ScanDevice) {
    setMsg(null);
    setFinding(d.id);
    try {
      const asked = await api.discoverScanners(d.id);
      const since = asked.discover_requested_at ?? "";
      for (let i = 0; i < 25; i++) {
        await new Promise((r) => setTimeout(r, 2500));
        const now = (await api.scanDevices({ include_revoked: showRevoked })).find((x) => x.id === d.id);
        if (now?.detected_at && now.detected_at >= since) {
          setRows((rs) => rs?.map((x) => (x.id === d.id ? now : x)) ?? rs);
          const n = now.detected_scanners?.length ?? 0;
          setMsg({ tone: n ? "ok" : "error", text: n ? `${d.name} sees ${n} scanner${n === 1 ? "" : "s"}.` : `${d.name} sees no scanner. Check the cable or Wi-Fi and that the scanner is switched on.` });
          return;
        }
      }
      setMsg({ tone: "error", text: `${d.name} didn't answer. Check the office PC is on and the bridge is running.` });
    } catch {
      setMsg({ tone: "error", text: "Couldn't ask the scanner PC. Try again." });
    } finally {
      setFinding(null);
    }
  }

  const columns: Column<ScanDevice>[] = [
    { key: "n", header: "Scanner", cell: (r) => <span className="font-medium">{r.name}</span>, sort: (r) => r.name },
    {
      key: "s",
      header: "Status",
      cell: (r) =>
        r.status === "ACTIVE" ? (
          <StatusChip tone={r.online ? "success" : "neutral"}>{r.online ? "Online" : "Offline"}</StatusChip>
        ) : r.status === "PENDING" ? (
          <StatusChip tone="warning">{r.paired ? "Waiting for approval" : "Waiting for bridge"}</StatusChip>
        ) : (
          <StatusChip tone="neutral">Revoked</StatusChip>
        ),
    },
    {
      key: "sc",
      header: "Scanner state",
      cell: (r) => (r.status === "ACTIVE" ? <span title={r.scanner_detail ?? undefined}><StatusChip tone={SCANNER_STATE[r.scanner_state].tone}>{SCANNER_STATE[r.scanner_state].label}</StatusChip></span> : "—"),
    },
    { key: "l", header: "Last seen", cell: (r) => when(r.last_seen_at), sort: (r) => r.last_seen_at ?? "" },
    { key: "v", header: "Bridge", cell: (r) => r.bridge_version ?? "—" },
    {
      key: "a",
      header: "Actions",
      cell: (r) => (
        <div className="flex gap-2">
          {r.status === "PENDING" && r.paired && (
            <button className="btn-primary py-1" onClick={() => { setInput(""); setDialog({ kind: "approve", d: r }); }}>Approve</button>
          )}
          {r.status === "ACTIVE" && (
            <button className="btn-ghost py-1" disabled={finding !== null} aria-label={`Find scanners on ${r.name}`} onClick={() => void find(r)}>
              {finding === r.id ? "Looking…" : "Find scanners"}
            </button>
          )}
          {r.status !== "REVOKED" && (
            <button className="btn-ghost py-1" onClick={() => { setInput(""); setDialog({ kind: "revoke", d: r }); }}>Revoke</button>
          )}
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-5">
      <AddScanner warehouses={warehouses} onPaired={(p) => { setPairing(p); void load(); }} />
      {pairing && (
        <section aria-labelledby="pair-code" className="card space-y-2 border-brand-primary p-6">
          <h2 id="pair-code" className="text-base font-semibold">Pair “{pairing.name}”</h2>
          <p className="text-sm">On the scanner PC, run:</p>
          <pre className="overflow-x-auto rounded bg-canvas p-3 text-sm" data-testid="pair-command">python -m pro_trading_bridge pair {pairing.code}</pre>
          <p className="text-sm text-ink-muted">
            The code works once and expires {when(pairing.expires_at)}. The bridge then prints a confirmation code — click <b>Approve</b> below and type it in.
          </p>
        </section>
      )}
      {msg && <Notice tone={msg.tone}>{msg.text}</Notice>}
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={showRevoked} onChange={(e) => setShowRevoked(e.target.checked)} /> Show revoked scanners
      </label>
      {error ? (
        <ErrorState title="Couldn't load scanners" onRetry={() => void load()} />
      ) : rows === null ? (
        <LoadingRows cols={7} />
      ) : (
        <DataGrid rows={rows} columns={columns} rowKey={(r) => r.id} caption={`Scanners (${rows.length})`} empty={<EmptyState title="No scanners yet" hint="Add the office scanner PC above to scan straight into Pro Trading." />} />
      )}
      {rows?.some((r) => r.detected_scanners) && (
        <section className="card space-y-3 p-6" aria-labelledby="found-h">
          <h2 id="found-h" className="text-base font-semibold">Scanners found on the office PC</h2>
          {rows
            .filter((r) => r.detected_scanners)
            .map((r) => (
              <div key={r.id} className="text-sm">
                <p className="font-medium">
                  {r.name} <span className="font-normal text-ink-muted">· checked {when(r.detected_at)}</span>
                </p>
                {r.detected_scanners?.length ? (
                  <ul className="mt-1 list-disc pl-5" data-testid={`found-${r.id}`}>
                    {r.detected_scanners.map((s) => (
                      <li key={`${s.driver}-${s.name}`}>
                        {s.name} <span className="text-ink-muted">({s.driver.toUpperCase()})</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-ink-muted">No scanner found. Check the cable or Wi-Fi and that the scanner is switched on.</p>
                )}
              </div>
            ))}
        </section>
      )}
      <ConfirmDialog
        open={dialog !== null}
        title={dialog?.kind === "approve" ? `Approve ${dialog.d.name}` : `Revoke ${dialog?.d.name ?? ""}`}
        confirmLabel={dialog?.kind === "approve" ? "Approve" : "Revoke"}
        danger={dialog?.kind === "revoke"}
        onClose={() => setDialog(null)}
        onConfirm={() => {
          if (dialog?.kind === "approve" ? input.trim().length >= 4 : input.trim().length >= 3) void act();
        }}
      >
        <label className="block">
          <span className="label">{dialog?.kind === "approve" ? "Confirmation code shown on the scanner PC" : "Reason"}</span>
          <input className="input" value={input} onChange={(e) => setInput(e.target.value)} autoComplete="off" maxLength={dialog?.kind === "approve" ? 32 : 500} />
        </label>
        {dialog?.kind === "revoke" && <p className="mt-2">The bridge will stop working immediately. To use it again, pair it as a new scanner.</p>}
      </ConfirmDialog>
    </div>
  );
}

function AddScanner({ warehouses, onPaired }: { warehouses: Warehouse[]; onPaired: (p: { code: string; expires_at: string; name: string }) => void }) {
  const wh = warehouses[0]?.id ?? "";
  const [name, setName] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await api.pairScanDevice({ warehouse_id: wh, name: name.trim() });
      onPaired({ code: r.pairing_code, expires_at: r.expires_at, name: name.trim() });
      setName("");
    } catch (e) {
      setErr(scanMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={(e) => void submit(e)} aria-labelledby="add-scanner" className="card space-y-3 p-6">
      <h2 id="add-scanner" className="text-base font-semibold">Add a scanner PC</h2>
      <div className="flex flex-wrap items-end gap-3">
        <label className="w-64">
          <span className="label">Name</span>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Front office scanner" minLength={2} maxLength={80} required />
        </label>
        <button className="btn-primary" disabled={busy || !wh || name.trim().length < 2}>Get pairing code</button>
      </div>
      {err && <Notice tone="error">{err}</Notice>}
    </form>
  );
}

/* ================================ Detail ================================ */

export function ScanJobPage() {
  const { id = "" } = useParams();
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
  if (!job) return <LoadingRows rows={3} cols={2} />;
  const url = job.document_id ? scanDocumentUrl(job.document_id) : null;
  const inline = job.document_mime === "application/pdf" || job.document_mime === "image/png" || job.document_mime === "image/jpeg";
  return (
    <div className="space-y-5">
      <PageHeader
        title={`Scan ${job.job_no}`}
        subtitle={<><Link className={link} to="/scan">Scan Center</Link> · {JOB_TYPE[job.job_type]}</>}
        actions={job.status === "REQUESTED" && <button className="btn-ghost" onClick={() => setConfirm(true)}>Cancel scan</button>}
      />
      {error && <Notice tone="error">{error}</Notice>}
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
