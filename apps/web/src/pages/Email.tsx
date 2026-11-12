import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { has } from "../api";
import { useAuth } from "../auth";
import { AttachmentChip, AttachmentPreview } from "../components/mail/Attachments";
import { Avatar, Star } from "../components/mail/Bits";
import { ComposeWindow, type ComposeInit, type SigChoice } from "../components/mail/Compose";
import { SignatureSettings } from "../components/mail/SignatureSettings";
import { signatures, type SignatureOut } from "../lib/signature";
import { Conversation, ToolButton } from "../components/mail/Conversation";
import { Menu } from "../components/mail/Menu";
import { OutboxDialog } from "../components/mail/Outbox";
import { Rail } from "../components/mail/Rail";
import { Modal } from "../components/gmail/PagePreview";
import {
  addrText,
  highlightParts,
  loadPrefs,
  mail,
  mailboxState,
  mailError,
  pool,
  savePrefs,
  senderOf,
  whenFull,
  whenShort,
  words,
  type ComposeMode,
  type MailAccount,
  type MailAttachment,
  type MailDraft,
  type MailFolder,
  type MailMessage,
  type MailPrefs,
  type MailRow,
  type Outgoing,
  type SearchFilters,
} from "../lib/mail";
import { useDebounced } from "../lib/useDebounced";
import { Icon, type IconName } from "../ui/Icon";
import { EmptyState, ErrorState, PageHeader, PageSkeleton } from "../ui/primitives";

const PAGE = 50;
const NO_FILTERS: SearchFilters = { from: "", to: "", folder: "", date_from: "", date_to: "", has_attachments: false };
const refreshUnread = () => window.dispatchEvent(new Event("pt:mail-unread"));
type Tab = "all" | "unread" | "starred" | "attachments";
const TABS: { key: Tab; label: string; icon: IconName | "star" }[] = [
  { key: "all", label: "All", icon: "inbox" },
  { key: "unread", label: "Unread", icon: "mail" },
  { key: "starred", label: "Starred", icon: "star" },
  { key: "attachments", label: "Has attachments", icon: "paperclip" },
];

/**
 * Email (Lark Mail): ONE mailbox at a time, chosen from the mailboxes a Super Admin gave me.
 * There is no combined inbox, and nothing here deletes or moves an email: read and star are
 * kept in Pro Trading for me only; Lark is changed only by sending what I composed.
 */
export function EmailPage() {
  const { session } = useAuth();
  // State lives in the query (?account=…&folder=…), so switching folders never reloads the
  // screen. ⌘K search hits arrive as ?account=<id>&message=<id>.
  const [params, setParams] = useSearchParams();
  const [accounts, setAccounts] = useState<MailAccount[] | null>(null);
  const [failed, setFailed] = useState<unknown>(null);
  const [deep, setDeep] = useState<{ message: string; n: number } | null>(null);
  const allowed = !!session && has(session, "mail.use");

  const load = useCallback(() => {
    mail.accounts().then(
      (a) => {
        setAccounts(a);
        setFailed(null);
      },
      (err: unknown) => setFailed(err),
    );
  }, []);
  useEffect(() => {
    if (!allowed) return;
    load();
    window.addEventListener("pt:mail-unread", load);
    return () => window.removeEventListener("pt:mail-unread", load);
  }, [allowed, load]);

  const wanted = params.get("account");
  const account = accounts?.find((a) => a.id === wanted) ?? accounts?.[0];
  const folderKey = params.get("folder") || "inbox";
  const linked = params.get("message");
  useEffect(() => {
    // Take the message from the link once, then drop it from the address.
    if (!linked) return;
    setDeep((d) => ({ message: linked, n: (d?.n ?? 0) + 1 }));
    setParams(
      (p) => {
        const next = new URLSearchParams(p);
        next.delete("message");
        return next;
      },
      { replace: true },
    );
  }, [linked, setParams]);

  if (!session) return null;
  if (!allowed)
    return (
      <div className="space-y-5">
        <PageHeader title="Email" />
        <section className="card">
          <EmptyState icon="mail" title="Your account can't open Email" hint="Ask a Super Admin if you need a company mailbox here." />
        </section>
      </div>
    );
  if (failed && !accounts) return <ErrorState title="Couldn't load your mailboxes" error={failed} onRetry={load} />;
  if (!accounts) return <PageSkeleton label="Loading mailboxes" />;
  if (!account)
    return (
      <div className="space-y-5">
        <PageHeader title="Email" />
        <section className="card">
          <EmptyState icon="mail" title="No mailbox has been given to you yet" hint="A Super Admin sets this up in Settings > Email Services." />
        </section>
      </div>
    );

  return (
    <Mailbox
      key={account.id}
      account={account}
      accounts={accounts}
      folderKey={folderKey}
      linked={deep}
      onAccount={(id) => setParams({ account: id })}
      onFolder={(f) => setParams(f === "inbox" ? { account: account.id } : { account: account.id, folder: f })}
    />
  );
}

/* ================================================================== one mailbox */

type ListState = { rows: MailRow[] | null; next: string | null; error: unknown; loading: boolean };

