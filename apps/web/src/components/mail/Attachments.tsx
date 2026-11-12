import { isBlocked, mail, type MailAttachment } from "../../lib/mail";
import { fileSize } from "../../lib/scans";
import { Icon } from "../../ui/Icon";
import { Modal } from "../gmail/PagePreview";


/** File-type badge: our own shapes and tokens (no third-party file icons). */
export function FileBadge({ a, size = "sm" }: { a: Pick<MailAttachment, "filename" | "mime_type">; size?: "sm" | "lg" }) {
  const ext = (a.filename.split(".").pop() ?? "").slice(0, 4).toUpperCase();
  const kind = a.mime_type === "application/pdf" || ext === "PDF" ? "pdf" : a.mime_type.startsWith("image/") ? "img" : /^(XLSX?|CSV)$/.test(ext) ? "sheet" : /^(DOCX?|TXT|RTF)$/.test(ext) ? "doc" : "file";
  const tone = { pdf: "bg-danger/10 text-danger", img: "bg-info/10 text-info", sheet: "bg-success/12 text-success", doc: "bg-info/10 text-info", file: "bg-subtle text-ink-muted" }[kind];
  return (
    <span aria-hidden className={`grid shrink-0 place-items-center rounded-md font-semibold ${tone} ${size === "lg" ? "size-10 text-[11px]" : "h-5 min-w-5 px-1 text-[9px]"}`}>
      {kind === "img" ? <Icon name="eye" size={size === "lg" ? 18 : 12} /> : ext || <Icon name="file" size={12} />}
    </span>
  );
}

/** Chip under a list row: opens the preview (PDF / picture) or downloads the file. */
export function AttachmentChip({ a, onPreview }: { a: MailAttachment; onPreview: (a: MailAttachment) => void }) {
  const cls = "inline-flex max-w-52 cursor-pointer items-center gap-1.5 rounded-full border border-line bg-card py-0.5 pl-1 pr-2.5 text-xs text-ink hover:bg-subtle";
  if (isBlocked(a))
    return (
      <span className={`${cls} cursor-default text-ink-muted`} title={`Can't be opened here${a.scan_detail ? `: ${a.scan_detail}` : ""}`}>
        <Icon name="shield" size={14} className="text-danger" />
        <span className="truncate">{a.filename}</span>
      </span>
    );
  return a.previewable ? (
    <button type="button" className={cls} onClick={(e) => (e.stopPropagation(), onPreview(a))} aria-label={`Preview ${a.filename}`} title={a.filename}>
      <FileBadge a={a} />
      <span className="truncate">{a.filename}</span>
    </button>
  ) : (
    <a className={cls} href={mail.attachmentUrl(a.id)} download={a.filename} onClick={(e) => e.stopPropagation()} aria-label={`Download ${a.filename}`} title={a.filename}>
      <FileBadge a={a} />
      <span className="truncate">{a.filename}</span>
    </a>
  );
}

/** Attachment card in a conversation: picture thumbnail or file badge, preview + download. */
export function AttachmentCard({ a, onPreview }: { a: MailAttachment; onPreview: (a: MailAttachment) => void }) {
  const blocked = isBlocked(a);
  const img = a.previewable && a.mime_type.startsWith("image/") && !blocked;
  return (
    <li className="group w-44 overflow-hidden rounded-xl border border-line bg-card">
      <button type="button" disabled={blocked || !a.previewable} onClick={() => onPreview(a)} className="grid h-24 w-full cursor-pointer place-items-center bg-subtle disabled:cursor-default" aria-label={`Preview ${a.filename}`} tabIndex={blocked || !a.previewable ? -1 : 0}>
        {img ? <img src={mail.attachmentUrl(a.id, true)} alt="" className="h-full w-full object-cover" loading="lazy" /> : <FileBadge a={a} size="lg" />}
      </button>
      <div className="flex items-center gap-1 px-2.5 py-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium" title={a.filename}>
            {a.filename}
          </p>
          <p className="text-[11px] text-ink-muted">{fileSize(a.byte_size)}</p>
        </div>
        {!blocked && (
          <a className="grid size-7 shrink-0 place-items-center rounded-full text-ink-muted hover:bg-subtle hover:text-ink" href={mail.attachmentUrl(a.id)} download={a.filename} aria-label={`Download ${a.filename}`} title="Download">
            <Icon name="download" size={15} />
          </a>
        )}
      </div>
      {blocked && (
        <p className="flex items-start gap-1 border-t border-line px-2.5 py-1.5 text-[11px] text-danger" role="note">
          <Icon name="shield" size={12} className="mt-px shrink-0" />
          Can't be opened here{a.scan_detail ? `: ${a.scan_detail}` : "."}
        </p>
      )}
    </li>
  );
}

export function AttachmentPreview({ a, onClose }: { a: MailAttachment | null; onClose: () => void }) {
  return (
    <Modal open={!!a} title={a?.filename ?? ""} onClose={onClose} wide>
      {a && (
        <div className="flex h-full flex-col">
          <div className="min-h-0 flex-1 bg-subtle p-3">
            {a.mime_type.startsWith("image/") ? (
              <img src={mail.attachmentUrl(a.id, true)} alt={a.filename} className="mx-auto max-h-full max-w-full rounded-ctl bg-card object-contain" />
            ) : (
              <iframe title={`Preview of ${a.filename}`} src={mail.attachmentUrl(a.id, true)} className="h-full min-h-[60dvh] w-full rounded-ctl border border-line bg-card" />
            )}
          </div>
          <div className="flex justify-end gap-2 border-t border-line px-5 py-3">
            <a className="btn-ghost" href={mail.attachmentUrl(a.id, true)} target="_blank" rel="noreferrer noopener">
              Open in a new tab
            </a>
            <a className="btn-primary" href={mail.attachmentUrl(a.id)} download={a.filename}>
              <Icon name="download" size={16} />
              Download
            </a>
          </div>
        </div>
      )}
    </Modal>
  );
}
