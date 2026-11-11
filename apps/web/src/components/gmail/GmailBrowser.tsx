import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, canInWarehouse, type Warehouse } from "../../api";
import { useAuth } from "../../auth";
import {
  bodyDocument,
  CATEGORIES,
  gmail,
  gmailMessage,
  NO_FILTERS,
  pickable,
  previousOf,
  RANGES,
  senderOf,
  SHOWS,
  STATE,
  themeColors,
  whenFull,
  whenShort,
  type Filters,
  type GmailAttachment,
  type GmailCategory,
  type GmailMessage,
  type GmailMessageDetail,
  type GmailStatus,
  type HistoryEntry,
  type ProcessOut,
} from "../../lib/gmail";
import { fileSize } from "../../lib/scans";
import { useDebounced } from "../../lib/useDebounced";
import { useGmailLive } from "../../lib/useGmailLive";
import { Icon } from "../../ui/Icon";
import { EmptyState, ErrorState, LoadingRows, StatusChip } from "../../ui/primitives";
import { Modal, PagePreview } from "./PagePreview";

const link = "font-medium text-brand-primary-hover underline-offset-2 hover:underline";
const chip = (on: boolean) =>
  `chip cursor-pointer px-3.5 py-1.5 text-[13px] transition-colors duration-150 ${on ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:border-brand-gray/60 hover:bg-subtle"}`;

type Props = {
  status: GmailStatus;
  /** `picker`: choose one document (Create invoice › Pick from Gmail). `page`: the full inbox. */
  mode: "page" | "picker";
  defaultCategory?: GmailCategory;
  initialMessage?: string | null;
  initialShow?: Filters["show"];
  /** Bumped by the page's Refresh button. */
  reloadKey?: number;
  onProcessed: (out: ProcessOut) => void;
};

/** The stored inbox: filters, search, the email list, one email with its files, and the
 * "Use for OCR" step. The same component is the Gmail Inbox page and the document picker. */
