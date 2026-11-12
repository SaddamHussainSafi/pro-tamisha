import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  addrText,
  htmlText,
  mail,
  mailDocument,
  plainHtml,
  mailError,
  quoteOriginal,
  replyRecipients,
  replySubject,
  scrubHtml,
  senderOf,
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
import { sigForDisplay, sigForWire } from "../../lib/signature";
import { AddressInput } from "./AddressInput";
import { EmojiPicker, FormatBar } from "./Formatting";
import { Menu } from "./Menu";

export type ComposeInit = { mode: ComposeMode; source?: MailMessage | null; draft?: MailDraft | null };

const SIG = "data-signature";
// The signature block, with the usual "--" separator; the logo is shown from our server here and
// goes out as cid:tt-logo (the server embeds the picture).
const sigBlock = (html: string) => (html ? `<div ${SIG}=""><p><br></p><p>--</p>${scrubHtml(sigForDisplay(html))}</div>` : `<div ${SIG}=""></div>`);

/** Which signature a mailbox uses for me, and when. */
export type SigChoice = { html: string; onNew: boolean; onReply: boolean };

function startBody(init: ComposeInit, sig: SigChoice): string {
  if (init.draft) return scrubHtml(sigForDisplay(init.draft.body_html));
  const quote = init.source ? `<p><br></p>${scrubHtml(quoteOriginal(init.source, init.mode))}` : "";
  const use = init.mode === "new" ? sig.onNew : sig.onReply;
  return `<p><br></p>${sigBlock(use ? sig.html : "")}${quote}`;
}

const composeLabel = (init: ComposeInit) => (init.mode === "reply" ? "Reply" : init.mode === "reply_all" ? "Reply all" : init.mode === "forward" ? "Forward" : init.draft ? "Draft" : "New message");

type Props = {
  accounts: MailAccount[];
  accountId: string;
  init: ComposeInit;
  onClose: () => void;
  onSent: (o: Outgoing) => void;
  /** Addresses seen in this mailbox, offered while typing a recipient. */
  suggestions?: Addr[];
  /** My signature for a mailbox (and whether it goes on new mail / replies). */
  signatureFor?: (accountId: string) => SigChoice;
  /** Open the signature settings. */
  onManageSignature?: () => void;
};

/**
 * Compose / reply / forward. Autosaves a Pro Trading draft while typing (POST then PUT), and
 * sends with ONE idempotency key for the whole compose session: pressing Send twice, or again
 * after a dropped connection, answers with the same queued message instead of sending twice.
 *
 * `floating`: the window anchored bottom-right (minimise, full screen, close = keep draft).
 * `inline`: the reply box at the bottom of a conversation.
 */
