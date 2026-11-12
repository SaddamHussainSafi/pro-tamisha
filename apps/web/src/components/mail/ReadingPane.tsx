import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { addrText, mail, mailDocument, senderOf, whenFull, whenShort, type ComposeMode, type MailAttachment, type MailLink, type MailMessage } from "../../lib/mail";
import { fileSize } from "../../lib/scans";
import { Icon, type IconName } from "../../ui/Icon";
import { Modal } from "../gmail/PagePreview";
import { LinksPanel } from "./LinksPanel";
import "./email.css";

const act = "inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-line bg-card px-3 py-1.5 text-sm font-medium text-ink transition-colors duration-150 hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50";

function Action({ icon, label, kbd, onClick, disabled, title, pressed }: { icon: IconName; label: string; kbd?: string; onClick: () => void; disabled?: boolean; title?: string; pressed?: boolean }) {
  return (
    <button type="button" className={act} onClick={onClick} disabled={disabled} title={title ?? (kbd ? `${label} (${kbd})` : label)} aria-pressed={pressed} aria-keyshortcuts={kbd}>
      <Icon name={icon} size={16} />
      <span className="hidden sm:inline">{label}</span>
      <span className="sr-only sm:hidden">{label}</span>
    </button>
  );
}

const blocked = (a: MailAttachment) => ["BLOCKED", "INFECTED"].includes(a.scan_status) || !a.downloadable;

/** One email with its conversation, files, linked records and the reply / forward / print actions.
 * The body is shown in a sandboxed frame: no scripts, no same-origin access, no network unless
 * the person asks for remote pictures. */
