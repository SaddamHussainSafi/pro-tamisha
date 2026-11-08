import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, canInWarehouse, documentContentUrl, has, type DocFileKind, type DocumentFile, type DocumentPage, type DocSyncStatus } from "../api";
import { useAuth } from "../auth";
import { DOC_KIND, documentMessage, linkedHref, linkedTo, SYNC_STATUS, syncProblem } from "../lib/documents";
import { ACCEPT, checkFile, fileSize } from "../lib/scans";
import { DataGrid, type Column } from "../ui/DataGrid";
import { EmptyState, ErrorState, FilterBar, KpiCard, LoadingRows, PageHeader, StatusChip } from "../ui/primitives";

const link = "font-medium text-brand-primary-hover underline-offset-2 hover:underline";
const when = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—";
const POLL_MS = 4000;

export const SyncChip = ({ f }: { f: Pick<DocumentFile, "sync_status"> }) => (
  <StatusChip tone={SYNC_STATUS[f.sync_status].tone}>{SYNC_STATUS[f.sync_status].label}</StatusChip>
);

type View = "all" | DocSyncStatus;
const VIEWS: { value: View; label: string }[] = [
  { value: "all", label: "All" },
  { value: "PENDING", label: "Pending sync" },
  { value: "FAILED", label: "Sync failed" },
  { value: "SYNCED", label: "In Drive" },
];
const KINDS: (DocFileKind | "")[] = ["", "INVOICE_PDF", "POD", "VENDOR_DOC", "SCAN"];

/* ================================ Documents archive ================================ */