export function GmailBrowser({ status, mode, defaultCategory = "PICK_SHEET", initialMessage = null, initialShow = "all", reloadKey = 0, onProcessed }: Props) {
  const picker = mode === "picker";
  const [filters, setFilters] = useState<Filters>({ ...NO_FILTERS, show: initialShow });
  const q = useDebounced(filters.q, 300);
  const [rows, setRows] = useState<GmailMessage[] | null>(null);
  const [total, setTotal] = useState(0);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(initialMessage);
  const [detail, setDetail] = useState<GmailMessageDetail | null>(null);
  const [detailErr, setDetailErr] = useState<string | null>(null);
  const [picked, setPicked] = useState<GmailAttachment | null>(null);
  const [preview, setPreview] = useState<GmailAttachment | null>(null);
  const [processing, setProcessing] = useState<GmailAttachment | null>(null);
  const shown = useRef(0);

  const applied = { ...filters, q };
  const sig = JSON.stringify(applied);
  const load = useCallback(
    async (more?: string | null) => {
      const f = JSON.parse(sig) as Filters;
      if (f.range === "custom" && !f.from && !f.to) return; // wait for a date
      try {
        // A quiet refresh re-reads as many rows as are on screen, so "Load more" is kept.
        const page = await gmail.messages(f, more, more ? 30 : Math.min(100, Math.max(30, shown.current)));
        setRows((prev) => (more && prev ? [...prev, ...page.items] : page.items));
        setTotal(page.total);
        setCursor(page.next_cursor);
        setError(null);
      } catch (err) {
        setError(gmailMessage(err));
      }
    },
    [sig],
  );
  shown.current = rows?.length ?? 0;
  useEffect(() => {
    shown.current = 0;
    setRows(null);
    void load();
  }, [load, reloadKey]);
  useGmailLive(() => void load());

  const openMessage = useCallback((id: string) => {
    gmail.message(id).then(
      (d) => {
        setDetail(d);
        setDetailErr(null);
      },
      (err: unknown) => setDetailErr(gmailMessage(err)),
    );
  }, []);
  useEffect(() => {
    setDetail(null);
    setDetailErr(null);
    setPicked(null);
    if (!openId) return;
    openMessage(openId);
    // Opening an email marks it read here (Gmail itself is never changed).
    gmail.markRead(openId, true).then(
      (m) => setRows((prev) => prev?.map((r) => (r.id === m.id ? { ...r, read: true } : r)) ?? prev),
      () => undefined,
    );
  }, [openId, openMessage]);
  // Keep the open email's processing states fresh while a reading runs.
  useGmailLive(() => openId && openMessage(openId), 8_000);

  const set = (patch: Partial<Filters>) => setFilters((f) => ({ ...f, ...patch }));
  const use = (a: GmailAttachment) => setProcessing(a);

  return (
    <div className="space-y-4">
      <div className="space-y-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex min-w-0 flex-wrap gap-1.5" role="group" aria-label="Received">
            {RANGES.map((r) => (
              <button key={r.value} type="button" aria-pressed={filters.range === r.value} className={chip(filters.range === r.value)} onClick={() => set({ range: r.value })}>
                {r.label}
              </button>
            ))}
          </div>
          <label className="relative min-w-0 flex-1 sm:ml-auto sm:max-w-80">
            <span className="sr-only">Search sender, subject or file name</span>
            <Icon name="search" size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-ink-muted" />
            <input data-page-search type="search" className="input w-full rounded-full py-2 pl-10" placeholder="Search sender, subject or file name" value={filters.q} onChange={(e) => set({ q: e.target.value })} />
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex min-w-0 flex-wrap gap-1.5" role="group" aria-label="Documents">
            {SHOWS.map((s) => (
              <button key={s.value} type="button" aria-pressed={filters.show === s.value} className={chip(filters.show === s.value)} onClick={() => set({ show: filters.show === s.value ? "all" : s.value })}>
                {s.label}
              </button>
            ))}
          </div>
          {filters.range === "custom" && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <label className="flex items-center gap-2">
                <span className="text-ink-muted">From</span>
                <input type="date" className="input w-auto py-1.5" value={filters.from} max={filters.to || undefined} onChange={(e) => set({ from: e.target.value })} />
              </label>
              <label className="flex items-center gap-2">
                <span className="text-ink-muted">to</span>
                <input type="date" className="input w-auto py-1.5" value={filters.to} min={filters.from || undefined} onChange={(e) => set({ to: e.target.value })} />
              </label>
            </div>
          )}
        </div>
      </div>

      <section className={`card grid overflow-clip lg:grid-cols-[minmax(0,25rem)_minmax(0,1fr)] ${picker ? "lg:h-[min(40rem,calc(100dvh-21rem))]" : "lg:h-[calc(100dvh-20.5rem)] lg:min-h-[30rem]"}`} aria-label="Emails">
        <div className={`min-h-0 flex-col border-line lg:flex lg:border-r ${openId ? "hidden" : "flex"}`}>
          <p className="border-b border-line px-4 py-2.5 text-xs text-ink-muted" aria-live="polite">
            {rows === null ? "Loading…" : `${total} ${total === 1 ? "email" : "emails"}, newest first`}
          </p>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {error ? (
              <ErrorState title="Couldn't load the inbox" detail={error} onRetry={() => void load()} />
            ) : rows === null ? (
              <LoadingRows rows={7} cols={2} />
            ) : rows.length === 0 ? (
              <EmptyState
                icon="mail"
                title={status.message_count === 0 ? "No emails yet" : "No emails match"}
                hint={status.message_count === 0 ? (status.account ? "New mail appears here by itself once the mailbox has been checked." : "Emails appear here after a mailbox is connected.") : "Try another date range or search."}
              />
            ) : (
              <ul aria-label="Email list">
                {rows.map((m) => (
                  <MessageRow key={m.id} m={m} active={m.id === openId} onOpen={() => setOpenId(m.id)} />
                ))}
              </ul>
            )}
            {cursor && rows && (
              <div className="flex justify-center border-t border-line p-3">
                <button type="button" className="btn-ghost px-4 py-2 text-xs" onClick={() => void load(cursor)}>
                  Load older emails
                </button>
              </div>
            )}
          </div>
        </div>

        <div className={`min-h-0 min-w-0 flex-col lg:flex ${openId ? "flex" : "hidden"}`}>
          {!openId ? (
            <div className="grid flex-1 place-items-center">
              <EmptyState icon="mail" title={picker ? "Choose an email" : "Select an email"} hint={picker ? "Then choose the document to read." : "Its message and files show here."} />
            </div>
          ) : detailErr ? (
            <ErrorState title="Couldn't open this email" detail={detailErr} onRetry={() => openMessage(openId)} />
          ) : !detail ? (
            <LoadingRows rows={5} cols={1} />
          ) : (
            <MessageDetail
              m={detail}
              status={status}
              picker={picker}
              picked={picked}
              onPick={setPicked}
              onBack={() => setOpenId(null)}
              onPreview={setPreview}
              onUse={use}
            />
          )}
        </div>
      </section>

      {picker && (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="min-w-0 text-sm text-ink-muted" aria-live="polite">
            {picked ? `Selected: ${picked.filename}${picked.page_count ? ` · ${picked.page_count} ${picked.page_count === 1 ? "page" : "pages"}` : ""}` : "No document selected."}
          </p>
          <button type="button" className="btn-primary" disabled={!picked || !status.can_process} onClick={() => picked && use(picked)}>
            Select document
          </button>
        </div>
      )}

      <Modal open={!!preview} title={preview?.filename ?? ""} onClose={() => setPreview(null)} wide>
        {preview && <PagePreview attachmentId={preview.id} title={preview.filename} className="h-full rounded-none border-0" fullScreen={false} />}
      </Modal>
      {processing && (
        <ProcessDialog
          attachment={processing}
          status={status}
          defaultCategory={defaultCategory}
          onClose={() => setProcessing(null)}
          onDone={(out) => {
            setProcessing(null);
            onProcessed(out);
          }}
        />
      )}
    </div>
  );
}

