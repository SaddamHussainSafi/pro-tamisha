import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { addrText, frameHeight, mail, mailDocument, senderOf, whenFull, whenShort, type ComposeMode, type MailAccount, type MailAttachment, type MailLink, type MailMessage, type MailRow, type Outgoing } from "../../lib/mail";
import { Icon, type IconName } from "../../ui/Icon";
import { ErrorState, LoadingRows } from "../../ui/primitives";
import { AttachmentCard, AttachmentPreview } from "./Attachments";
import { Avatar, Star } from "./Bits";
import { InlineCompose, type ComposeInit, type SigChoice } from "./Compose";
import { LinksPanel } from "./LinksPanel";
import "./email.css";

const iconBtn = "grid size-9 shrink-0 cursor-pointer place-items-center rounded-full text-ink-muted transition-colors duration-150 hover:bg-subtle hover:text-ink disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent";

export function ToolButton({ icon, label, onClick, disabled, kbd, pressed, children }: { icon?: IconName; label: string; onClick: () => void; disabled?: boolean; kbd?: string; pressed?: boolean; children?: React.ReactNode }) {
  return (
    <button type="button" className={iconBtn} aria-label={label} title={kbd ? `${label} (${kbd})` : label} onClick={onClick} disabled={disabled} aria-pressed={pressed} aria-keyshortcuts={kbd}>
      {children ?? (icon && <Icon name={icon} size={18} />)}
    </button>
  );
}

type Nav = { index: number; total: number | null; hasPrev: boolean; hasNext: boolean; onPrev: () => void; onNext: () => void };

/**
 * One conversation, full width: every email of the thread as a card (the opened one and the
 * latest expanded, earlier ones folded to one line), reply / forward right underneath, linked
 * ERP records. Bodies are sandboxed frames: no scripts, no same-origin access, no network unless
 * the person asks for pictures.
 */