function Mailbox({
  account,
  accounts,
  folderKey,
  linked,
  onAccount,
  onFolder,
}: {
  account: MailAccount;
  accounts: MailAccount[];
  folderKey: string;
  linked: { message: string; n: number } | null;
  onAccount: (id: string) => void;
  onFolder: (key: string) => void;
}) {
  const accId = account.id;
  const uid = useId();
  const { session } = useAuth();
  const [sigOpen, setSigOpen] = useState(false);
  const [mySig, setMySig] = useState<SignatureOut | null>(null);
  const loadSig = useCallback(() => {
    signatures.get(accId).then(setMySig, () => setMySig(null));
  }, [accId]);
  useEffect(loadSig, [loadSig]);
  const signatureFor = useCallback(
    (id: string): SigChoice =>
      id === accId && mySig ? { html: mySig.html || mySig.mailbox_default_html, onNew: mySig.use_on_new, onReply: mySig.use_on_reply } : { html: accounts.find((a) => a.id === id)?.signature_html ?? "", onNew: true, onReply: true },
    [accId, mySig, accounts],
  );
  const [prefs, setPrefsState] = useState<MailPrefs>(loadPrefs);
  const setPrefs = (p: Partial<MailPrefs>) =>
    setPrefsState((x) => {
      const n = { ...x, ...p };
      savePrefs(n);
      return n;
    });
  const [folders, setFolders] = useState<MailFolder[] | null>(null);
  const [tab, setTab] = useState<Tab>("all");
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 250);
  const [filters, setFilters] = useState<SearchFilters>(NO_FILTERS);
  const [advanced, setAdvanced] = useState(false);
  const [cursors, setCursors] = useState<(string | null)[]>([null]);
  const [list, setList] = useState<ListState>({ rows: null, next: null, error: null, loading: true });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [focusId, setFocusId] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(linked?.message ?? null);
  const [drafts, setDrafts] = useState<MailDraft[]>([]);
  const [composes, setComposes] = useState<{ key: number; init: ComposeInit }[]>([]);
  const [replyReq, setReplyReq] = useState<{ mode: ComposeMode; n: number } | null>(null);
  const [outgoing, setOutgoing] = useState<Outgoing[]>([]);
  const [outbox, setOutbox] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const [drawer, setDrawer] = useState(false);
  const [help, setHelp] = useState(false);
  const [preview, setPreview] = useState<MailAttachment | null>(null);
  const composeKey = useRef(0);
  const reqTok = useRef(0);

  useEffect(() => {
    if (!linked) return;
    setOpenId(linked.message);
  }, [linked]);

  const searching = dq !== "" || Object.entries(filters).some(([, v]) => v !== "" && v !== false);
  const folder = folders?.find((f) => (folderKey === "inbox" ? f.role === "INBOX" : f.id === folderKey)) ?? null;
  const isDrafts = folder?.role === "DRAFTS";
  const isSentFolder = folder?.role === "SENT";
  const st = mailboxState(account);
  const split = prefs.pane === "right";
  const cursor = cursors[cursors.length - 1] ?? null;
  const pageIdx = cursors.length - 1;
  const pageSize = searching ? 30 : PAGE; // the search endpoint answers 30 at a time

  const loadFolders = useCallback(() => {
    mail.folders(accId).then(setFolders, () => setFolders((f) => f ?? []));
  }, [accId]);
  useEffect(loadFolders, [loadFolders]);
  const loadDrafts = useCallback(() => {
    mail.drafts(accId).then(setDrafts, () => setDrafts([]));
  }, [accId]);
  useEffect(loadDrafts, [loadDrafts]);
  const loadOutgoing = useCallback(() => {
    mail.outgoing(accId).then(setOutgoing, () => undefined);
  }, [accId]);
  const [watchUntil, setWatchUntil] = useState(0);
  const busy = outgoing.some((o) => o.status === "QUEUED" || o.status === "SENDING");
  useEffect(() => {
    loadOutgoing();
    // After a send: check every 5 s for up to 2 minutes (Lark's Sent copy arrives by sync).
    const fast = busy || Date.now() < watchUntil;
    const t = setInterval(() => {
      loadOutgoing();
      if (Date.now() < watchUntil) setTick((n) => n + 1);
    }, fast ? 5000 : 60_000);
    return () => clearInterval(t);
  }, [loadOutgoing, busy, watchUntil]);

  // Whatever changes the list's question starts again at its first page.
  const question = JSON.stringify({ folderKey, tab, searching, dq, filters });
  useEffect(() => {
    setCursors([null]);
    setSelected(new Set());
  }, [question]);

  const sig = JSON.stringify({ accId, folderKey, tab, searching, dq, filters, cursor });
  const loadList = useCallback(async (quiet = false) => {
    const s = JSON.parse(sig) as { accId: string; folderKey: string; tab: Tab; searching: boolean; dq: string; filters: SearchFilters; cursor: string | null };
    const tok = ++reqTok.current;
    if (!quiet) setList((p) => ({ ...p, loading: true }));
    try {
      const page = s.searching
        ? await mail.search(s.accId, s.dq, s.filters, s.cursor)
        : await mail.messages(s.accId, { folder: s.folderKey, cursor: s.cursor, limit: PAGE, unread: s.tab === "unread", starred: s.tab === "starred", has_attachments: s.tab === "attachments" });
      if (tok !== reqTok.current) return;
      setList({ rows: page.items, next: page.next_cursor, error: null, loading: false });
    } catch (err) {
      if (tok === reqTok.current) setList((p) => ({ ...p, error: err, loading: false }));
    }
  }, [sig]);
  useEffect(() => {
    void loadList();
  }, [loadList]);
  // While a send is being watched, the Sent folder refreshes itself.
  useEffect(() => {
    if (!tick || !isSentFolder) return;
    void loadList(true);
    loadFolders();
  }, [tick]); // eslint-disable-line react-hooks/exhaustive-deps
  const refresh = () => {
    void loadList();
    loadFolders();
    loadOutgoing();
    loadDrafts();
  };

  const rows = useMemo(() => list.rows ?? [], [list.rows]);
  const patchRows = (ids: Set<string>, patch: Partial<MailRow>) => setList((p) => ({ ...p, rows: p.rows?.map((r) => (ids.has(r.id) ? { ...r, ...patch } : r)) ?? null }));
  const ws = useMemo(() => (searching ? words(dq) : []), [searching, dq]);
  const total = !searching && folder && !folder.virtual ? (tab === "all" ? folder.total : tab === "unread" ? folder.unread : null) : null;
  const start = rows.length ? pageIdx * pageSize + 1 : 0;
  const end = pageIdx * pageSize + rows.length;

  /* ---------------- actions ---------------- */
  async function star(id: string, now: boolean): Promise<boolean> {
    patchRows(new Set([id]), { starred: !now });
    try {
      await mail.setState(id, { starred: !now });
      return true;
    } catch (err) {
      patchRows(new Set([id]), { starred: now });
      setNotice(mailError(err));
      return false;
    }
  }
  async function markRead(ids: string[], read: boolean) {
    if (!ids.length) return;
    patchRows(new Set(ids), { unread: !read });
    const r = await pool(ids, 4, (id) => mail.setState(id, { read }));
    if (r.failed) setNotice(`${r.failed} of ${ids.length} could not be marked. Nothing else changed.`);
    loadFolders();
    refreshUnread();
  }
  async function bulk(kind: "read" | "unread" | "star" | "unstar") {
    const ids = [...selected];
    if (!ids.length) return;
    const r = await pool(ids, 4, (id) => mail.setState(id, kind === "read" || kind === "unread" ? { read: kind === "read" } : { starred: kind === "star" }));
    const what = { read: "marked as read", unread: "marked as unread", star: "starred", unstar: "unstarred" }[kind];
    setNotice(r.failed ? `${r.ok} ${what}; ${r.failed} could not be changed.` : `${r.ok} ${r.ok === 1 ? "email" : "emails"} ${what}.`);
    setSelected(new Set());
    refresh();
    refreshUnread();
  }
  const selectBy = (pick: (r: MailRow) => boolean) => setSelected(new Set(rows.filter(pick).map((r) => r.id)));
  const toggleSel = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });
  const open = (id: string) => {
    setFocusId(id);
    setOpenId(id);
  };
  const compose = (init: ComposeInit) => {
    if (composes.length >= 2) return setNotice("Two emails are already open. Send or close one first (closing keeps it as a draft).");
    setComposes((c) => [...c, { key: ++composeKey.current, init }]);
  };
  const sent = (o: Outgoing) => {
    setOutgoing((x) => [o, ...x.filter((y) => y.id !== o.id)]);
    setToast(o.id);
    setWatchUntil(Date.now() + 120_000);
    loadOutgoing();
    loadDrafts();
  };
  const sentKey = folders?.find((f) => f.role === "SENT")?.id ?? null;
  const goFolder = (key: string) => {
    setQ("");
    setFilters(NO_FILTERS);
    setTab("all");
    setOpenId(null);
    setDrawer(false);
    onFolder(key);
  };

  /* ---------------- keyboard ---------------- */
  const keys = useRef<(e: KeyboardEvent) => void>(() => undefined);
  keys.current = (e: KeyboardEvent) => {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
    const t = e.target as HTMLElement;
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable || document.querySelector("dialog[open]") || t.closest('[role="dialog"],[role="menu"]')) return;
    const k = e.key;
    const idx = rows.findIndex((r) => r.id === (openId ?? focusId));
    const step = (d: number) => {
      const n = rows[Math.min(rows.length - 1, Math.max(0, idx < 0 ? 0 : idx + d))];
      if (!n) return;
      if (openId) open(n.id);
      else setFocusId(n.id);
    };
    const target = openId ?? focusId;
    if (k === "j" || (k === "ArrowDown" && !openId)) step(1);
    else if (k === "k" || (k === "ArrowUp" && !openId)) step(-1);
    else if ((k === "Enter" || k === "o") && focusId && !openId && t.tagName !== "BUTTON" && t.tagName !== "A") open(focusId);
    else if (k === "u" && openId) setOpenId(null);
    else if (k === "x" && focusId && !openId) toggleSel(focusId);
    else if (k === "c") compose({ mode: "new" });
    else if ((k === "r" || k === "a" || k === "f") && openId && account.can_send) setReplyReq((p) => ({ mode: k === "r" ? "reply" : k === "a" ? "reply_all" : "forward", n: (p?.n ?? 0) + 1 }));
    else if (k === "s" && target) {
      const r = rows.find((x) => x.id === target);
      if (openId) document.querySelector<HTMLButtonElement>('[role="toolbar"][aria-label="Email actions"] [aria-keyshortcuts="s"]')?.click();
      else if (r) void star(r.id, r.starred);
    } else if (k === "I" || k === "U") {
      const ids = selected.size ? [...selected] : target ? [target] : [];
      void markRead(ids, k === "I");
      if (k === "U" && openId) setOpenId(null);
    } else if (k === "?") setHelp(true);
    else return;
    e.preventDefault();
  };
  useEffect(() => {
    const h = (e: KeyboardEvent) => keys.current(e);
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);
  useEffect(() => {
    if (focusId && !openId) document.getElementById(`mail-row-${focusId}`)?.scrollIntoView({ block: "nearest" });
  }, [focusId, openId]);

  /* ---------------- pieces ---------------- */
  const folderName = (id: string, role: string) => folders?.find((f) => f.id === id)?.name ?? (role ? role[0] + role.slice(1).toLowerCase() : "");
  const allSel = rows.length > 0 && rows.every((r) => selected.has(r.id));
  const someSel = selected.size > 0;
  const openIdx = openId ? rows.findIndex((r) => r.id === openId) : -1;
  const prevRow = rows[openIdx - 1];
  const nextRow = rows[openIdx + 1];
  const nav =
    openIdx >= 0
      ? {
          index: pageIdx * pageSize + openIdx,
          total: total ?? (list.next ? null : pageIdx * pageSize + rows.length),
          hasPrev: !!prevRow,
          hasNext: !!nextRow,
          onPrev: () => prevRow && open(prevRow.id),
          onNext: () => nextRow && open(nextRow.id),
        }
      : null;
  const problems = outgoing.filter((o) => o.status === "FAILED" || o.status === "UNCERTAIN").length;
  // Sent: what Lark accepted (or is being handed to Lark) shows at once, until Lark's own copy
  // arrives in the Sent folder. FAILED / UNCERTAIN are never shown as sent.
  const pending = isSentFolder && !searching && tab === "all" && pageIdx === 0 ? outgoing.filter((o) => (o.status === "QUEUED" || o.status === "SENDING" || (o.status === "SENT" && Date.now() - new Date(o.sent_at ?? o.created_at).getTime() < 30 * 60_000)) && !o.seen_in_sent) : [];
  const suggestions = useMemo(() => {
    const seen = new Map<string, { name: string; email: string }>();
    for (const r of rows) for (const a of [{ name: r.from_name, email: r.from_email }, ...r.to]) if (a.email && a.email.toLowerCase() !== account.address.toLowerCase() && !seen.has(a.email.toLowerCase())) seen.set(a.email.toLowerCase(), a);
    return [...seen.values()].slice(0, 200);
  }, [rows, account.address]);
  const listTitle = searching ? "Search results" : (folder?.name ?? (folderKey === "starred" ? "Starred" : "Inbox"));

  const rail = (compact: boolean) => (
    <Rail
      compact={compact}
      account={account}
      accounts={accounts}
      folders={folders}
      folderKey={searching ? "" : folder ? (folder.role === "INBOX" ? "inbox" : folder.id) : folderKey}
      drafts={drafts.length}
      problems={problems}
      onAccount={onAccount}
      onFolder={goFolder}
      onCompose={() => compose({ mode: "new" })}
      onProblems={() => setOutbox(true)}
      onHelp={() => setHelp(true)}
      onSignature={() => setSigOpen(true)}
    />
  );

  const toolbar = (
    <div className="sticky top-0 z-10 flex h-12 shrink-0 items-center gap-0.5 border-b border-line bg-card px-2 sm:px-3" role="toolbar" aria-label="List actions">
      <label className="grid size-9 cursor-pointer place-items-center rounded-full hover:bg-subtle" title="Select all on this page">
        <input
          type="checkbox"
          className="size-4 cursor-pointer accent-brand-primary"
          aria-label="Select all on this page"
          checked={allSel}
          ref={(el) => {
            if (el) el.indeterminate = someSel && !allSel;
          }}
          onChange={() => (allSel || someSel ? setSelected(new Set()) : selectBy(() => true))}
        />
      </label>
      <Menu
        label="Select"
        button={<Icon name="chevronDown" size={14} />}
        items={[
          { label: "All", onSelect: () => selectBy(() => true) },
          { label: "None", onSelect: () => setSelected(new Set()) },
          { label: "Read", onSelect: () => selectBy((r) => !r.unread) },
          { label: "Unread", onSelect: () => selectBy((r) => r.unread) },
          { label: "Starred", onSelect: () => selectBy((r) => r.starred) },
          { label: "Unstarred", onSelect: () => selectBy((r) => !r.starred) },
        ]}
      />
      {someSel ? (
        <>
          <span className="mx-1 h-5 w-px bg-line" aria-hidden />
          <ToolButton icon="mailOpen" label="Mark as read" kbd="Shift+I" onClick={() => void bulk("read")} />
          <ToolButton icon="mail" label="Mark as unread" kbd="Shift+U" onClick={() => void bulk("unread")} />
          <ToolButton label="Star" onClick={() => void bulk("star")}>
            <Star on size={18} />
          </ToolButton>
          <ToolButton label="Unstar" onClick={() => void bulk("unstar")}>
            <Star on={false} size={18} />
          </ToolButton>
          <span className="num ml-1 hidden text-xs text-ink-muted sm:inline">{selected.size} selected</span>
        </>
      ) : (
        <>
          <ToolButton icon="refresh" label="Refresh" onClick={refresh} />
          <Menu
            label="More options"
            button={<Icon name="more" size={18} />}
            items={[
              { heading: "Reading pane" },
              { label: "No split", checked: !split, onSelect: () => setPrefs({ pane: "none" }) },
              { label: "Right of inbox", checked: split, onSelect: () => setPrefs({ pane: "right" }) },
              "divider",
              { heading: "Density" },
              { label: "Comfortable", checked: prefs.density === "comfortable", onSelect: () => setPrefs({ density: "comfortable" }) },
              { label: "Compact", checked: prefs.density === "compact", onSelect: () => setPrefs({ density: "compact" }) },
              "divider",
              { label: "Signature…", onSelect: () => setSigOpen(true) },
              { label: "Keyboard shortcuts", hint: "?", onSelect: () => setHelp(true) },
            ]}
          />
        </>
      )}
      <div className="ml-auto flex items-center gap-0.5 text-xs text-ink-muted">
        <span className="num px-2" aria-live="polite">
          {list.rows === null ? "" : rows.length === 0 ? "0" : `${start}–${end}${total !== null ? ` of ${total}` : list.next ? " of many" : ` of ${end}`}`}
        </span>
        <ToolButton icon="chevronLeft" label="Newer" onClick={() => setCursors((c) => (c.length > 1 ? c.slice(0, -1) : c))} disabled={cursors.length <= 1} />
        <ToolButton icon="chevron" label="Older" onClick={() => list.next && setCursors((c) => [...c, list.next])} disabled={!list.next} />
      </div>
    </div>
  );

  const tabs = !searching && (
    <div role="tablist" aria-label="Show" className="flex shrink-0 overflow-x-auto border-b border-line">
      {TABS.map((t) => (
        <button
          key={t.key}
          type="button"
          role="tab"
          aria-selected={tab === t.key}
          onClick={() => setTab(t.key)}
          className={`flex min-w-fit cursor-pointer items-center gap-2.5 border-b-[3px] px-4 py-3 text-sm transition-colors duration-150 lg:min-w-44 sm:px-5 ${tab === t.key ? "border-brand-primary font-semibold text-brand-primary-hover" : "border-transparent text-ink-muted hover:bg-subtle"}`}
        >
          {t.icon === "star" ? <Star on={tab === t.key} size={17} /> : <Icon name={t.icon} size={17} />}
          {t.label}
          {t.key === "unread" && folder && folder.unread > 0 && !folder.virtual && <span className="num rounded-full bg-brand-primary px-1.5 text-[11px] font-semibold leading-4 text-white">{folder.unread}</span>}
        </button>
      ))}
    </div>
  );

  const listBody = (
    <div className="min-h-0 flex-1 overflow-y-auto" id={`${uid}-list`}>
      {isDrafts && !searching && tab === "all" && drafts.length > 0 && (
        <section aria-label="Drafts saved in Pro Trading" className="border-b border-line">
          <p className="bg-subtle px-4 py-1.5 text-xs font-medium text-ink-muted">Saved in Pro Trading</p>
          <ul>
            {drafts.map((d) => (
              <li key={d.id} className="border-b border-line/70 last:border-b-0">
                <button type="button" className="flex w-full cursor-pointer items-center gap-3 px-4 py-2.5 text-left text-sm hover:bg-subtle" onClick={() => compose({ mode: d.mode, draft: d })}>
                  <span className="w-44 shrink-0 truncate font-medium text-danger">Draft</span>
                  <span className="min-w-0 flex-1 truncate">
                    {d.subject || "(no subject)"} <span className="text-ink-muted">- {d.to.map(addrText).join(", ") || "no recipients yet"}</span>
                  </span>
                  <time className="num shrink-0 text-xs text-ink-muted" dateTime={d.updated_at}>
                    {whenShort(d.updated_at)}
                  </time>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {list.error && !list.rows ? (
        <ErrorState title="Couldn't load the emails" error={list.error} onRetry={() => void loadList()} />
      ) : list.rows === null ? (
        <SkeletonRows dense={prefs.density === "compact"} />
      ) : rows.length === 0 ? (
        account.status === "NOT_CONFIGURED" && !account.last_sync_at && !searching ? (
          <EmptyState icon="mail" title="Not connected yet" hint="A Super Admin enters the Lark app password in Settings > Email Services. Mail appears here after the first sync." />
        ) : (
          <EmptyState
            icon={searching ? "search" : "inbox"}
            title={searching ? "No emails match" : isDrafts && drafts.length ? "No drafts in Lark" : tab === "unread" ? "No unread emails" : tab === "starred" ? "No starred emails" : tab === "attachments" ? "No emails with attachments" : "No emails here"}
            hint={searching ? `Searched ${account.label} only. Try other words or fewer filters.` : !account.history_complete && account.last_sync_at ? "History still importing… older mail appears as it arrives." : undefined}
          />
        )
      ) : (
        <ul aria-label="Email list" aria-busy={list.loading || undefined} className={list.loading ? "opacity-60 transition-opacity" : ""}>
          {pending.map((o) => (
            <li key={o.id} data-pending className="flex items-center gap-3 border-b border-line/70 bg-card py-2 pl-[4.4rem] pr-4 text-[14px] max-md:pl-4" title="Shown until Lark's own copy arrives in Sent">
              <span className="w-[11rem] shrink-0 truncate lg:w-[13rem]">To: {o.to.map((a) => a.name || a.email).join(", ") || "(Bcc only)"}</span>
              <span className="min-w-0 flex-1 truncate">
                <span className="mr-2 rounded bg-subtle px-1.5 py-0.5 text-[11px] font-medium text-ink-muted">{o.status === "SENT" ? "Sent" : "Sending…"}</span>
                {o.subject || "(no subject)"}
              </span>
              <time className="num w-16 shrink-0 text-right text-xs text-ink-muted" dateTime={o.sent_at ?? o.created_at}>
                {whenShort(o.sent_at ?? o.created_at)}
              </time>
            </li>
          ))}
          {rows.map((r) => (
            <Row
              key={r.id}
              r={r}
              me={account.address}
              open={r.id === openId}
              focused={r.id === focusId}
              checked={selected.has(r.id)}
              stacked={split && !!openId}
              dense={prefs.density === "compact"}
              ws={ws}
              onOpen={() => open(r.id)}
              onCheck={() => toggleSel(r.id)}
              onStar={() => void star(r.id, r.starred)}
              onRead={() => void markRead([r.id], r.unread)}
              onPreview={setPreview}
            />
          ))}
        </ul>
      )}
    </div>
  );

  const conversation = openId && (
    <Conversation
      key={openId}
      id={openId}
      account={account}
      accounts={accounts}
      folderName={folderName}
      nav={nav}
      split={split}
      replyRequest={replyReq}
      composeExtras={{ suggestions, signatureFor, onManageSignature: () => setSigOpen(true) }}
      onBack={() => setOpenId(null)}
      onRead={(m: MailMessage) => {
        void mail.setState(m.id, { read: true }).then(() => {
          patchRows(new Set([m.id]), { unread: false });
          setFolders((fs) => fs?.map((f) => (f.id === m.folder_id && f.unread > 0 ? { ...f, unread: f.unread - 1 } : f)) ?? null);
          refreshUnread();
        }, () => undefined);
      }}
      onUnread={(id) => {
        void markRead([id], false);
        setOpenId(null);
      }}
      onStar={star}
      onSent={sent}
    />
  );

  return (
    <div className="-mx-1 flex flex-col gap-3 md:h-[calc(100dvh-7.25rem)] md:min-h-[32rem] md:flex-row md:gap-2">
      <h1 className="sr-only">Email</h1>
      {/* rail: full on wide screens, icons only on tablets, a drawer on phones */}
      <aside aria-label="Mailbox and folders" className="hidden shrink-0 xl:block xl:w-60">
        {rail(false)}
      </aside>
      <aside aria-label="Mailbox and folders" className="hidden shrink-0 md:block md:w-[4.5rem] xl:hidden">
        {rail(true)}
      </aside>
      {drawer && (
        <>
          <div className="fixed inset-0 z-30 animate-fade bg-ink/40" aria-hidden onClick={() => setDrawer(false)} />
          <aside aria-label="Mailbox and folders" className="fixed inset-y-0 left-0 z-40 w-72 max-w-[85vw] animate-drawer overflow-y-auto bg-canvas p-3">
            {rail(false)}
          </aside>
        </>
      )}

      <div className="flex min-h-0 min-w-0 flex-1 flex-col gap-2">
        {/* search */}
        <div className="relative flex items-center gap-2">
          <button type="button" className="icon-btn size-11 shrink-0 border-transparent bg-transparent md:hidden" aria-label="Folders and mailboxes" aria-expanded={drawer} onClick={() => setDrawer(true)}>
            <Icon name="inbox" size={20} />
          </button>
          <label className="flex h-12 min-w-0 flex-1 items-center gap-2 rounded-full bg-card pl-4 pr-1.5 shadow-[0_1px_2px_rgb(24_24_27/0.06)] focus-within:shadow-[0_1px_6px_rgb(24_24_27/0.16)]">
            <Icon name="search" size={19} className="shrink-0 text-ink-muted" />
            <span className="sr-only">Search {account.label}</span>
            <input
              data-page-search
              type="search"
              aria-keyshortcuts="/"
              aria-controls={openId && !split ? undefined : `${uid}-list`}
              className="min-w-0 flex-1 bg-transparent py-2 text-[15px] outline-none placeholder:text-ink-muted [&::-webkit-search-cancel-button]:hidden"
              placeholder={`Search ${account.label.replace(/\s*\(synthetic\)/i, "")}`}
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setOpenId(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                  e.preventDefault();
                  const i = rows.findIndex((r) => r.id === focusId);
                  const n = rows[Math.min(rows.length - 1, Math.max(0, i + (e.key === "ArrowDown" ? 1 : -1)))];
                  if (n) setFocusId(n.id);
                } else if (e.key === "Enter" && focusId) {
                  e.preventDefault();
                  open(focusId);
                } else if (e.key === "Escape" && q) {
                  e.preventDefault();
                  setQ("");
                }
              }}
            />
            {(q || searching) && (
              <button
                type="button"
                className="grid size-9 cursor-pointer place-items-center rounded-full text-ink-muted hover:bg-subtle"
                aria-label="Clear search"
                onClick={() => {
                  setQ("");
                  setFilters(NO_FILTERS);
                }}
              >
                <Icon name="x" size={17} />
              </button>
            )}
            <button type="button" className={`grid size-9 cursor-pointer place-items-center rounded-full hover:bg-subtle ${advanced || searching ? "text-brand-primary-hover" : "text-ink-muted"}`} aria-label="Search options" aria-expanded={advanced} title="Search options" onClick={() => setAdvanced((a) => !a)}>
              <Icon name="tune" size={18} />
            </button>
          </label>
          {advanced && (
            <Advanced
              folders={folders}
              filters={filters}
              setFilters={(f) => {
                setFilters(f);
                setOpenId(null);
              }}
              onClose={() => setAdvanced(false)}
            />
          )}
        </div>

        {(account.status !== "CONNECTED" || !account.history_complete) && (
          <p role="note" className={`flex items-start gap-2 rounded-xl px-3.5 py-2 text-xs ${account.status === "AUTH_FAILED" ? "bg-danger/8 text-danger" : account.status === "ERROR" ? "bg-warning/10 text-ink" : "bg-card text-ink-muted"}`}>
            <Icon name={account.status === "AUTH_FAILED" || account.status === "ERROR" ? "alert" : "info"} size={14} className="mt-px shrink-0" />
            <span>
              {account.status !== "CONNECTED" && st.detail}
              {/synthetic/i.test(account.label) && " This is a synthetic test mailbox imported from a local stand-in server, not a Lark connection."}
              {account.status === "CONNECTED" && !account.history_complete && "History still importing… older mail appears as it arrives."}
            </span>
          </p>
        )}
        {notice && (
          <p role="status" className="flex items-center gap-2 rounded-xl bg-ink px-4 py-2 text-sm text-white">
            <span className="min-w-0 flex-1">{notice}</span>
            <button type="button" className="grid size-7 cursor-pointer place-items-center rounded-full hover:bg-white/15" aria-label="Dismiss" onClick={() => setNotice(null)}>
              <Icon name="x" size={14} />
            </button>
          </p>
        )}

        <section aria-label={listTitle} className={`flex min-h-[70dvh] min-w-0 flex-1 overflow-hidden rounded-2xl bg-card md:min-h-0 ${split && openId ? "flex-row" : "flex-col"}`}>
          {openId && !split ? (
            conversation
          ) : (
            <>
              <div className={`flex min-h-0 min-w-0 flex-col ${split && openId ? "hidden w-[26rem] shrink-0 border-r border-line lg:flex xl:w-[30rem]" : "flex-1"}`}>
                {toolbar}
                {tabs}
                {listBody}
              </div>
              {split && openId && <div className="flex min-h-0 min-w-0 flex-1 flex-col">{conversation}</div>}
            </>
          )}
        </section>
      </div>

      {/* phones: floating compose button */}
      {!composes.length && !openId && (
        <button type="button" onClick={() => compose({ mode: "new" })} className="fixed bottom-5 right-5 z-20 inline-flex cursor-pointer items-center gap-2 rounded-2xl bg-brand-accent/25 px-5 py-4 text-sm font-semibold text-ink shadow-[0_4px_12px_rgb(24_24_27/0.18)] backdrop-blur md:hidden">
          <Icon name="pencil" size={18} />
          Compose
        </button>
      )}
      {composes.map((c, i) => (
        <ComposeWindow
          key={c.key}
          slot={composes.length - 1 - i}
          accounts={accounts}
          accountId={accId}
          suggestions={suggestions}
          signatureFor={signatureFor}
          onManageSignature={() => setSigOpen(true)}
          init={c.init}
          onClose={() => {
            setComposes((x) => x.filter((y) => y.key !== c.key));
            loadDrafts();
          }}
          onSent={(o) => {
            setComposes((x) => x.filter((y) => y.key !== c.key));
            sent(o);
          }}
        />
      ))}
      <OutboxDialog open={outbox} items={outgoing} onClose={() => setOutbox(false)} onChanged={loadOutgoing} />
      <AttachmentPreview a={preview} onClose={() => setPreview(null)} />
      {toast && (
        <SendToast
          o={outgoing.find((x) => x.id === toast) ?? null}
          onView={() => {
            setToast(null);
            if (sentKey) goFolder(sentKey);
          }}
          onDetails={() => {
            setToast(null);
            setOutbox(true);
          }}
          onClose={() => setToast(null)}
        />
      )}
      <ShortcutHelp open={help} onClose={() => setHelp(false)} />
      <SignatureSettings
        open={sigOpen}
        account={account}
        myName={session?.employee.display_name ?? ""}
        onClose={() => setSigOpen(false)}
        onSaved={() => {
          loadSig();
          refreshUnread(); // reloads the mailbox list (signature_html)
        }}
      />
    </div>
  );
}

/* ================================================================== search options */

function Advanced({ folders, filters, setFilters, onClose }: { folders: MailFolder[] | null; filters: SearchFilters; setFilters: (f: SearchFilters) => void; onClose: () => void }) {
  const [f, setF] = useState(filters);
  const id = useId();
  const field = "input py-2";
  return (
    <form
      role="group"
      aria-label="Search options"
      className="absolute left-0 right-0 top-14 z-30 grid animate-pop gap-3 rounded-2xl border border-line bg-card p-5 shadow-[0_8px_24px_rgb(24_24_27/0.16)] sm:grid-cols-2 md:left-auto md:w-[36rem]"
      onSubmit={(e) => {
        e.preventDefault();
        setFilters(f);
        onClose();
      }}
      onKeyDown={(e) => e.key === "Escape" && (e.stopPropagation(), onClose())}
    >
      <label className="text-sm">
        <span className="mb-1 block text-xs text-ink-muted">From</span>
        <input id={`${id}-f`} className={field} value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} placeholder="Name or address" autoFocus />
      </label>
      <label className="text-sm">
        <span className="mb-1 block text-xs text-ink-muted">To</span>
        <input className={field} value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} placeholder="Name or address" />
      </label>
      <label className="text-sm">
        <span className="mb-1 block text-xs text-ink-muted">From date</span>
        <input type="date" className={field} value={f.date_from} max={f.date_to || undefined} onChange={(e) => setF({ ...f, date_from: e.target.value })} />
      </label>
      <label className="text-sm">
        <span className="mb-1 block text-xs text-ink-muted">To date</span>
        <input type="date" className={field} value={f.date_to} min={f.date_from || undefined} onChange={(e) => setF({ ...f, date_to: e.target.value })} />
      </label>
      <label className="text-sm">
        <span className="mb-1 block text-xs text-ink-muted">Folder</span>
        <select className={field} value={f.folder} onChange={(e) => setF({ ...f, folder: e.target.value })}>
          <option value="">All folders</option>
          {folders?.map((x) => (
            <option key={x.id} value={x.id}>
              {x.name}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-2 self-end pb-2 text-sm">
        <input type="checkbox" className="size-4 accent-brand-primary" checked={f.has_attachments} onChange={(e) => setF({ ...f, has_attachments: e.target.checked })} />
        Has attachments
      </label>
      <div className="flex justify-end gap-2 sm:col-span-2">
        <button
          type="button"
          className="btn-ghost px-4 py-2"
          onClick={() => {
            setF(NO_FILTERS);
            setFilters(NO_FILTERS);
          }}
        >
          Clear filters
        </button>
        <button type="submit" className="btn-primary px-5 py-2">
          Search
        </button>
      </div>
    </form>
  );
}

/* ================================================================== rows */

function SkeletonRows({ dense }: { dense: boolean }) {
  return (
    <div role="status" aria-busy="true" aria-label="Loading" className="overflow-hidden">
      {Array.from({ length: 12 }, (_, i) => (
        <div key={i} className={`flex items-center gap-4 border-b border-line/70 px-4 ${dense ? "py-2.5" : "py-3.5"}`} aria-hidden>
          <div className="skeleton size-4 rounded" />
          <div className="skeleton h-3 w-32" />
          <div className={`skeleton h-3 ${["w-2/5", "w-1/2", "w-1/3"][i % 3]}`} />
          <div className="skeleton ml-auto h-3 w-12" />
        </div>
      ))}
      <span className="sr-only">Loading…</span>
    </div>
  );
}

function Row({
  r,
  me,
  open,
  focused,
  checked,
  stacked,
  dense,
  ws,
  onOpen,
  onCheck,
  onStar,
  onRead,
  onPreview,
}: {
  r: MailRow;
  me: string;
  open: boolean;
  focused: boolean;
  checked: boolean;
  stacked: boolean;
  dense: boolean;
  ws: string[];
  onOpen: () => void;
  onCheck: () => void;
  onStar: () => void;
  onRead: () => void;
  onPreview: (a: MailAttachment) => void;
}) {
  const hl = (t: string) => highlightParts(t, ws).map((p, i) => (p.hit ? <mark key={i} className="rounded-sm bg-brand-accent/30 px-0.5 text-ink">{p.text}</mark> : <span key={i}>{p.text}</span>));
  const fromMe = r.from_email.toLowerCase() === me.toLowerCase();
  const who = fromMe ? (r.to[0] ? `To: ${r.to[0].name || r.to[0].email}` : "me") : senderOf(r);
  const files = r.attachments ?? [];
  const more = Math.max(0, r.attachment_count - files.length);
  const label = `${r.unread ? "Unread, " : ""}${senderOf(r)}, ${r.subject || "(no subject)"}, ${whenFull(r.received_at)}`;
  const tint = open ? "bg-brand-accent/12" : checked ? "bg-brand-accent/10" : r.unread ? "bg-card" : "bg-subtle/70";
  const chips = files.length > 0 && (
    <div className="relative z-[1] mt-1.5 flex flex-wrap gap-1.5 [&>*]:pointer-events-auto">
      {files.map((a) => (
        <AttachmentChip key={a.id} a={a} onPreview={onPreview} />
      ))}
      {more > 0 && <span className="self-center text-xs text-ink-muted">+{more}</span>}
    </div>
  );
  const sender = (
    <>
      {hl(who)}
      {r.thread_count > 1 && <span className="num ml-1 text-xs font-normal text-ink-muted">{r.thread_count}</span>}
      {r.unread && <span className="sr-only"> (unread)</span>}
    </>
  );
  const date = (
    <time className={`num whitespace-nowrap text-xs ${r.unread ? "font-semibold text-ink" : "text-ink-muted"}`} dateTime={r.received_at} title={whenFull(r.received_at)}>
      {whenShort(r.received_at)}
    </time>
  );
  const starBtn = (
    <button type="button" className="relative z-[1] grid size-8 shrink-0 cursor-pointer place-items-center rounded-full hover:bg-ink/5" aria-pressed={r.starred} aria-label={r.starred ? `Unstar ${r.subject}` : `Star ${r.subject}`} onClick={onStar}>
      <Star on={r.starred} size={17} />
    </button>
  );
  return (
    <li
      id={`mail-row-${r.id}`}
      data-unread={r.unread || undefined}
      className={`group relative border-b border-line/70 text-[14px] leading-5 [contain-intrinsic-size:auto_3rem] [content-visibility:auto] hover:z-[2] hover:shadow-[0_1px_3px_rgb(24_24_27/0.18)] ${tint} ${focused ? "shadow-[inset_3px_0_0_var(--color-brand-primary)]" : ""}`}
    >
      {/* The whole row opens the email; the controls sit above this button. */}
      <button type="button" className="absolute inset-0 cursor-pointer focus-visible:outline-2 focus-visible:-outline-offset-2" aria-label={label} aria-current={open ? "true" : undefined} onClick={onOpen} />
      {/* phones and the narrow list next to the reading pane: avatar + three lines */}
      <div className={`pointer-events-none items-start gap-3 px-3 ${dense ? "py-2" : "py-3"} ${stacked ? "flex" : "flex md:hidden"}`}>
        {!stacked && <Avatar name={senderOf(r)} size={40} />}
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <span className={`min-w-0 flex-1 truncate ${r.unread ? "font-bold" : ""}`}>{sender}</span>
            {date}
          </div>
          <div className="flex items-start gap-2">
            <div className="min-w-0 flex-1">
              <p className={`truncate ${r.unread ? "font-semibold" : ""}`}>{hl(r.subject || "(no subject)")}</p>
              <p className="truncate text-ink-muted">{hl(r.highlight || r.snippet)}</p>
            </div>
            <span className="pointer-events-auto">{starBtn}</span>
          </div>
          <div>{chips}</div>
        </div>
      </div>
      {/* wide screens: one line like a desktop mail client */}
      <div className={`pointer-events-none items-start gap-1 pl-2 pr-4 ${dense ? "py-0" : "py-1"} ${stacked ? "hidden" : "hidden md:flex"}`}>
        <label className="pointer-events-auto relative z-[1] grid size-8 shrink-0 cursor-pointer place-items-center rounded-full hover:bg-ink/5">
          <input type="checkbox" className="size-4 cursor-pointer accent-brand-primary" checked={checked} onChange={onCheck} aria-label={`Select ${r.subject || "(no subject)"}`} />
        </label>
        <span className="pointer-events-auto">{starBtn}</span>
        <div className="min-w-0 flex-1 pl-1">
          <div className="flex h-8 items-center gap-3">
            <span className={`w-[11rem] shrink-0 truncate lg:w-[13rem] ${r.unread ? "font-bold text-ink" : "text-ink"}`}>{sender}</span>
            <span className="min-w-0 flex-1 truncate">
              <span className={r.unread ? "font-bold" : ""}>{hl(r.subject || "(no subject)")}</span>
              <span className="text-ink-muted"> - {hl(r.highlight || r.snippet)}</span>
            </span>
            <span className="w-16 shrink-0 text-right group-focus-within:invisible group-hover:invisible">{date}</span>
            {/* hover / focus actions take the date's place (kept in the layout so keyboards reach them) */}
            <span className="pointer-events-auto absolute right-3 top-1 z-[1] flex justify-end opacity-0 transition-opacity duration-100 focus-within:opacity-100 group-hover:opacity-100">
              <button type="button" className="grid size-8 cursor-pointer place-items-center rounded-full text-ink-muted hover:bg-ink/5 hover:text-ink" aria-label={r.unread ? `Mark ${r.subject} as read` : `Mark ${r.subject} as unread`} title={r.unread ? "Mark as read" : "Mark as unread"} onClick={onRead}>
                <Icon name={r.unread ? "mailOpen" : "mail"} size={17} />
              </button>
            </span>
          </div>
          <div>{chips && <div className="-mt-1 pb-1.5 pl-[calc(11rem+0.825rem)] lg:pl-[calc(13rem+0.825rem)]">{chips}</div>}</div>
        </div>
      </div>
    </li>
  );
}

/* ================================================================== keyboard help */

const SHORTCUTS: [string, string][] = [
  ["j / k", "Older / newer email"],
  ["↓ / ↑", "Move in the list"],
  ["Enter or o", "Open"],
  ["u", "Back to the list"],
  ["x", "Select the email"],
  ["s", "Star / unstar"],
  ["Shift + I", "Mark as read"],
  ["Shift + U", "Mark as unread"],
  ["r", "Reply"],
  ["a", "Reply all"],
  ["f", "Forward"],
  ["c", "Compose"],
  ["/", "Search this mailbox"],
  ["Ctrl / ⌘ Enter", "Send (while writing)"],
  ["Ctrl / ⌘ K", "Search all of Pro Trading"],
  ["?", "This list"],
];
function ShortcutHelp({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} title="Keyboard shortcuts" onClose={onClose}>
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-6 gap-y-2 p-5 text-sm">
        {SHORTCUTS.map(([k, v]) => (
          <div key={k} className="contents">
            <dt>
              <kbd className="rounded-md border border-line bg-subtle px-2 py-0.5 font-sans text-xs">{k}</kbd>
            </dt>
            <dd className="text-ink-muted">{v}</dd>
          </div>
        ))}
      </dl>
    </Modal>
  );
}

/** Bottom-left note after Send, following the real delivery state (never claims more). */
function SendToast({ o, onView, onDetails, onClose }: { o: Outgoing | null; onView: () => void; onDetails: () => void; onClose: () => void }) {
  const status = o?.status ?? "QUEUED";
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (status !== "SENT") return;
    const t = setTimeout(() => close.current(), 10_000);
    return () => clearTimeout(t);
  }, [status]);
  const text = status === "SENT" ? "Message sent" : status === "FAILED" ? "Not sent: Lark did not accept it" : status === "UNCERTAIN" ? "May have been sent — check Sent before sending again" : "Sending…";
  const bad = status === "FAILED" || status === "UNCERTAIN";
  return (
    <div role="status" className="fixed bottom-5 left-4 z-40 flex max-w-[calc(100vw-2rem)] animate-pop items-center gap-3 rounded-xl bg-ink py-2.5 pl-4 pr-2 text-sm text-white shadow-[0_8px_24px_rgb(24_24_27/0.3)] lg:left-[19rem]">
      <span>{text}</span>
      <button type="button" className="cursor-pointer rounded-md px-2 py-1 font-semibold text-apple-200 hover:bg-white/10" onClick={bad ? onDetails : onView}>
        {bad ? "Details" : "View message"}
      </button>
      <button type="button" className="grid size-7 cursor-pointer place-items-center rounded-full hover:bg-white/15" aria-label="Dismiss" onClick={onClose}>
        <Icon name="x" size={14} />
      </button>
    </div>
  );
}