function MessageRow({ m, active, onOpen }: { m: GmailMessage; active: boolean; onOpen: () => void }) {
  const st = STATE[m.state];
  return (
    <li className="border-b border-line last:border-b-0">
      <button
        type="button"
        onClick={onOpen}
        aria-current={active ? "true" : undefined}
        className={`grid w-full cursor-pointer grid-cols-[0.5rem_minmax(0,1fr)_auto] items-start gap-x-2.5 px-4 py-3 text-left transition-colors duration-150 hover:bg-subtle focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand-primary ${active ? "bg-brand-accent/10" : ""}`}
      >
        <span aria-hidden className={`mt-1.5 size-2 rounded-full ${m.read ? "bg-transparent" : "bg-brand-primary"}`} />
        <span className="min-w-0">
          <span className={`block truncate text-sm ${m.read ? "text-ink" : "font-semibold text-ink"}`}>
            {senderOf(m)}
            {!m.read && <span className="sr-only"> (unread)</span>}
          </span>
          <span className={`block truncate text-sm ${m.read ? "text-ink-muted" : "text-ink"}`}>{m.subject || "(no subject)"}</span>
          <span className="block truncate text-xs text-ink-muted">{m.snippet}</span>
          {(m.attachment_count > 0 || m.state !== "NOT_PROCESSED") && (
            <span className="mt-1.5 flex flex-wrap items-center gap-1.5">
              {m.attachment_count > 0 && (
                <span className="chip border-line bg-card px-2 py-0.5 text-[11px] text-ink-muted" title={m.attachments.map((a) => a.filename).join(", ")}>
                  <Icon name="file" size={12} />
                  <span className="num">{m.attachment_count}</span>
                  <span className="sr-only">{m.attachment_count === 1 ? "attachment" : "attachments"}</span>
                  <span className="max-w-36 truncate">{pickable(m)[0]?.filename}</span>
                </span>
              )}
              {m.state !== "NOT_PROCESSED" && (
                <span className="text-[11px]">
                  <StatusChip tone={st.tone}>{st.label}</StatusChip>
                </span>
              )}
            </span>
          )}
        </span>
        <time className="num whitespace-nowrap pt-0.5 text-xs text-ink-muted" dateTime={m.received_at} title={whenFull(m.received_at)}>
          {whenShort(m.received_at)}
        </time>
      </button>
    </li>
  );
}