export function Conversation({
  id,
  account,
  accounts,
  folderName,
  nav,
  split,
  onBack,
  onRead,
  onUnread,
  onStar,
  onSent,
  replyRequest,
  composeExtras,
}: {
  id: string;
  account: MailAccount;
  accounts: MailAccount[];
  folderName: (folderId: string, role: string) => string;
  nav: Nav | null;
  split: boolean;
  onBack: () => void;
  onRead: (m: MailMessage) => void;
  onUnread: (id: string) => void;
  /** Star / unstar on the server; resolves false when it failed (the card flips back). */
  onStar: (id: string, starred: boolean) => Promise<boolean>;
  onSent: (o: Outgoing) => void;
  /** r / a / f pressed in the page: open that reply here. */
  replyRequest: { mode: ComposeMode; n: number } | null;
  /** Passed to the inline reply: address suggestions and my signature. */
  composeExtras?: { suggestions?: { name: string; email: string }[]; signatureFor?: (id: string) => SigChoice; onManageSignature?: () => void };
}) {
  const [msgs, setMsgs] = useState<Record<string, MailMessage>>({});
  const [failed, setFailed] = useState<unknown>(null);
  const [open, setOpen] = useState<Set<string>>(new Set([id]));
  const [images, setImages] = useState<Set<string>>(new Set());
  const [preview, setPreview] = useState<MailAttachment | null>(null);
  const [reply, setReply] = useState<ComposeInit | null>(null);
  const [printing, setPrinting] = useState<MailMessage | null>(null);
  const main = msgs[id];

  const load = useCallback(
    (mid: string, withImages = false) =>
      mail.message(mid, withImages).then(
        (m) => {
          setMsgs((x) => ({ ...x, [m.id]: m }));
          return m;
        },
        (err: unknown) => {
          if (mid === id) setFailed(err);
          throw err;
        },
      ),
    [id],
  );

  useEffect(() => {
    setFailed(null);
    setReply(null);
    load(id).then(
      (m) => {
        const last = m.thread[m.thread.length - 1];
        setOpen(new Set([m.id, ...(last ? [last.id] : [])]));
        if (m.unread) onRead(m);
      },
      () => undefined,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, load]);
  // Cards that are expanded need their body.
  useEffect(() => {
    for (const mid of open) if (!msgs[mid]) void load(mid).catch(() => undefined);
  }, [open, msgs, load]);
  useEffect(() => {
    if (!replyRequest || !main) return;
    const latest = msgs[main.thread[main.thread.length - 1]?.id ?? main.id] ?? main;
    setReply({ mode: replyRequest.mode, source: latest });
  }, [replyRequest]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!printing) return;
    const done = () => setPrinting(null);
    window.addEventListener("afterprint", done);
    return () => window.removeEventListener("afterprint", done);
  }, [printing]);

  if (failed && !main)
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <div className="flex items-center gap-1 border-b border-line px-3 py-1.5">
          <ToolButton icon="chevronLeft" label="Back to list" kbd="u" onClick={onBack} />
        </div>
        <ErrorState title="Couldn't open this email" error={failed} onRetry={() => void load(id).catch(() => undefined)} />
      </div>
    );
  if (!main) return <LoadingRows rows={5} cols={1} />;

  const thread: MailRow[] = main.thread.length ? main.thread : [{ ...main, thread_key: "", thread_count: 1, snippet: main.body_text.slice(0, 140), attachment_count: main.attachments.length }];
  const latest = msgs[thread[thread.length - 1]?.id ?? id] ?? main;
  const canSend = account.can_send;
  const noSend = canSend ? undefined : "This mailbox can't send: Lark has not confirmed its sign-in, or you were given read-only access.";
  const startReply = (mode: ComposeMode) => setReply({ mode, source: latest });
  const star = (m: MailMessage) => {
    const flip = (on: boolean) => setMsgs((x) => (x[m.id] ? { ...x, [m.id]: { ...(x[m.id] as MailMessage), starred: on } } : x));
    flip(!m.starred);
    void onStar(m.id, m.starred).then((ok) => ok || flip(m.starred));
  };

  return (
    <article className="flex min-h-0 flex-1 flex-col" aria-label="Email">
      <div className="sticky top-0 z-10 flex items-center gap-0.5 border-b border-line bg-card px-2 py-1.5 sm:px-3" role="toolbar" aria-label="Email actions">
        <ToolButton icon={split ? "x" : "chevronLeft"} label={split ? "Close" : "Back to list"} kbd="u" onClick={onBack} />
        <span className="mx-1 h-5 w-px bg-line" aria-hidden />
        <ToolButton icon="mailOpen" label="Mark as unread" kbd="Shift+U" onClick={() => onUnread(main.id)} />
        <ToolButton label={main.starred ? "Unstar" : "Star"} kbd="s" pressed={main.starred} onClick={() => star(main)}>
          <Star on={main.starred} size={18} />
        </ToolButton>
        <ToolButton icon="printer" label="Print" onClick={() => setPrinting(main)} />
        {nav && (
          <div className="ml-auto flex items-center gap-1 text-xs text-ink-muted">
            <span className="num hidden px-1 sm:inline">
              {nav.index + 1} of {nav.total ?? "many"}
            </span>
            <ToolButton icon="chevronLeft" label="Newer" kbd="k" onClick={nav.onPrev} disabled={!nav.hasPrev} />
            <ToolButton icon="chevron" label="Older" kbd="j" onClick={nav.onNext} disabled={!nav.hasNext} />
          </div>
        )}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-5xl space-y-1 px-3 pb-10 pt-5 sm:px-6">
          <header className="flex flex-wrap items-center gap-x-3 gap-y-1 pb-3 sm:pl-14">
            <h2 className="text-[22px] font-normal leading-snug tracking-tight">{main.subject || "(no subject)"}</h2>
            <span className="rounded-md bg-subtle px-2 py-0.5 text-xs text-ink-muted">{folderName(main.folder_id, main.folder_role)}</span>
          </header>
          {(main.removed_in_lark || main.warnings.length > 0) && (
            <div className="alert-warning mb-3 space-y-1 sm:ml-14">
              {main.removed_in_lark && <p>This email is no longer in Lark. The copy kept here is shown.</p>}
              {main.warnings.map((w) => (
                <p key={w}>{w}</p>
              ))}
            </div>
          )}
          <ol aria-label="Conversation" className="divide-y divide-line">
            {thread.map((t) => {
              const m = msgs[t.id];
              const expanded = open.has(t.id);
              return (
                <li key={t.id}>
                  {expanded && m ? (
                    <MessageCard
                      m={m}
                      me={account.address}
                      canSend={canSend}
                      noSend={noSend}
                      onCollapse={thread.length > 1 ? () => setOpen((s) => new Set([...s].filter((x) => x !== t.id))) : undefined}
                      onStar={() => star(m)}
                      onReply={() => setReply({ mode: "reply", source: m })}
                      onImages={() => {
                        setImages((s) => new Set(s).add(m.id));
                        void load(m.id, true).catch(() => undefined);
                      }}
                      imagesLoaded={images.has(m.id) && m.remote_images_loaded}
                      onPreview={setPreview}
                    />
                  ) : (
                    <button type="button" className="flex w-full cursor-pointer items-center gap-3 py-3 text-left hover:bg-subtle/70" onClick={() => setOpen((s) => new Set(s).add(t.id))} aria-expanded={false}>
                      <Avatar name={senderOf(t)} />
                      <span className={`w-40 shrink-0 truncate text-sm ${t.unread ? "font-semibold" : ""}`}>{t.from_email.toLowerCase() === account.address.toLowerCase() ? "me" : senderOf(t)}</span>
                      <span className="min-w-0 flex-1 truncate text-sm text-ink-muted">{t.snippet}</span>
                      <time className="num shrink-0 text-xs text-ink-muted" dateTime={t.received_at}>
                        {whenShort(t.received_at)}
                      </time>
                      {expanded && !m && <span className="sr-only">Loading…</span>}
                    </button>
                  )}
                </li>
              );
            })}
          </ol>
          <div className="pt-4 sm:pl-14">
            {reply ? (
              <InlineCompose
                key={`${reply.mode}-${reply.source?.id ?? ""}`}
                accounts={accounts}
                accountId={account.id}
                init={reply}
                {...composeExtras}
                onClose={() => setReply(null)}
                onSent={(o) => {
                  setReply(null);
                  onSent(o);
                }}
              />
            ) : (
              <div className="flex flex-wrap gap-2.5">
                <Pill icon="reply" label="Reply" kbd="r" onClick={() => startReply("reply")} disabled={!canSend} title={noSend} />
                <Pill icon="users" label="Reply all" kbd="a" onClick={() => startReply("reply_all")} disabled={!canSend} title={noSend} />
                <Pill icon="arrowUpRight" label="Forward" kbd="f" onClick={() => startReply("forward")} disabled={!canSend} title={noSend} />
              </div>
            )}
            {!canSend && <p className="mt-2 text-xs text-ink-muted">{noSend}</p>}
          </div>
          <div className="pt-6 sm:pl-14">
            <LinksPanel messageId={main.id} links={main.links} onChange={(links: MailLink[]) => setMsgs((x) => ({ ...x, [main.id]: { ...main, links } }))} />
          </div>
        </div>
      </div>
      <AttachmentPreview a={preview} onClose={() => setPreview(null)} />
      {printing && createPortal(<PrintCopy m={printing} />, document.body)}
    </article>
  );
}

