import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  htmlText,
  mail,
  mailError,
  quoteOriginal,
  replyRecipients,
  replySubject,
  scrubHtml,
  validEmail,
  type Addr,
  type ComposeMode,
  type DraftIn,
  type MailAccount,
  type MailDraft,
  type MailMessage,
  type Outgoing,
  type Upload,
} from "../../lib/mail";
import { fileSize } from "../../lib/scans";
import { Icon } from "../../ui/Icon";
import { AddressInput } from "./AddressInput";

export type ComposeInit = { mode: ComposeMode; source?: MailMessage | null; draft?: MailDraft | null };

const SIG = "data-signature";
const sigBlock = (html: string) => (html ? `<div ${SIG}=""><p><br></p><p>--</p>${scrubHtml(html)}</div>` : `<div ${SIG}=""></div>`);

function startBody(init: ComposeInit, from: MailAccount | undefined): string {
  if (init.draft) return scrubHtml(init.draft.body_html);
  const quote = init.source ? `<p><br></p>${scrubHtml(quoteOriginal(init.source, init.mode))}` : "";
  return `<p><br></p>${sigBlock(from?.signature_html ?? "")}${quote}`;
}

/**
 * Compose / reply / forward. Autosaves a Pro Trading draft while typing (POST then PUT), and
 * sends with ONE idempotency key for the whole compose session: pressing Send twice, or again
 * after a dropped connection, answers with the same queued message instead of sending twice.
 */