function MessageDetail({
  m,
  status,
  picker,
  picked,
  onPick,
  onBack,
  onPreview,
  onUse,
}: {
  m: GmailMessageDetail;
  status: GmailStatus;
  picker: boolean;
  picked: GmailAttachment | null;
  onPick: (a: GmailAttachment | null) => void;
  onBack: () => void;
  onPreview: (a: GmailAttachment) => void;
  onUse: (a: GmailAttachment) => void;
}) {
  const files = pickable(m);
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="space-y-1 border-b border-line px-5 py-4">
        <button type="button" className="mb-1 inline-flex cursor-pointer items-center gap-1 text-sm text-brand-primary-hover lg:hidden" onClick={onBack}>
          <Icon name="chevronLeft" size={16} />
          All emails
        </button>
        <h2 className="text-lg font-semibold leading-snug tracking-tight">{m.subject || "(no subject)"}</h2>
        <p className="text-sm">
          <span className="font-medium">{senderOf(m)}</span>
          {m.from_name && m.from_email && <span className="text-ink-muted"> &lt;{m.from_email}&gt;</span>}
        </p>
        <p className="text-xs text-ink-muted">
          <time dateTime={m.received_at}>{whenFull(m.received_at)}</time>
          {m.to_addr && ` · to ${m.to_addr}`}
          {m.removed && " · no longer in Gmail"}
        </p>
      </div>
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-5 py-4">
        <section aria-label="Attachments" className="space-y-2">
          <h3 className="text-sm font-semibold">{files.length ? `Attachments (${files.length})` : "No attachments"}</h3>
          {files.length > 0 && (
            <ul className="space-y-2">
              {files.map((a) => (
                <AttachmentRow key={a.id} a={a} status={status} picker={picker} picked={picked?.id === a.id} onPick={() => onPick(picked?.id === a.id ? null : a)} onPreview={() => onPreview(a)} onUse={() => onUse(a)} />
              ))}
            </ul>
          )}
          {picker && picked && status.can_preview && <PagePreview attachmentId={picked.id} title={picked.filename} className="h-[26rem]" />}
        </section>
        <section aria-label="Message" className="space-y-2">
          <h3 className="text-sm font-semibold">Message</h3>
          {!m.body_visible ? (
            <p className="text-sm text-ink-muted">Your account can see who sent this email and its files, but not its text.</p>
          ) : m.body_html ? (
            // Sandboxed: scripts, forms and same-origin access are off; only links may open a new tab.
            <iframe title="Email message" sandbox="allow-popups allow-popups-to-escape-sandbox" referrerPolicy="no-referrer" srcDoc={bodyDocument(m.body_html, themeColors())} className="h-80 w-full rounded-[var(--radius-ctl)] border border-line bg-card" />
          ) : m.body_text ? (
            <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-[var(--radius-ctl)] border border-line bg-card p-3 font-sans text-sm">{m.body_text}</pre>
          ) : (
            <p className="text-sm text-ink-muted">This email has no text.</p>
          )}
          {m.body_visible && m.body_truncated && <p className="text-xs text-ink-muted">Long message: only the first part is kept here.</p>}
        </section>
      </div>
    </div>
  );
}