function Pill({ icon, label, kbd, onClick, disabled, title }: { icon: IconName; label: string; kbd: string; onClick: () => void; disabled?: boolean; title?: string }) {
  return (
    <button type="button" onClick={onClick} disabled={disabled} title={title ?? `${label} (${kbd})`} aria-keyshortcuts={kbd} className="inline-flex cursor-pointer items-center gap-2 rounded-full border border-line bg-card px-5 py-2 text-sm font-medium text-ink transition-colors duration-150 hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50">
      <Icon name={icon} size={16} />
      {label}
    </button>
  );
}

function MessageCard({
  m,
  me,
  canSend,
  noSend,
  onCollapse,
  onStar,
  onReply,
  onImages,
  imagesLoaded,
  onPreview,
}: {
  m: MailMessage;
  me: string;
  canSend: boolean;
  noSend?: string;
  onCollapse?: () => void;
  onStar: () => void;
  onReply: () => void;
  onImages: () => void;
  imagesLoaded: boolean;
  onPreview: (a: MailAttachment) => void;
}) {
  const [details, setDetails] = useState(false);
  const mine = (a: { email: string }) => a.email.toLowerCase() === me.toLowerCase();
  const toLine = [...m.to, ...m.cc].map((a) => (mine(a) ? "me" : a.name || a.email)).join(", ") || "(Bcc only)";
  const fromMe = m.from_email.toLowerCase() === me.toLowerCase();
  return (
    <section className="py-3" aria-label={`From ${senderOf(m)}`}>
      <div className="flex items-start gap-3">
        <span className="hidden sm:block">
          <Avatar name={senderOf(m)} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <button type="button" className="cursor-pointer truncate text-left text-sm font-semibold disabled:cursor-default" onClick={onCollapse} disabled={!onCollapse} title={onCollapse ? "Fold this email" : undefined}>
              {fromMe ? `${senderOf(m)} (me)` : senderOf(m)}
            </button>
            {m.from_name && <span className="truncate text-xs text-ink-muted">&lt;{m.from_email}&gt;</span>}
          </div>
          <button type="button" className="inline-flex max-w-full cursor-pointer items-center gap-0.5 text-xs text-ink-muted hover:text-ink" aria-expanded={details} onClick={() => setDetails((d) => !d)}>
            <span className="truncate">to {toLine}</span>
            <Icon name="chevronDown" size={14} className={details ? "rotate-180" : ""} />
            <span className="sr-only"> (details)</span>
          </button>
          {details && (
            <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 rounded-xl border border-line px-3 py-2 text-xs">
              <dt className="text-ink-muted">from</dt>
              <dd className="break-words">{addrText({ name: m.from_name, email: m.from_email })}</dd>
              {m.to.length > 0 && (
                <>
                  <dt className="text-ink-muted">to</dt>
                  <dd className="break-words">{m.to.map(addrText).join(", ")}</dd>
                </>
              )}
              {m.cc.length > 0 && (
                <>
                  <dt className="text-ink-muted">cc</dt>
                  <dd className="break-words">{m.cc.map(addrText).join(", ")}</dd>
                </>
              )}
              {m.bcc.length > 0 && (
                <>
                  <dt className="text-ink-muted">bcc</dt>
                  <dd className="break-words">{m.bcc.map(addrText).join(", ")}</dd>
                </>
              )}
              {m.reply_to.length > 0 && (
                <>
                  <dt className="text-ink-muted">reply-to</dt>
                  <dd className="break-words">{m.reply_to.map(addrText).join(", ")}</dd>
                </>
              )}
              <dt className="text-ink-muted">date</dt>
              <dd>{whenFull(m.sent_at ?? m.received_at)}</dd>
            </dl>
          )}
        </div>
        <time className="num shrink-0 pt-0.5 text-xs text-ink-muted" dateTime={m.received_at} title={whenFull(m.received_at)}>
          <span className="sm:hidden">{whenShort(m.received_at)}</span>
          <span className="hidden sm:inline">{whenFull(m.received_at)}</span>
        </time>
        <button type="button" className={iconBtn} aria-pressed={m.starred} aria-label={m.starred ? "Starred" : "Star"} title={m.starred ? "Unstar" : "Star"} onClick={onStar}>
          <Star on={m.starred} size={17} />
        </button>
        <button type="button" className={iconBtn} aria-label={`Reply to ${senderOf(m)}`} title={noSend ?? "Reply"} onClick={onReply} disabled={!canSend}>
          <Icon name="reply" size={17} />
        </button>
      </div>
      <div className="mt-3 space-y-3 sm:pl-14">
        {m.has_remote_images && !imagesLoaded && !m.remote_images_loaded && (
          <div className="flex flex-wrap items-center gap-3 rounded-xl bg-subtle px-4 py-2 text-sm" role="note">
            <Icon name="eye" size={16} className="text-ink-muted" />
            <span className="min-w-0 flex-1">Pictures from the internet are hidden. Loading them can tell the sender you opened this email.</span>
            <button type="button" className="cursor-pointer rounded-full px-3 py-1 text-sm font-medium text-brand-primary-hover hover:bg-card" onClick={onImages}>
              Load images
            </button>
          </div>
        )}
        {m.body_html ? (
          <div className="resize-y overflow-hidden" style={{ height: frameHeight(m.body_html, m.body_text) }}>
            {/* Sandboxed: no scripts, no same-origin access; only links may open a new tab. */}
            <iframe title="Email message" sandbox="allow-popups allow-popups-to-escape-sandbox" referrerPolicy="no-referrer" srcDoc={mailDocument(m.body_html, m.remote_images_loaded)} className="block h-full w-full border-0 bg-card" />
          </div>
        ) : m.body_text ? (
          <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed">{m.body_text}</pre>
        ) : (
          <p className="text-sm text-ink-muted">This email has no text.</p>
        )}
        {m.body_truncated && <p className="text-xs text-ink-muted">Long message: only the first part is kept here.</p>}
        {m.attachments.length > 0 && (
          <section aria-label="Attachments" className="space-y-2 border-t border-line pt-3">
            <h3 className="text-sm font-medium">
              {m.attachments.length} {m.attachments.length === 1 ? "attachment" : "attachments"}
            </h3>
            <ul className="flex flex-wrap gap-3">
              {m.attachments.map((a) => (
                <AttachmentCard key={a.id} a={a} onPreview={onPreview} />
              ))}
            </ul>
          </section>
        )}
      </div>
    </section>
  );
}

function PrintCopy({ m }: { m: MailMessage }) {
  useEffect(() => {
    if (m.body_html) return;
    const t = setTimeout(() => window.print(), 50);
    return () => clearTimeout(t);
  }, [m.body_html]);
  return (
    <div className="mail-print">
      <h1 className="pb-2 text-xl font-semibold">{m.subject || "(no subject)"}</h1>
      <p className="text-sm">
        <strong>{senderOf(m)}</strong> &lt;{m.from_email}&gt; · {whenFull(m.received_at)}
      </p>
      <p className="pb-4 text-xs">To: {m.to.map(addrText).join(", ")}{m.cc.length ? ` · Cc: ${m.cc.map(addrText).join(", ")}` : ""}</p>
      {m.body_html ? (
        <iframe title="Email for printing" sandbox="" srcDoc={mailDocument(m.body_html, m.remote_images_loaded)} className="mail-print-body" onLoad={() => setTimeout(() => window.print(), 50)} />
      ) : (
        <pre className="whitespace-pre-wrap font-sans text-sm">{m.body_text}</pre>
      )}
      {m.attachments.length > 0 && <p className="pt-4 text-xs">Attachments: {m.attachments.map((a) => a.filename).join(", ")}</p>}
    </div>
  );
}