export function DocumentsPage() {
  const { session } = useAuth();
  const [view, setView] = useState<View>("all");
  const [kind, setKind] = useState<DocFileKind | "">("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState<DocumentPage | null>(null);
  const [rows, setRows] = useState<DocumentFile[] | null>(null);
  const [error, setError] = useState(false);
  const [msg, setMsg] = useState<{ tone: "error" | "ok"; text: string } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const allowed = !!session && has(session, "document.read");
  const load = useCallback(
    async (cursor?: string) => {
      if (!allowed) return;
      try {
        const r = await api.documents({
          sync_status: view === "all" ? undefined : view,
          kind: kind || undefined,
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
    [allowed, kind, q, view],
  );
  useEffect(() => {
    const t = setTimeout(() => void load(), 200);
    return () => clearTimeout(t);
  }, [load]);
  // Copies in flight finish in the background; keep the list fresh while any are pending.
  const anyPending = rows?.some((r) => r.sync_status === "PENDING") ?? false;
  useEffect(() => {
    if (!anyPending) return;
    const t = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(t);
  }, [anyPending, load]);

  if (!session) return null;
  if (!allowed)
    return (
      <div className="space-y-5">
        <PageHeader title="Documents" />
        <EmptyState title="You don't have access to the document archive" hint="Ask a manager if you need a copy of an invoice or delivery proof." />
      </div>
    );
  const canRetry = (f: DocumentFile) => canInWarehouse(session, "document.manage", f.warehouse_id);
  const retry = async (f: DocumentFile) => {
    setBusy(f.id);
    setMsg(null);
    try {
      await api.retryDocumentSync(f.id, f.version);
      setMsg({ tone: "ok", text: `${f.filename} queued for Drive again.` });
    } catch (e) {
      setMsg({ tone: "error", text: documentMessage(e) });
    } finally {
      setBusy(null);
      void load();
    }
  };

  const columns: Column<DocumentFile>[] = [
    {
      key: "f",
      header: "File",
      cell: (r) => (
        <a className={link} href={documentContentUrl(r.id)} target="_blank" rel="noreferrer">
          {r.filename}
        </a>
      ),
      sort: (r) => r.filename,
    },
    { key: "k", header: "Type", cell: (r) => DOC_KIND[r.kind] },
    {
      key: "l",
      header: "Linked to",
      cell: (r) => {
        const href = linkedHref(r);
        return href ? <Link className={link} to={href}>{linkedTo(r)}</Link> : linkedTo(r);
      },
    },
    { key: "p", header: "Archive folder", cell: (r) => <span className="text-ink-muted">{r.folder}</span>, sort: (r) => r.folder },
    { key: "z", header: "Size", cell: (r) => <span className="whitespace-nowrap">{fileSize(r.byte_size)}</span>, align: "right" },
    { key: "d", header: "Filed", cell: (r) => when(r.created_at), sort: (r) => r.created_at },
    {
      key: "s",
      header: "Drive",
      cell: (r) => (
        <div className="space-y-1">
          <SyncChip f={r} />
          {r.sync_status !== "SYNCED" && syncProblem(r.last_error) && <p className="max-w-64 text-xs text-ink-muted">{syncProblem(r.last_error)}</p>}
          {r.sync_status === "FAILED" && canRetry(r) && (
            <button className="btn-ghost text-xs" disabled={busy === r.id} onClick={() => void retry(r)} aria-label={`Retry Drive copy of ${r.filename}`}>
              {busy === r.id ? "Retrying…" : "Retry"}
            </button>
          )}
        </div>
      ),
    },
  ];
  const counts = page?.counts ?? {};
  const driveOff = page?.drive_provider === "disabled";
  return (
    <div className="space-y-5">
      <PageHeader
        title="Documents"
        subtitle="Every issued invoice, proof of delivery, vendor document and scan, filed by customer or vendor and month. Files are kept exactly as issued and are never overwritten."
      />
      <div className="grid gap-4 sm:grid-cols-3">
        <KpiCard label="Pending sync" value={counts.PENDING ?? 0} icon="documents" tone="info" note="Waiting for Google Drive" />
        <KpiCard label="Sync failed" value={counts.FAILED ?? 0} icon="documents" tone={counts.FAILED ? "danger" : "neutral"} note="Needs a retry" />
        <KpiCard label="In Drive" value={counts.SYNCED ?? 0} icon="documents" tone="success" note={driveOff ? "Drive copying is off" : "Copied to the archive"} />
      </div>
      {driveOff && <p className="alert-warning" role="status">Copying to Google Drive is turned off. Files are stored here only.</p>}
      {msg && (
        <p className={msg.tone === "error" ? "alert-danger" : "alert-success"} role={msg.tone === "error" ? "alert" : "status"}>
          {msg.text}
        </p>
      )}
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1">
          <FilterBar search={q} onSearch={setQ} options={VIEWS} value={view} onChange={setView} placeholder="File name, invoice or shipment no." />
        </div>
        <label className="text-sm">
          <span className="mb-1 block text-ink-muted">Type</span>
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value as DocFileKind | "")}>
            {KINDS.map((k) => (
              <option key={k} value={k}>{k ? DOC_KIND[k] : "All types"}</option>
            ))}
          </select>
        </label>
      </div>
      {error ? (
        <ErrorState title="Couldn't load documents" onRetry={() => void load()} />
      ) : rows === null ? (
        <LoadingRows cols={7} />
      ) : (
        <>
          <DataGrid
            rows={rows}
            columns={columns}
            rowKey={(r) => r.id}
            caption={`Documents (${page?.total ?? rows.length})`}
            empty={<EmptyState title="No documents yet" hint="Issued invoices, uploaded scans and vendor documents appear here automatically." />}
          />
          {page?.next_cursor && (
            <div className="flex justify-center">
              <button className="btn-ghost" onClick={() => void load(page.next_cursor ?? undefined)}>Load more</button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/* ================================ Invoice panel: archive + POD ================================ */

export function InvoiceFiles({ invoiceId, warehouseId }: { invoiceId: string; warehouseId: string }) {
  const { session } = useAuth();
  const [files, setFiles] = useState<DocumentFile[] | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ tone: "error" | "ok"; text: string } | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const canRead = !!session && has(session, "document.read");
  const canUpload = !!session && canInWarehouse(session, "document.manage", warehouseId);

  const load = useCallback(async () => {
    try {
      setFiles((await api.documents({ invoice_id: invoiceId })).items);
    } catch {
      setFiles([]);
    }
  }, [invoiceId]);
  useEffect(() => {
    if (canRead) void load();
  }, [canRead, load]);
  const pending = files?.some((f) => f.sync_status === "PENDING") ?? false;
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(t);
  }, [pending, load]);

  if (!canRead) return null;
  const upload = async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    const bad = checkFile(file);
    if (bad) {
      setMsg({ tone: "error", text: bad });
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const f = await api.uploadPod(invoiceId, file, note.trim() || undefined);
      setMsg({ tone: "ok", text: `Proof of delivery filed as ${f.filename}.` });
      setNote("");
      if (fileRef.current) fileRef.current.value = "";
      void load();
    } catch (e) {
      setMsg({ tone: "error", text: documentMessage(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="card space-y-3 p-5 text-sm" aria-labelledby="inv-files">
      <h2 id="inv-files" className="font-semibold">Archive</h2>
      {files === null ? (
        <p className="text-ink-muted">Loading…</p>
      ) : files.length === 0 ? (
        <p className="text-ink-muted">Nothing filed yet.</p>
      ) : (
        <ul className="space-y-2">
          {files.map((f) => (
            <li key={f.id} className="space-y-1">
              <a className={link} href={documentContentUrl(f.id)} target="_blank" rel="noreferrer">{f.filename}</a>
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-ink-muted">{DOC_KIND[f.kind]}</span>
                <SyncChip f={f} />
              </div>
              {f.note && <p className="text-xs text-ink-muted">{f.note}</p>}
            </li>
          ))}
        </ul>
      )}
      {files && files[0] && <p className="text-xs text-ink-muted">Folder: {files[0].folder}</p>}
      {canUpload && (
        <div className="space-y-2 border-t border-line pt-3">
          <h3 className="font-medium">Add proof of delivery</h3>
          <label className="block">
            <span className="mb-1 block text-ink-muted">Signed delivery file</span>
            <input ref={fileRef} type="file" accept={ACCEPT} className="block w-full text-sm" />
          </label>
          <label className="block">
            <span className="mb-1 block text-ink-muted">Note (optional)</span>
            <input className="input w-full" value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} placeholder="Signed by…" />
          </label>
          <button className="btn-primary" disabled={busy} onClick={() => void upload()}>{busy ? "Uploading…" : "Upload proof of delivery"}</button>
          <p className="text-xs text-ink-muted">Earlier files are kept; a new upload never replaces one.</p>
        </div>
      )}
      {msg && (
        <p className={msg.tone === "error" ? "alert-danger" : "alert-success"} role={msg.tone === "error" ? "alert" : "status"}>
          {msg.text}
        </p>
      )}
    </section>
  );
}