function AttachmentRow({ a, status, picker, picked, onPick, onPreview, onUse }: { a: GmailAttachment; status: GmailStatus; picker: boolean; picked: boolean; onPick: () => void; onPreview: () => void; onUse: () => void }) {
  const st = STATE[a.state];
  const ok = !a.unsupported_reason;
  const small = "inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-line bg-card px-3 py-1.5 text-xs font-medium hover:bg-subtle";
  return (
    <li className={`rounded-[var(--radius-ctl)] border px-3.5 py-3 ${picked ? "border-brand-primary bg-brand-accent/8" : "border-line"}`}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span aria-hidden className="grid size-9 shrink-0 place-items-center rounded-ctl bg-subtle text-ink-muted">
          <Icon name="file" size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium" title={a.filename}>
            {a.filename}
          </p>
          <p className="text-xs text-ink-muted">
            {fileSize(a.byte_size)}
            {a.page_count ? ` · ${a.page_count} ${a.page_count === 1 ? "page" : "pages"}` : ""}
            {a.inline ? " · inside the message" : ""}
          </p>
        </div>
        {a.state !== "NOT_PROCESSED" && <StatusChip tone={st.tone}>{st.label}</StatusChip>}
        <div className="flex flex-wrap items-center gap-1.5">
          {ok && status.can_preview && !picker && (
            <button type="button" className={small} onClick={onPreview} aria-label={`Preview ${a.filename}`}>
              <Icon name="eye" size={14} />
              Preview
            </button>
          )}
          {ok && status.can_download && (
            <a className={small} href={gmail.downloadUrl(a.id)} download aria-label={`Download ${a.filename}`}>
              <Icon name="download" size={14} />
              Download
            </a>
          )}
          {ok && status.can_process && !picker && (
            <button type="button" className="btn-primary px-3.5 py-1.5 text-xs" onClick={onUse} aria-label={`Use ${a.filename} for OCR`}>
              {a.state === "NOT_PROCESSED" ? "Use for OCR" : "Processing history"}
            </button>
          )}
          {ok && picker && (
            <button type="button" className={picked ? "btn-accent px-3.5 py-1.5 text-xs" : "btn-ghost px-3.5 py-1.5 text-xs"} aria-pressed={picked} onClick={onPick} aria-label={`Select ${a.filename}`}>
              {picked ? "Selected" : "Select"}
            </button>
          )}
        </div>
      </div>
      {!ok && (
        <p className="mt-2 flex items-start gap-1.5 text-xs text-ink-muted">
          <Icon name="info" size={14} className="mt-px shrink-0" />
          {a.unsupported_reason}
        </p>
      )}
    </li>
  );
}

/** "Use for OCR": where the document belongs, what it is, and what was done with it before.
 * A document that was already processed is only read again on an explicit choice. */