export function ReadingPane({
  m,
  canSend,
  onBack,
  onCompose,
  onStar,
  onOpen,
  onLoadImages,
  onLinks,
}: {
  m: MailMessage;
  canSend: boolean;
  onBack: () => void;
  onCompose: (mode: ComposeMode) => void;
  onStar: () => void;
  onOpen: (id: string) => void;
  onLoadImages: () => void;
  onLinks: (l: MailLink[]) => void;
}) {
  const [preview, setPreview] = useState<MailAttachment | null>(null);
  const [printing, setPrinting] = useState(false);
  const thread = m.thread.length > 1 ? m.thread : [];
  const files = m.attachments;
  const noSend = canSend ? undefined : "This mailbox can't send: Lark has not confirmed its sign-in, or you were given read-only access.";

  useEffect(() => {
    if (!printing) return;
    const done = () => setPrinting(false);
    window.addEventListener("afterprint", done);
    return () => window.removeEventListener("afterprint", done);
  }, [printing]);

  const header = (
    <>
      <h2 className="text-xl font-semibold leading-snug tracking-tight">{m.subject || "(no subject)"}</h2>
      <div className="flex flex-wrap items-baseline gap-x-2 text-sm">
        <span className="font-semibold">{senderOf(m)}</span>
        {m.from_name && <span className="text-ink-muted">&lt;{m.from_email}&gt;</span>}
        <time className="ml-auto text-xs text-ink-muted" dateTime={m.received_at}>
          {whenFull(m.received_at)}
        </time>
      </div>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-2 text-xs text-ink-muted">
        {m.to.length > 0 && (
          <>
            <dt>To</dt>
            <dd className="break-words">{m.to.map(addrText).join(", ")}</dd>
          </>
        )}
        {m.cc.length > 0 && (
          <>
            <dt>Cc</dt>
            <dd className="break-words">{m.cc.map(addrText).join(", ")}</dd>
          </>
        )}
        {m.bcc.length > 0 && (
          <>
            <dt>Bcc</dt>
            <dd className="break-words">{m.bcc.map(addrText).join(", ")}</dd>
          </>
        )}
        {m.reply_to.length > 0 && (
          <>
            <dt>Reply to</dt>
            <dd className="break-words">{m.reply_to.map(addrText).join(", ")}</dd>
          </>
        )}
      </dl>
    </>
  );
  const bodyFrame = (title: string, cls: string) =>
    m.body_html ? (
      // Sandboxed: no scripts, no same-origin access; only links may open a new tab.
      <iframe title={title} sandbox="allow-popups allow-popups-to-escape-sandbox" referrerPolicy="no-referrer" srcDoc={mailDocument(m.body_html, m.remote_images_loaded)} className={cls} />
    ) : m.body_text ? (
      <pre className="whitespace-pre-wrap font-sans text-[15px] leading-relaxed">{m.body_text}</pre>
    ) : (
      <p className="text-sm text-ink-muted">This email has no text.</p>
    );

  return (
    <article className="flex min-h-0 flex-1 flex-col" aria-label="Email">
      <div className="flex flex-wrap items-center gap-1.5 border-b border-line px-4 py-2.5 sm:px-5" role="toolbar" aria-label="Email actions">
        <button type="button" className="icon-btn size-10 md:hidden" aria-label="Back to the list" onClick={onBack}>
          <Icon name="chevronLeft" size={18} />
        </button>
        <Action icon="reply" label="Reply" kbd="r" onClick={() => onCompose("reply")} disabled={!canSend} title={noSend} />
        <Action icon="users" label="Reply all" kbd="a" onClick={() => onCompose("reply_all")} disabled={!canSend} title={noSend} />
        <Action icon="arrowUpRight" label="Forward" kbd="f" onClick={() => onCompose("forward")} disabled={!canSend} title={noSend} />
        <span className="ml-auto" />
        <button type="button" className={`${act} ${m.starred ? "border-warning/40 bg-warning/10" : ""}`} onClick={onStar} aria-pressed={m.starred} title={m.starred ? "Unstar (s)" : "Star (s)"} aria-keyshortcuts="s">
          <Star on={m.starred} />
          <span className="sr-only">{m.starred ? "Starred" : "Star"}</span>
        </button>
        <button type="button" className={act} onClick={() => setPrinting(true)} title="Print">
          <Icon name="printer" size={16} />
          <span className="sr-only">Print</span>
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="space-y-5 px-4 py-5 sm:px-6">
          <header className="space-y-1.5">{header}</header>
          {(m.removed_in_lark || m.warnings.length > 0) && (
            <div className="alert-warning space-y-1">
              {m.removed_in_lark && <p>This email is no longer in Lark. The copy kept here is shown.</p>}
              {m.warnings.map((w) => (
                <p key={w}>{w}</p>
              ))}
            </div>
          )}
          {thread.length > 0 && (
            <nav aria-label="Conversation" className="rounded-[var(--radius-ctl)] border border-line">
              <p className="border-b border-line px-3.5 py-2 text-xs font-medium text-ink-muted">Conversation · {thread.length} emails</p>
              <ol>
                {thread.map((t) => (
                  <li key={t.id} className="border-b border-line last:border-b-0">
                    <button
                      type="button"
                      onClick={() => onOpen(t.id)}
                      aria-current={t.id === m.id ? "true" : undefined}
                      className={`grid w-full cursor-pointer grid-cols-[minmax(0,10rem)_minmax(0,1fr)_auto] items-baseline gap-3 px-3.5 py-2 text-left text-sm hover:bg-subtle ${t.id === m.id ? "bg-brand-accent/10" : ""}`}
                    >
                      <span className={`truncate ${t.unread ? "font-semibold" : ""}`}>{senderOf(t)}</span>
                      <span className="truncate text-ink-muted">{t.snippet}</span>
                      <time className="num text-xs text-ink-muted" dateTime={t.received_at}>
                        {whenShort(t.received_at)}
                      </time>
                    </button>
                  </li>
                ))}
              </ol>
            </nav>
          )}
          {m.has_remote_images && !m.remote_images_loaded && (
            <div className="flex flex-wrap items-center gap-3 rounded-[var(--radius-ctl)] bg-subtle px-4 py-2.5 text-sm" role="note">
              <Icon name="eye" size={16} className="text-ink-muted" />
              <span className="min-w-0 flex-1">Pictures from the internet are hidden. Loading them can tell the sender you opened this email.</span>
              <button type="button" className="btn-ghost px-4 py-1.5" onClick={onLoadImages}>
                Load images
              </button>
            </div>
          )}
          <section aria-label="Message">{bodyFrame("Email message", "block h-[max(24rem,55dvh)] w-full border-0 bg-card")}</section>
          {m.body_truncated && <p className="text-xs text-ink-muted">Long message: only the first part is kept here.</p>}
          {files.length > 0 && (
            <section aria-label="Attachments" className="space-y-2">
              <h3 className="text-sm font-semibold">Attachments ({files.length})</h3>
              <ul className="grid gap-2 sm:grid-cols-2">
                {files.map((a) => (
                  <li key={a.id} className="rounded-[var(--radius-ctl)] border border-line px-3.5 py-2.5">
                    <div className="flex items-center gap-3">
                      <span aria-hidden className="grid size-9 shrink-0 place-items-center rounded-ctl bg-subtle text-ink-muted">
                        <Icon name="file" size={18} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium" title={a.filename}>
                          {a.filename}
                        </p>
                        <p className="text-xs text-ink-muted">{fileSize(a.byte_size)}</p>
                      </div>
                      {!blocked(a) && a.previewable && (
                        <button type="button" className="icon-btn size-9" aria-label={`Preview ${a.filename}`} title="Preview" onClick={() => setPreview(a)}>
                          <Icon name="eye" size={16} />
                        </button>
                      )}
                      {!blocked(a) && (
                        <a className="icon-btn size-9" href={mail.attachmentUrl(a.id)} download={a.filename} aria-label={`Download ${a.filename}`} title="Download">
                          <Icon name="download" size={16} />
                        </a>
                      )}
                    </div>
                    {blocked(a) && (
                      <p className="mt-2 flex items-start gap-1.5 text-xs text-danger" role="note">
                        <Icon name="shield" size={14} className="mt-px shrink-0" />
                        Can't be opened here{a.scan_detail ? `: ${a.scan_detail}` : "."}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}
          <LinksPanel messageId={m.id} links={m.links} onChange={onLinks} />
        </div>
      </div>
      <Modal open={!!preview} title={preview?.filename ?? ""} onClose={() => setPreview(null)} wide>
        {preview && (
          <div className="flex h-full flex-col">
            <div className="min-h-0 flex-1 bg-subtle p-3">
              {preview.mime_type.startsWith("image/") ? (
                <img src={mail.attachmentUrl(preview.id, true)} alt={preview.filename} className="mx-auto max-h-full max-w-full rounded-ctl bg-card object-contain" />
              ) : (
                <iframe title={`Preview of ${preview.filename}`} src={mail.attachmentUrl(preview.id, true)} className="h-full min-h-[60dvh] w-full rounded-ctl border border-line bg-card" />
              )}
            </div>
            <div className="flex justify-end gap-2 border-t border-line px-5 py-3">
              <a className="btn-ghost" href={mail.attachmentUrl(preview.id, true)} target="_blank" rel="noreferrer noopener">
                Open in a new tab
              </a>
              <a className="btn-primary" href={mail.attachmentUrl(preview.id)} download={preview.filename}>
                <Icon name="download" size={16} />
                Download
              </a>
            </div>
          </div>
        )}
      </Modal>
      {printing &&
        createPortal(
          <div className="mail-print">
            <div className="space-y-1.5 pb-4">{header}</div>
            {m.body_html ? (
              <iframe
                title="Email for printing"
                sandbox=""
                srcDoc={mailDocument(m.body_html, m.remote_images_loaded)}
                className="mail-print-body"
                onLoad={() => setTimeout(() => window.print(), 50)}
              />
            ) : (
              <PrintText text={m.body_text} />
            )}
            {files.length > 0 && <p className="pt-4 text-xs">Attachments: {files.map((a) => a.filename).join(", ")}</p>}
          </div>,
          document.body,
        )}
    </article>
  );
}

function PrintText({ text }: { text: string }) {
  useEffect(() => {
    const t = setTimeout(() => window.print(), 50);
    return () => clearTimeout(t);
  }, []);
  return <pre className="whitespace-pre-wrap font-sans text-sm">{text}</pre>;
}

export function Star({ on, size = 16 }: { on: boolean; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden className={`shrink-0 ${on ? "text-warning" : "text-ink-muted"}`} fill={on ? "currentColor" : "none"} stroke="currentColor" strokeWidth={1.8} strokeLinejoin="round">
      <path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3l-5.6 2.9 1.1-6.2L3 9.6l6.2-.9z" />
    </svg>
  );
}