function ComposeForm({ accounts, accountId, init, onClose, onSent, suggestions = [], signatureFor, onManageSignature, variant, onTitle, closeRef }: Props & { variant: "floating" | "inline"; onTitle?: (t: string) => void; closeRef?: { current: (() => Promise<void>) | null } }) {
  const editor = useRef<HTMLDivElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const uid = useId();
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
  const sigOf = (id: string): SigChoice => signatureFor?.(id) ?? { html: accounts.find((a) => a.id === id)?.signature_html ?? "", onNew: true, onReply: true };
  const [body, setBody] = useState(() => startBody(init, sigOf(from)));
  const [uploads, setUploads] = useState<Upload[]>(init.draft?.uploads ?? []);
  // Only for an older answer without file names: keep the ids so the files stay attached.
  const [earlier] = useState<string[]>(init.draft && !init.draft.uploads ? init.draft.upload_ids : []);
  const [uploading, setUploading] = useState<{ name: string; error?: string }[]>([]);
  const fwdFiles = useMemo(() => (init.mode === "forward" && src ? src.attachments.filter((a) => a.downloadable && !["BLOCKED", "INFECTED"].includes(a.scan_status)) : []), [init.mode, src]);
  const [fwd, setFwd] = useState<string[]>(fwdFiles.map((a) => a.id));
  const [saveState, setSaveState] = useState<{ kind: "idle" | "saving" | "saved" | "error"; at?: Date; msg?: string }>({ kind: init.draft ? "saved" : "idle", at: init.draft ? new Date(init.draft.updated_at) : undefined });
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showTools, setShowTools] = useState(true);
  const [plain, setPlain] = useState(false);
  const [spell, setSpell] = useState(true);
  const [printing, setPrinting] = useState(false);
  const [photoNote, setPhotoNote] = useState(false);
  const [sigState, setSigState] = useState(false);
  const photoInput = useRef<HTMLInputElement>(null);
  const idemKey = useRef(crypto.randomUUID());
  const draftRef = useRef<string | null>(init.draft?.id ?? null);
  const saving = useRef<Promise<string | null>>(Promise.resolve(draftRef.current));
  const touched = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const canSend = !!fromAcc?.can_send;
  const senders = accounts.filter((a) => a.can_send || a.id === from);

  useEffect(() => {
    if (editor.current) editor.current.innerHTML = body;
    (root.current?.querySelector<HTMLElement>("[data-autofocus]") ?? editor.current)?.focus();
    // The editor owns its HTML after the first paint.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    onTitle?.(subject);
  }, [subject, onTitle]);

  const payload = useCallback(
    (): DraftIn => ({
      account_id: from,
      mode: init.mode,
      source_message_id: src?.id ?? draftSource,
      to,
      cc,
      bcc,
      subject,
      body_html: sigForWire(body),
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
    const el = editor.current?.querySelector(`[${SIG}]`);
    if (el && editor.current) {
      const s = sigOf(id);
      el.outerHTML = sigBlock((init.mode === "new" ? s.onNew : s.onReply) ? s.html : "");
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
      onSent(out);
    } catch (err) {
      // The key is kept: pressing Send again cannot queue a second copy.
      setError(mailError(err));
      setSending(false);
    }
  }

  const close = useCallback(async () => {
    if (touched.current && canSend && (all.length || subject || htmlText(body).trim() || uploads.length)) await save();
    onClose();
  }, [canSend, all.length, subject, body, uploads.length, save, onClose]);
  useEffect(() => {
    if (closeRef) closeRef.current = close;
  }, [close, closeRef]);

  async function discard() {
    if (draftRef.current) {
      try {
        await mail.discardDraft(draftRef.current);
      } catch (err) {
        setError(mailError(err));
        return;
      }
    }
    onClose();
  }

  const exec = (cmd: string, arg?: string) => {
    editor.current?.focus();
    // Inline CSS rather than <font> attributes: the server's allow-list keeps these styles.
    document.execCommand("styleWithCSS", false, "true");
    document.execCommand(cmd, false, arg);
    if (editor.current) {
      touched.current = true;
      setBody(editor.current.innerHTML);
    }
  };
  const tool = "grid size-9 shrink-0 cursor-pointer place-items-center rounded-full text-sm text-ink-muted hover:bg-subtle hover:text-ink disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent";
  void sigState; // re-render after switching (the editor DOM is the truth)
  const sigOn = !!editor.current?.querySelector(`[${SIG}]`)?.innerHTML.trim();
  function setSignature(on: boolean) {
    if (!editor.current) return;
    const el = editor.current.querySelector(`[${SIG}]`);
    const html = sigBlock(on ? sigOf(from).html : "");
    if (el) el.outerHTML = html;
    else {
      // No block yet: put it under the first paragraph (below the cursor's line, above any quote).
      const first = editor.current.firstElementChild;
      if (first) first.insertAdjacentHTML("afterend", html);
      else editor.current.insertAdjacentHTML("beforeend", html);
    }
    setSigState((x) => !x);
    touched.current = true;
    setBody(editor.current.innerHTML);
  }
  function togglePlain() {
    if (!editor.current) return;
    if (!plain) {
      editor.current.innerHTML = plainHtml(editor.current.innerHTML);
      touched.current = true;
      setBody(editor.current.innerHTML);
    }
    setPlain((x) => !x);
  }
  const row = "flex items-center gap-2 border-b border-line py-1 focus-within:border-brand-primary";
  const saved =
    saveState.kind === "saving" ? "Saving…" : saveState.kind === "saved" && saveState.at ? `Draft saved ${saveState.at.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}` : saveState.kind === "error" ? `Draft not saved: ${saveState.msg}` : "";

  return (
    <div ref={root} className="flex min-h-0 flex-1 flex-col" onKeyDown={(e) => (e.metaKey || e.ctrlKey) && e.key === "Enter" && (e.preventDefault(), void send())}>
      <div className={`min-h-0 flex-1 overflow-y-auto ${variant === "inline" ? "px-4" : "px-4"}`}>
        {(variant === "floating" || (!replying && senders.length > 1) || !canSend) && (
          <div className={row}>
            <label htmlFor={`${uid}-from`} className="w-14 shrink-0 text-sm text-ink-muted">
              From
            </label>
            <select id={`${uid}-from`} className="min-w-0 flex-1 cursor-pointer truncate bg-transparent py-1.5 text-sm outline-none disabled:cursor-default" value={from} disabled={replying} onChange={(e) => switchSender(e.target.value)}>
              {senders.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.label} &lt;{a.address}&gt;
                </option>
              ))}
            </select>
          </div>
        )}
        <div className={`relative ${showCc ? "" : "[&>div:first-child]:pr-20"}`}>
          <AddressInput label="To" value={to} onChange={change(setTo)} suggestions={suggestions} autoFocus={init.mode === "new" || init.mode === "forward"} />
          {!showCc && (
            <span className="absolute right-0 top-2.5 flex gap-2 text-sm text-ink-muted">
              <button type="button" className="cursor-pointer hover:text-ink hover:underline" onClick={() => setShowCc(true)}>
                Cc
              </button>
              <button type="button" className="cursor-pointer hover:text-ink hover:underline" onClick={() => setShowCc(true)}>
                Bcc
              </button>
            </span>
          )}
        </div>
        {showCc && (
          <>
            <AddressInput label="Cc" value={cc} onChange={change(setCc)} suggestions={suggestions} />
            <AddressInput label="Bcc" value={bcc} onChange={change(setBcc)} suggestions={suggestions} />
          </>
        )}
        {(variant === "floating" || init.mode === "forward" || init.draft) && (
          <div className={row}>
            <label htmlFor={`${uid}-subj`} className="sr-only">
              Subject
            </label>
            <input id={`${uid}-subj`} placeholder="Subject" className="min-w-0 flex-1 bg-transparent py-1.5 text-sm outline-none placeholder:text-ink-muted" value={subject} onChange={(e) => change(setSubject)(e.target.value)} />
          </div>
        )}
        <div
          ref={editor}
          role="textbox"
          aria-multiline="true"
          aria-label="Message"
          contentEditable
          spellCheck={spell}
          suppressContentEditableWarning
          className={`py-3 text-sm leading-relaxed outline-none [&_a]:text-brand-primary-hover [&_a]:underline [&_blockquote]:my-2 [&_blockquote]:border-l-[3px] [&_blockquote]:border-line [&_blockquote]:pl-3 [&_blockquote]:text-ink-muted [&_ol]:list-decimal [&_ol]:pl-6 [&_table]:max-w-full [&_ul]:list-disc [&_ul]:pl-6 ${variant === "inline" ? "min-h-40" : "min-h-48"}`}
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
          <section aria-label="Attachments" className="space-y-2 pb-3">
            <ul className="flex flex-wrap gap-2">
              {earlier.length > 0 && (
                <li className="chip border-line bg-subtle py-1 text-ink">
                  <Icon name="paperclip" size={14} />
                  {earlier.length === 1 ? "1 file attached earlier" : `${earlier.length} files attached earlier`}
                </li>
              )}
              {uploads.map((u) => (
                <li key={u.id} className="chip border-line bg-subtle py-1 pr-1 text-ink">
                  <Icon name="paperclip" size={14} />
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
        <p role={error ? "alert" : "note"} className={`mx-4 mb-2 ${error ? "alert-danger py-2" : "text-xs text-ink-muted"}`}>
          {error ?? problem}
        </p>
      )}
      {showTools && !plain && <FormatBar exec={exec} />}
      {photoNote && <p className="mx-4 mt-1 text-xs text-ink-muted">Pictures are added as attachments: pictures inside the text can't be sent from here yet.</p>}
      <div className="flex items-center gap-0.5 px-3 py-2.5">
        <button type="button" className="btn-accent mr-1.5 gap-2 px-6 py-2" onClick={() => void send()} disabled={!!problem || sending} title="Send (Ctrl / ⌘ Enter)">
          {sending ? "Sending…" : "Send"}
        </button>
        <button type="button" className={`${tool} ${showTools && !plain ? "bg-line/70 text-ink" : ""}`} aria-pressed={showTools && !plain} disabled={plain} aria-label="Formatting options" title="Formatting options" onClick={() => setShowTools((x) => !x)}>
          <span className="text-[15px] font-semibold underline">A</span>
        </button>
        <button type="button" className={tool} aria-label="Attach files" title="Attach files" onClick={() => fileInput.current?.click()}>
          <Icon name="paperclip" size={17} />
        </button>
        <input ref={fileInput} type="file" multiple className="sr-only" tabIndex={-1} aria-label="Files to attach" onChange={(e) => void addFiles(e.target.files)} />
        <button
          type="button"
          className={`${tool} max-sm:hidden`}
          aria-label="Insert link"
          title="Insert link"
          disabled={plain}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => {
            const url = window.prompt("Link address (https://…)", "https://");
            if (url && /^(https?:\/\/|mailto:)/i.test(url.trim())) exec("createLink", url.trim());
          }}
        >
          <Icon name="external" size={17} />
        </button>
        <span className="max-sm:hidden">
          <EmojiPicker onPick={(e) => exec("insertText", e)} />
        </span>
        <button type="button" className={`${tool} max-sm:hidden`} aria-label="Insert photo" title="Insert photo" onClick={() => photoInput.current?.click()}>
          <Icon name="image" size={17} />
        </button>
        <input
          ref={photoInput}
          type="file"
          accept="image/*"
          multiple
          className="sr-only"
          tabIndex={-1}
          aria-label="Photos to insert"
          onChange={(e) => {
            setPhotoNote(true);
            void addFiles(e.target.files);
          }}
        />
        <span className="max-sm:hidden">
          <Menu
            label="Signature"
            up
            button={<Icon name="signature" size={17} />}
            items={[
              { heading: "Signature" },
              { label: sigOf(from).html ? "My signature" : "My signature (not set up yet)", checked: sigOn, onSelect: () => (sigOf(from).html ? setSignature(true) : onManageSignature?.()) },
              { label: "No signature", checked: !sigOn, onSelect: () => setSignature(false) },
              ...(onManageSignature ? ["divider" as const, { label: "Manage signatures…", onSelect: onManageSignature }] : []),
            ]}
          />
        </span>
        <Menu
          label="More options"
          align="right"
          up
          button={<Icon name="more" size={17} />}
          items={[
            { label: "Plain text mode", checked: plain, onSelect: togglePlain },
            { label: "Check spelling", checked: spell, onSelect: () => setSpell((x) => !x) },
            "divider",
            { label: "Print", onSelect: () => setPrinting(true) },
          ]}
        />
        <span className="ml-auto min-w-0 truncate px-2 text-xs text-ink-muted" aria-live="polite">
          {saved}
        </span>
        <button type="button" className={tool} aria-label="Discard draft" title="Discard draft (only the Pro Trading draft is removed; no email is deleted)" onClick={() => void discard()} disabled={sending}>
          <Icon name="discard" size={17} />
        </button>
      </div>
      {printing &&
        createPortal(
          <div className="mail-print">
            <h1 className="pb-2 text-xl font-semibold">{subject || "(no subject)"}</h1>
            <p className="pb-4 text-xs">
              From: {fromAcc?.address} · To: {to.map(addrText).join(", ")}
              {cc.length ? ` · Cc: ${cc.map(addrText).join(", ")}` : ""}
            </p>
            <iframe title="Draft for printing" sandbox="" srcDoc={mailDocument(body, false)} className="mail-print-body" onLoad={() => setTimeout(() => window.print(), 50)} />
          </div>,
          document.body,
        )}
    </div>
  );
}