function ProcessDialog({ attachment, status, defaultCategory, onClose, onDone }: { attachment: GmailAttachment; status: GmailStatus; defaultCategory: GmailCategory; onClose: () => void; onDone: (out: ProcessOut) => void }) {
  const { session } = useAuth();
  const [history, setHistory] = useState<HistoryEntry[] | null>(null);
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [wh, setWh] = useState("");
  const [category, setCategory] = useState<GmailCategory>(defaultCategory);
  const [again, setAgain] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    gmail.attachment(attachment.id).then(
      (d) => setHistory(d.history),
      (err: unknown) => {
        setHistory([]);
        setError(gmailMessage(err));
      },
    );
    api.warehouses().then(
      (w) => {
        const ok = w.filter((x) => x.active && session && canInWarehouse(session, "scan.create", x.id));
        setWarehouses(ok);
        setWh((cur) => cur || (ok[0]?.id ?? ""));
      },
      () => setWarehouses([]),
    );
  }, [attachment.id, session]);

  const before = history ?? [];
  const drafted = before.some((h) => h.sales_order_id || h.shipment_id);
  const repeat = before.length > 0;
  const blocked = repeat && (!status.can_reprocess || (drafted && !again));

  async function run() {
    setBusy(true);
    setError(null);
    try {
      onDone(await gmail.process(attachment.id, { warehouse_id: wh, category, ...(repeat ? { reprocess: true, confirm_duplicate: drafted && again } : {}) }));
    } catch (err) {
      const prev = previousOf(err);
      // The same file from another email or an upload: only known once it was fetched.
      if (prev.length) setHistory(prev);
      else setError(gmailMessage(err));
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      title={repeat ? "Previously processed" : "Use for OCR"}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn-primary" disabled={busy || history === null || !wh || blocked} onClick={() => void run()}>
            {busy ? "Fetching the file…" : repeat ? "Reprocess document" : "Read with OCR"}
          </button>
        </>
      }
    >
      <div className="space-y-4 p-5 text-sm">
        <p className="font-medium">{attachment.filename}</p>
        {history === null ? (
          <LoadingRows rows={2} cols={2} />
        ) : (
          <>
            {repeat && (
              <div className="space-y-2">
                <p className="text-ink-muted">This document was already used. Open what exists, or read it again as a separate attempt.</p>
                <ul className="divide-y divide-line rounded-[var(--radius-ctl)] border border-line">
                  {before.map((h) => (
                    <li key={h.scan_job_id} className="space-y-1.5 px-3.5 py-3">
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                        <StatusChip tone={STATE[h.status].tone}>{STATE[h.status].label}</StatusChip>
                        <span>{whenFull(h.processed_at)}</span>
                        <span className="text-ink-muted">by {h.processed_by_name ?? "someone"}</span>
                      </div>
                      <p className="text-xs text-ink-muted">
                        {h.scan_job_no}
                        {h.attempt_no ? ` · attempt ${h.attempt_no}` : ""}
                        {!h.same_attachment && (h.source === "gmail" ? " · same file from another email" : " · same file, uploaded in Scan Center")}
                      </p>
                      <p className="flex flex-wrap gap-x-4 gap-y-1">
                        {h.sales_order_id && (
                          <Link className={link} to={`/orders/${h.sales_order_id}`}>
                            View draft {h.order_no}
                          </Link>
                        )}
                        {h.shipment_id && (
                          <Link className={link} to={`/receiving/${h.shipment_id}`}>
                            View receiving record
                          </Link>
                        )}
                        <Link className={link} to={h.extraction_id ? `/scan/review/${h.extraction_id}` : `/scan/${h.scan_job_id}`}>
                          View extraction
                        </Link>
                      </p>
                    </li>
                  ))}
                </ul>
                {!status.can_reprocess && <p className="text-ink-muted">Reading a document again needs an administrator.</p>}
                {status.can_reprocess && drafted && (
                  <label className="flex items-start gap-2.5">
                    <input type="checkbox" className="mt-0.5 size-4 accent-brand-primary" checked={again} onChange={(e) => setAgain(e.target.checked)} />
                    <span>A draft already exists for this document. Read it again anyway, as a separate attempt. The existing draft is not changed.</span>
                  </label>
                )}
              </div>
            )}
            {(!repeat || status.can_reprocess) && (
              <div className="grid gap-4 sm:grid-cols-2">
                <div>
                  <label className="label" htmlFor="gm-cat">
                    Document type
                  </label>
                  <select id="gm-cat" className="input" value={category} onChange={(e) => setCategory(e.target.value as GmailCategory)}>
                    {CATEGORIES.map((c) => (
                      <option key={c.value} value={c.value}>
                        {c.label}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="label" htmlFor="gm-wh">
                    Warehouse
                  </label>
                  <select id="gm-wh" className="input" value={wh} onChange={(e) => setWh(e.target.value)}>
                    {!warehouses.length && <option value="">None you can scan for</option>}
                    {warehouses.map((w) => (
                      <option key={w.id} value={w.id}>
                        {w.name}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            )}
            {!repeat && <p className="text-ink-muted">The file is fetched from Gmail and read. You then check every line; accepting the reading creates a draft sales order. Nothing is invoiced and no stock moves.</p>}
          </>
        )}
        {error && (
          <p role="alert" className="alert-danger">
            {error}
          </p>
        )}
      </div>
    </Modal>
  );
}