export function Compose({ accounts, accountId, init, onClose, onSent }: { accounts: MailAccount[]; accountId: string; init: ComposeInit; onClose: () => void; onSent: (o: Outgoing) => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const editor = useRef<HTMLDivElement>(null);
  const titleId = useId();
  const src = init.source ?? null;
  const draftSource = init.draft?.source_message_id ?? null;
  const replying = init.mode !== "new";
  // A reply or forward goes out from the mailbox the email arrived in (the server checks this too).
  const [from, setFrom] = useState(init.draft?.account_id ?? src?.account_id ?? accountId);
  const fromAcc = accounts.find((a) => a.id === from);
  const startRcpt = useMemo(() => (src && !init.draft ? replyRecipients(src, init.mode, fromAcc?.address ?? "") : { to: [], cc: [] }), []); // eslint-disable-line react-hooks/exhaustive-deps
  const [to, setTo] = useState<Addr[]>(init.draft?.to ?? startRcpt.to);
  const [cc, setCc] = useState<Addr[]>(init.draft?.cc ?? startRcpt.cc);
  const [bcc, setBcc] = useState<Addr[]>(init.draft?.bcc ?? []);
  const [showCc, setShowCc] = useState(cc.length > 0 || bcc.length > 0);
  const [subject, setSubject] = useState(init.draft?.subject ?? (src ? replySubject(src.subject, init.mode) : ""));
  const [body, setBody] = useState(() => startBody(init, fromAcc));
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [earlier] = useState<string[]>(init.draft?.upload_ids ?? []); // names are not returned with a draft
  const [uploading, setUploading] = useState<{ name: string; error?: string }[]>([]);
  const fwdFiles = useMemo(() => (init.mode === "forward" && src ? src.attachments.filter((a) => a.downloadable && !["BLOCKED", "INFECTED"].includes(a.scan_status)) : []), [init.mode, src]);
  const [fwd, setFwd] = useState<string[]>(fwdFiles.map((a) => a.id));
  const [draftId, setDraftId] = useState<string | null>(init.draft?.id ?? null);
  const [saveState, setSaveState] = useState<{ kind: "idle" | "saving" | "saved" | "error"; at?: Date; msg?: string }>({ kind: init.draft ? "saved" : "idle", at: init.draft ? new Date(init.draft.updated_at) : undefined });
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const idemKey = useRef(crypto.randomUUID());
  const draftRef = useRef<string | null>(draftId);
  const saving = useRef<Promise<string | null>>(Promise.resolve(draftId));
  const touched = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const canSend = !!fromAcc?.can_send;
  const senders = accounts.filter((a) => a.can_send || a.id === from);

  useEffect(() => {
    const d = ref.current;
    if (editor.current) editor.current.innerHTML = body;
    if (d && !d.open) d.showModal();
    // showModal focuses the first control (Close); start in the field that matters instead.
    (d?.querySelector<HTMLElement>("[data-autofocus]") ?? editor.current)?.focus();
    // The editor owns its HTML after the first paint.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const payload = useCallback(
    (): DraftIn => ({
      account_id: from,
      mode: init.mode,
      source_message_id: src?.id ?? draftSource,
      to,
      cc,
      bcc,
      subject,
      body_html: body,
      upload_ids: [...earlier, ...uploads.map((u) => u.id)],
      forward_attachment_ids: fwd,
    }),
    [from, init.mode, src, draftSource, to, cc, bcc, subject, body, earlier, uploads, fwd],
  );

  /** Save now (create the first time); one save at a time. Resolves to the draft id. */
  const save = useCallback((): Promise<string | null> => {
    if (!canSend) return Promise.resolve(draftRef.current);
    const b = payload();
    saving.current = saving.current.then(async () => {
      setSaveState({ kind: "saving" });
      try {
        const d = draftRef.current ? await mail.saveDraft(draftRef.current, b) : await mail.createDraft(b);
        draftRef.current = d.id;
        setDraftId(d.id);
        setSaveState({ kind: "saved", at: new Date() });
      } catch (err) {
        setSaveState({ kind: "error", msg: mailError(err) });
      }
      return draftRef.current;
    });
    return saving.current;
  }, [canSend, payload]);

  // Autosave 1.5 s after the last change.
  const sig = JSON.stringify(payload());
  useEffect(() => {
    if (!touched.current) return;
    const t = setTimeout(() => void save(), 1500);
    return () => clearTimeout(t);
  }, [sig]); // eslint-disable-line react-hooks/exhaustive-deps
  const change = <T,>(fn: (v: T) => void) => (v: T) => {
    touched.current = true;
    fn(v);
  };

  function switchSender(id: string) {
    touched.current = true;
    setFrom(id);
    const acc = accounts.find((a) => a.id === id);
    const el = editor.current?.querySelector(`[${SIG}]`);
    if (el && editor.current) {
      el.outerHTML = sigBlock(acc?.signature_html ?? "");
      setBody(editor.current.innerHTML);
    }
  }

  async function addFiles(files: FileList | null) {
    if (!files) return;
    touched.current = true;
    for (const f of Array.from(files)) {
      setUploading((u) => [...u, { name: f.name }]);
      try {
        const up = await mail.upload(f);
        setUploads((u) => [...u, up]);
        setUploading((u) => u.filter((x) => x.name !== f.name));
      } catch (err) {
        setUploading((u) => u.map((x) => (x.name === f.name ? { ...x, error: mailError(err) } : x)));
      }
    }
    if (fileInput.current) fileInput.current.value = "";
  }

  const all = [...to, ...cc, ...bcc];
  const invalid = all.filter((a) => !validEmail(a.email));
  const busyUploading = uploading.some((u) => !u.error);
  const problem = !canSend
    ? fromAcc
      ? `${fromAcc.label} can't send yet: Lark has not confirmed its sign-in, or you were given read-only access. Ask a Super Admin (Settings > Email Services).`
      : "Choose a mailbox to send from."
    : !all.length
      ? "Add at least one recipient."
      : invalid.length
        ? "Fix the addresses shown in red."
        : busyUploading
          ? "Wait for the files to finish uploading."
          : null;

  async function send() {
    if (problem || sending) return;
    setSending(true);
    setError(null);
    try {
      const id = touched.current || !draftRef.current ? await save() : draftRef.current;
      const out = await mail.send({ ...payload(), draft_id: id }, idemKey.current);
      ref.current?.close();
      onSent(out);
    } catch (err) {
      // The key is kept: pressing Send again cannot queue a second copy.
      setError(mailError(err));
      setSending(false);
    }
  }

  async function close() {
    if (touched.current && canSend && (all.length || subject || htmlText(body).trim() || uploads.length)) await save();
    ref.current?.close();
    onClose();
  }
  async function discard() {
    if (draftRef.current) {
      try {
        await mail.discardDraft(draftRef.current);
      } catch (err) {
        setError(mailError(err));
        return;
      }
    }
    ref.current?.close();
    onClose();
  }

  const exec = (cmd: string, arg?: string) => {
    editor.current?.focus();
    document.execCommand(cmd, false, arg);
    if (editor.current) {
      touched.current = true;
      setBody(editor.current.innerHTML);
    }
  };
  const tool = "grid size-9 cursor-pointer place-items-center rounded-ctl text-sm text-ink hover:bg-subtle";
  const title = init.mode === "reply" ? "Reply" : init.mode === "reply_all" ? "Reply all" : init.mode === "forward" ? "Forward" : init.draft ? "Draft" : "New email";

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(e) => {
        e.preventDefault();
        void close();
      }}
      className="m-auto flex h-[100dvh] max-h-[100dvh] w-screen max-w-none flex-col overflow-hidden border-0 bg-card p-0 text-ink shadow-[0_8px_24px_rgb(24_24_27/0.12)] backdrop:bg-ink/40 sm:h-[min(46rem,calc(100dvh-2rem))] sm:w-[min(54rem,calc(100vw-2rem))] sm:rounded-card [&:not([open])]:hidden"
    >
      <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3">
        <h2 id={titleId} className="truncate text-lg font-semibold tracking-tight">
          {title}
        </h2>
        <span className="ml-auto text-xs text-ink-muted" aria-live="polite">
          {saveState.kind === "saving" ? "Saving draft…" : saveState.kind === "saved" && saveState.at ? `Draft saved ${saveState.at.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}` : saveState.kind === "error" ? `Draft not saved: ${saveState.msg}` : ""}
        </span>
        <button type="button" className="icon-btn size-9" aria-label="Close (the draft is kept)" title="Close (the draft is kept)" onClick={() => void close()}>
          <Icon name="x" size={16} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5">
        <div className="flex items-center gap-3 border-b border-line py-2">
          <label htmlFor={`${titleId}-from`} className="w-12 shrink-0 text-sm text-ink-muted">
            From
          </label>
          <select id={`${titleId}-from`} className="input min-w-0 flex-1 py-1.5 sm:w-auto sm:flex-none" value={from} disabled={replying} onChange={(e) => switchSender(e.target.value)}>
            {senders.map((a) => (
              <option key={a.id} value={a.id}>
                {a.label} &lt;{a.address}&gt;
              </option>
            ))}
          </select>
          {!showCc && (
            <button type="button" className="ml-auto cursor-pointer text-sm font-medium text-brand-primary-hover hover:underline" onClick={() => setShowCc(true)}>
              Cc / Bcc
            </button>
          )}
        </div>
        <AddressInput label="To" value={to} onChange={change(setTo)} autoFocus={init.mode === "new" || init.mode === "forward"} />
        {showCc && (
          <>
            <AddressInput label="Cc" value={cc} onChange={change(setCc)} />
            <AddressInput label="Bcc" value={bcc} onChange={change(setBcc)} />
          </>
        )}
        <div className="flex items-center gap-3 border-b border-line py-1.5 focus-within:border-brand-primary">
          <label htmlFor={`${titleId}-subj`} className="w-12 shrink-0 text-sm text-ink-muted">
            Subject
          </label>
          <input id={`${titleId}-subj`} className="min-w-0 flex-1 bg-transparent py-1.5 text-sm outline-none" value={subject} onChange={(e) => change(setSubject)(e.target.value)} />
        </div>
        <div className="sticky top-0 z-10 flex flex-wrap items-center gap-0.5 border-b border-line bg-card py-1" role="toolbar" aria-label="Formatting">
          <button type="button" className={`${tool} font-bold`} aria-label="Bold" onMouseDown={(e) => e.preventDefault()} onClick={() => exec("bold")}>
            B
          </button>
          <button type="button" className={`${tool} italic`} aria-label="Italic" onMouseDown={(e) => e.preventDefault()} onClick={() => exec("italic")}>
            I
          </button>
          <button type="button" className={`${tool} underline`} aria-label="Underline" onMouseDown={(e) => e.preventDefault()} onClick={() => exec("underline")}>
            U
          </button>
          <span className="mx-1 h-5 w-px bg-line" aria-hidden />
          <button type="button" className={tool} aria-label="Bulleted list" onMouseDown={(e) => e.preventDefault()} onClick={() => exec("insertUnorderedList")}>
            <Icon name="menu" size={16} />
          </button>
          <button type="button" className={`${tool} num text-xs`} aria-label="Numbered list" onMouseDown={(e) => e.preventDefault()} onClick={() => exec("insertOrderedList")}>
            1.
          </button>
          <button
            type="button"
            className={tool}
            aria-label="Link"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              const url = window.prompt("Link address (https://…)", "https://");
              if (url && /^(https?:\/\/|mailto:)/i.test(url.trim())) exec("createLink", url.trim());
            }}
          >
            <Icon name="external" size={16} />
          </button>
          <span className="mx-1 h-5 w-px bg-line" aria-hidden />
          <button type="button" className={tool} aria-label="Attach files" title="Attach files" onClick={() => fileInput.current?.click()}>
            <Icon name="upload" size={16} />
          </button>
          <input ref={fileInput} type="file" multiple className="sr-only" tabIndex={-1} aria-label="Files to attach" onChange={(e) => void addFiles(e.target.files)} />
        </div>
        <div
          ref={editor}
          role="textbox"
          aria-multiline="true"
          aria-label="Message"
          contentEditable
          suppressContentEditableWarning
          className="min-h-56 py-3 text-[15px] leading-relaxed outline-none [&_a]:text-brand-primary-hover [&_a]:underline [&_blockquote]:my-2 [&_blockquote]:border-l-[3px] [&_blockquote]:border-line [&_blockquote]:pl-3 [&_blockquote]:text-ink-muted [&_ol]:list-decimal [&_ol]:pl-6 [&_table]:max-w-full [&_ul]:list-disc [&_ul]:pl-6"
          onInput={(e) => {
            touched.current = true;
            setBody(e.currentTarget.innerHTML);
          }}
          onPaste={(e) => {
            // Paste as plain text: no foreign styling or hidden content enters the email.
            e.preventDefault();
            document.execCommand("insertText", false, e.clipboardData.getData("text/plain"));
          }}
        />
        {(uploads.length > 0 || uploading.length > 0 || earlier.length > 0 || fwdFiles.length > 0) && (
          <section aria-label="Attachments" className="space-y-2 border-t border-line py-3">
            <ul className="flex flex-wrap gap-2">
              {earlier.length > 0 && (
                <li className="chip border-line bg-subtle py-1 text-ink">
                  <Icon name="file" size={14} />
                  {earlier.length === 1 ? "1 file attached earlier" : `${earlier.length} files attached earlier`}
                </li>
              )}
              {uploads.map((u) => (
                <li key={u.id} className="chip border-line bg-card py-1 pr-1 text-ink">
                  <Icon name="file" size={14} />
                  <span className="max-w-48 truncate">{u.filename}</span>
                  <span className="text-ink-muted">{fileSize(u.byte_size)}</span>
                  <button
                    type="button"
                    className="grid size-5 cursor-pointer place-items-center rounded-full hover:bg-line"
                    aria-label={`Remove ${u.filename} from this email`}
                    onClick={() => {
                      touched.current = true;
                      setUploads((x) => x.filter((y) => y.id !== u.id));
                    }}
                  >
                    <Icon name="x" size={12} />
                  </button>
                </li>
              ))}
              {uploading.map((u) => (
                <li key={u.name} className={`chip py-1 ${u.error ? "border-danger/40 bg-danger/5 text-danger" : "border-line bg-subtle text-ink-muted"}`} role={u.error ? "alert" : "status"}>
                  <Icon name={u.error ? "alert" : "clock"} size={14} />
                  <span className="max-w-48 truncate">{u.name}</span>
                  <span>{u.error ?? "Uploading…"}</span>
                  {u.error && (
                    <button type="button" className="grid size-5 cursor-pointer place-items-center rounded-full hover:bg-line" aria-label={`Dismiss ${u.name}`} onClick={() => setUploading((x) => x.filter((y) => y.name !== u.name))}>
                      <Icon name="x" size={12} />
                    </button>
                  )}
                </li>
              ))}
            </ul>
            {fwdFiles.length > 0 && (
              <fieldset className="space-y-1">
                <legend className="text-xs font-medium text-ink-muted">Files from the original email</legend>
                {fwdFiles.map((a) => (
                  <label key={a.id} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      className="size-4 accent-brand-primary"
                      checked={fwd.includes(a.id)}
                      onChange={(e) => {
                        touched.current = true;
                        setFwd((x) => (e.target.checked ? [...x, a.id] : x.filter((y) => y !== a.id)));
                      }}
                    />
                    {a.filename} <span className="text-ink-muted">{fileSize(a.byte_size)}</span>
                  </label>
                ))}
              </fieldset>
            )}
          </section>
        )}
      </div>
      {(error || problem) && (
        <p role={error ? "alert" : "note"} className={`mx-5 mb-2 ${error ? "alert-danger" : "text-sm text-ink-muted"}`}>
          {error ?? problem}
        </p>
      )}
      <div className="flex items-center gap-2 border-t border-line px-4 py-3 sm:gap-2.5 sm:px-5">
        <button type="button" className="btn-ghost px-4" onClick={() => void discard()} disabled={sending}>
          Discard<span className="hidden sm:inline"> draft</span>
        </button>
        <span className="ml-auto" />
        <button type="button" className="btn-ghost px-4" onClick={() => void close()} disabled={sending}>
          Save<span className="hidden sm:inline"> and close</span>
        </button>
        <button type="button" className="btn-accent px-5" onClick={() => void send()} disabled={!!problem || sending} aria-describedby={problem ? undefined : undefined}>
          <Icon name="arrowUpRight" size={16} />
          {sending ? "Sending…" : "Send"}
        </button>
      </div>
    </dialog>
  );
}