/** The floating compose window, anchored bottom-right like a desktop mail client. Up to two
 * stack side by side; on phones it fills the screen. Closing keeps the draft. */
export function ComposeWindow({ slot, ...p }: Props & { slot: number }) {
  const [min, setMin] = useState(false);
  const [full, setFull] = useState(false);
  const [title, setTitle] = useState("");
  const label = composeLabel(p.init);
  const closeRef = useRef<(() => Promise<void>) | null>(null);
  const close = () => void (closeRef.current ? closeRef.current() : p.onClose());
  const right = `${1 + slot * 38}rem`;
  return (
    <>
      {full && <div className="fixed inset-0 z-40 hidden bg-ink/40 sm:block" aria-hidden onClick={() => setFull(false)} />}
      <section
        role="dialog"
        aria-label={label}
        style={full ? undefined : ({ "--right": right } as React.CSSProperties)}
        onKeyDown={(e) => e.key === "Escape" && (e.stopPropagation(), close())}
        className={`fixed z-50 flex flex-col overflow-hidden bg-card text-ink shadow-[0_8px_32px_rgb(24_24_27/0.22)] max-sm:inset-0 ${
          full ? "sm:inset-x-[max(1rem,calc(50vw-36rem))] sm:inset-y-8 sm:rounded-card" : min ? "sm:bottom-0 sm:right-[var(--right)] sm:w-72 sm:rounded-t-2xl max-sm:hidden" : "sm:bottom-0 sm:right-[var(--right)] sm:h-[min(38rem,calc(100dvh-5rem))] sm:w-[min(37rem,calc(100vw-2rem))] sm:rounded-t-2xl"
        }`}
      >
        <div className="flex shrink-0 items-center gap-1 bg-subtle py-1.5 pl-4 pr-1.5">
          <button type="button" className="min-w-0 flex-1 cursor-pointer truncate text-left text-sm font-semibold" onClick={() => setMin((m) => !m)} aria-expanded={!min}>
            {title.trim() || label}
          </button>
          <button type="button" className="grid size-8 cursor-pointer place-items-center rounded-full text-ink-muted hover:bg-line hover:text-ink max-sm:hidden" aria-label={min ? "Restore" : "Minimise"} title={min ? "Restore" : "Minimise"} onClick={() => setMin((m) => !m)}>
            <Icon name="minimize" size={16} />
          </button>
          <button
            type="button"
            className="grid size-8 cursor-pointer place-items-center rounded-full text-ink-muted hover:bg-line hover:text-ink max-sm:hidden"
            aria-label={full ? "Exit full screen" : "Full screen"}
            title={full ? "Exit full screen" : "Full screen"}
            onClick={() => {
              setFull((f) => !f);
              setMin(false);
            }}
          >
            <Icon name={full ? "minimizeWin" : "maximize"} size={15} />
          </button>
          <button type="button" className="grid size-8 cursor-pointer place-items-center rounded-full text-ink-muted hover:bg-line hover:text-ink" aria-label="Save and close" title="Save draft and close" onClick={close}>
            <Icon name="x" size={16} />
          </button>
        </div>
        <div className={`min-h-0 flex-1 flex-col ${min ? "hidden" : "flex"}`}>
          <ComposeForm {...p} variant="floating" onTitle={setTitle} closeRef={closeRef} />
        </div>
      </section>
    </>
  );
}

/** Reply / forward written right under the conversation, like a modern mail client. */
export function InlineCompose(p: Props) {
  const me = p.accounts.find((a) => a.id === (p.init.source?.account_id ?? p.accountId))?.address ?? "";
  const first = p.init.source ? replyRecipients(p.init.source, p.init.mode === "reply_all" ? "reply_all" : "reply", me).to[0] : undefined;
  const to = first ? first.name || first.email : p.init.source ? senderOf(p.init.source) : "";
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    ref.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, []);
  return (
    <section ref={ref} aria-label={composeLabel(p.init)} className="flex flex-col rounded-2xl border border-line bg-card shadow-[0_1px_3px_rgb(24_24_27/0.08)]">
      <p className="flex items-center gap-2 px-4 pt-3 text-xs text-ink-muted">
        <Icon name={p.init.mode === "forward" ? "arrowUpRight" : "reply"} size={14} />
        {p.init.mode === "forward" ? "Forward" : p.init.mode === "reply_all" ? `Reply all to ${to}` : `Reply to ${to}`}
      </p>
      <ComposeForm {...p} variant="inline" />
    </section>
  );
}
