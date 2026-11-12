import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { has } from "../api";
import { useAuth } from "../auth";
import { Compose, type ComposeInit } from "../components/mail/Compose";
import { OutboxDialog, OutboxStrip } from "../components/mail/Outbox";
import { ReadingPane, Star } from "../components/mail/ReadingPane";
import {
  addrText,
  ago,
  FOLDER_ICON,
  highlightParts,
  mail,
  mailboxState,
  mailError,
  senderOf,
  whenFull,
  whenShort,
  words,
  type ComposeMode,
  type MailAccount,
  type MailDraft,
  type MailFolder,
  type MailMessage,
  type MailRow,
  type Outgoing,
  type SearchFilters,
} from "../lib/mail";
import { useDebounced } from "../lib/useDebounced";
import { Icon } from "../ui/Icon";
import { EmptyState, ErrorState, LoadingRows, PageHeader, PageSkeleton, StatusChip } from "../ui/primitives";

const NO_FILTERS: SearchFilters = { folder: "", date_from: "", date_to: "", has_attachments: false };
const refreshUnread = () => window.dispatchEvent(new Event("pt:mail-unread"));

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

  const st = mailboxState(account);
  return (
    <div className="flex flex-col gap-4 md:h-[calc(100dvh-9rem)] md:min-h-[34rem]">
      <PageHeader
        title="Email"
        subtitle={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <span className="font-medium text-ink">{account.address}</span>
            <StatusChip tone={st.tone}>{st.label}</StatusChip>
            <span className="text-sm" title={account.last_sync_at ? whenFull(account.last_sync_at) : undefined}>
              {account.last_sync_at ? `Last synced ${ago(account.last_sync_at)}` : "Never synced"}
            </span>
            {!account.history_complete && account.last_sync_at && <span className="text-sm">· History still importing…</span>}
          </span>
        }
      />
      {account.status !== "CONNECTED" && (
        <p role="note" className={account.status === "AUTH_FAILED" ? "alert-danger" : account.status === "ERROR" ? "alert-warning" : "rounded-[var(--radius-ctl)] bg-card px-4 py-3 text-sm text-ink-muted"}>
          {st.detail}
          {/synthetic/i.test(account.label) && " This is a synthetic test mailbox imported from a local stand-in server, not a Lark connection."}
        </p>
      )}
      <Mailbox
        key={account.id}
        account={account}
        accounts={accounts}
        folderKey={folderKey}
        linked={deep}
        onAccount={(id) => setParams({ account: id })}
        onFolder={(f) => setParams(f === "inbox" ? { account: account.id } : { account: account.id, folder: f })}
      />
    </div>
  );
}

/* ================================================================== one mailbox */

type ListState = { rows: MailRow[] | null; cursor: string | null; error: unknown };

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
  const [folders, setFolders] = useState<MailFolder[] | null>(null);
  const [list, setList] = useState<ListState>({ rows: null, cursor: null, error: null });
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 250);
  const [filters, setFilters] = useState<SearchFilters>(NO_FILTERS);
  const [showFilters, setShowFilters] = useState(false);
  const [openId, setOpenId] = useState<string | null>(linked?.message ?? null);
  const [sel, setSel] = useState<string | null>(linked?.message ?? null);
  useEffect(() => {
    if (!linked) return;
    setOpenId(linked.message);
    setSel(linked.message);
  }, [linked]);
  const [detail, setDetail] = useState<MailMessage | null>(null);
  const [detailErr, setDetailErr] = useState<unknown>(null);
  const [images, setImages] = useState(false);
  const [drafts, setDrafts] = useState<MailDraft[]>([]);
  const [compose, setCompose] = useState<ComposeInit | null>(null);
  const [outgoing, setOutgoing] = useState<Outgoing[]>([]);
  const [outbox, setOutbox] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const reqTok = useRef(0);
  const loadingMore = useRef(false);
  const scroller = useRef<HTMLDivElement>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  const searchId = useId();

  const searching = dq !== "" || filters.folder !== "" || !!filters.date_from || !!filters.date_to || filters.has_attachments;
  const folder = folders?.find((f) => (folderKey === "inbox" ? f.role === "INBOX" : f.id === folderKey)) ?? null;
  const apiFolder = folderKey;
  const isDrafts = folder?.role === "DRAFTS";
  const st = mailboxState(account);

  const loadFolders = useCallback(() => {
    mail.folders(accId).then(setFolders, () => setFolders((f) => f ?? []));
  }, [accId]);
  useEffect(loadFolders, [loadFolders]);

  const sig = JSON.stringify({ accId, apiFolder, searching, dq, filters, unreadOnly });
  const loadList = useCallback(
    async (more: string | null = null) => {
      const s = JSON.parse(sig) as { accId: string; apiFolder: string; searching: boolean; dq: string; filters: SearchFilters; unreadOnly: boolean };
      const tok = more ? reqTok.current : ++reqTok.current;
      if (more) {
        if (loadingMore.current) return;
        loadingMore.current = true;
      }
      try {
        const page = s.searching ? await mail.search(s.accId, s.dq, s.filters, more) : await mail.messages(s.accId, { folder: s.apiFolder, cursor: more, unread: s.unreadOnly });
        if (tok !== reqTok.current) return;
        setList((prev) => ({ rows: more && prev.rows ? [...prev.rows, ...page.items] : page.items, cursor: page.next_cursor, error: null }));
      } catch (err) {
        if (tok === reqTok.current) setList((prev) => ({ ...prev, rows: more ? prev.rows : (prev.rows ?? null), error: err }));
      } finally {
        if (more) loadingMore.current = false;
      }
    },
    [sig],
  );
  useEffect(() => {
    setList({ rows: null, cursor: null, error: null });
    void loadList();
  }, [loadList]);

  // Older mail loads as the list is scrolled (keyset pages; only what is looked at is fetched).
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !list.cursor) return;
    const io = new IntersectionObserver((e) => e.some((x) => x.isIntersecting) && void loadList(list.cursor), { root: scroller.current, rootMargin: "400px" });
    io.observe(el);
    return () => io.disconnect();
  }, [list.cursor, list.rows, loadList]);

  const loadDrafts = useCallback(() => {
    mail.drafts(accId).then(setDrafts, () => setDrafts([]));
  }, [accId]);
  useEffect(() => {
    if (isDrafts) loadDrafts();
  }, [isDrafts, loadDrafts]);

  const loadOutgoing = useCallback(() => {
    mail.outgoing(accId).then(setOutgoing, () => undefined);
  }, [accId]);
  const busy = outgoing.some((o) => o.status === "QUEUED" || o.status === "SENDING");
  useEffect(() => {
    loadOutgoing();
    const t = setInterval(loadOutgoing, busy ? 5000 : 60_000);
    return () => clearInterval(t);
  }, [loadOutgoing, busy]);

  // The open email (also when it isn't in the loaded page, e.g. from ⌘K search).
  useEffect(() => {
    setImages(false);
  }, [openId]);
  const openMessage = useCallback(
    (id: string, withImages: boolean) => {
      setDetailErr(null);
      mail.message(id, withImages).then(
        (d) => {
          setDetail(d);
          if (d.unread) {
            // Opening marks it read for me only (Lark is not changed).
            mail.setState(d.id, { read: true }).then(() => {
              setList((p) => ({ ...p, rows: p.rows?.map((r) => (r.id === d.id ? { ...r, unread: false } : r)) ?? null }));
              setFolders((fs) => fs?.map((f) => (f.id === d.folder_id && f.unread > 0 ? { ...f, unread: f.unread - 1 } : f)) ?? null);
              setDetail((x) => (x && x.id === d.id ? { ...x, unread: false } : x));
              refreshUnread();
            }, () => undefined);
          }
        },
        (err: unknown) => setDetailErr(err),
      );
    },
    [],
  );
  useEffect(() => {
    if (!openId) return setDetail(null);
    setDetail((d) => (d && d.id === openId ? d : null));
    openMessage(openId, images);
  }, [openId, images, openMessage]);

  const rows = useMemo(() => list.rows ?? [], [list.rows]);
  const open = (id: string) => {
    setSel(id);
    setOpenId(id);
  };
  const move = (step: number) => {
    if (!rows.length) return;
    const i = rows.findIndex((r) => r.id === (sel ?? openId));
    const next = rows[Math.min(rows.length - 1, Math.max(0, i < 0 ? 0 : i + step))];
    if (next) open(next.id);
  };
  useEffect(() => {
    if (sel) document.getElementById(`mail-row-${sel}`)?.scrollIntoView({ block: "nearest" });
  }, [sel]);

  async function toggleStar(id: string, now: boolean) {
    const flip = (on: boolean) => {
      setList((p) => ({ ...p, rows: p.rows?.map((r) => (r.id === id ? { ...r, starred: on } : r)) ?? null }));
      setDetail((d) => (d && d.id === id ? { ...d, starred: on } : d));
    };
    flip(!now);
    try {
      await mail.setState(id, { starred: !now });
    } catch (err) {
      flip(now);
      setNotice(mailError(err));
    }
  }

  const startCompose = useCallback(
    (mode: ComposeMode) => {
      if (mode === "new") return setCompose({ mode });
      if (detail) setCompose({ mode, source: detail });
    },
    [detail],
  );

  // Keyboard: j/k or arrows move, Enter opens, r/a/f reply/all/forward, c compose, s star.
  // "/" (search) is handled by the app shell; ⌘K / Ctrl K stays the universal search.
  const keys = useRef<(e: KeyboardEvent) => void>(() => undefined);
  keys.current = (e: KeyboardEvent) => {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey || compose) return;
    const t = e.target as HTMLElement;
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable || document.querySelector("dialog[open]")) return;
    const k = e.key;
    if (k === "j" || k === "ArrowDown") move(1);
    else if (k === "k" || k === "ArrowUp") move(-1);
    else if (k === "Enter" && sel && sel !== openId && t.tagName !== "BUTTON" && t.tagName !== "A") open(sel);
    else if (k === "c") startCompose("new");
    else if ((k === "r" || k === "a" || k === "f") && detail && account.can_send) startCompose(k === "r" ? "reply" : k === "a" ? "reply_all" : "forward");
    else if (k === "s" && detail) void toggleStar(detail.id, detail.starred);
    else return;
    e.preventDefault();
  };
  useEffect(() => {
    const h = (e: KeyboardEvent) => keys.current(e);
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  const ws = useMemo(() => words(dq), [dq]);
  const folderName = searching ? "Search results" : (folder?.name ?? (folderKey === "starred" ? "Starred" : "Inbox"));
  const neverSynced = !account.last_sync_at;
  const selectCls = "input py-2 text-sm";

  const accountPicker = (id: string) =>
    accounts.length > 1 ? (
      <div>
        <label htmlFor={id} className="sr-only">
          Mailbox
        </label>
        <select id={id} className={selectCls} value={accId} onChange={(e) => onAccount(e.target.value)}>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.label}
              {a.unread ? ` (${a.unread})` : ""}
            </option>
          ))}
        </select>
      </div>
    ) : (
      <p className="truncate px-1 text-sm font-semibold" title={account.address}>
        {account.label}
      </p>
    );

  return (
    <>
      {notice && (
        <p role="status" className="flex items-center gap-2 rounded-[var(--radius-ctl)] bg-card px-4 py-2.5 text-sm">
          <Icon name="info" size={16} className="text-info" />
          <span className="min-w-0 flex-1">{notice}</span>
          <button type="button" className="icon-btn size-8 border-transparent" aria-label="Dismiss" onClick={() => setNotice(null)}>
            <Icon name="x" size={14} />
          </button>
        </p>
      )}
      <section
        aria-label="Mailbox"
        className="card grid h-[calc(100dvh-11rem)] min-h-[28rem] grid-cols-1 overflow-clip md:h-auto md:min-h-0 md:flex-1 md:grid-cols-[minmax(0,20rem)_minmax(0,1fr)] xl:grid-cols-[15.5rem_minmax(0,22rem)_minmax(0,1fr)]"
      >
        {/* ---------- pane 1: mailbox + folders (wide screens) ---------- */}
        <div className="hidden min-h-0 flex-col gap-4 overflow-y-auto border-r border-line p-4 xl:flex">
          {accountPicker(`${searchId}-acc`)}
          <button type="button" className="btn-primary w-full" onClick={() => startCompose("new")} aria-keyshortcuts="c" title="New email (c)">
            <Icon name="plus" size={16} />
            Compose
          </button>
          <nav aria-label="Folders">
            {folders === null ? (
              <div className="space-y-3 p-2" aria-hidden>
                {[1, 2, 3, 4].map((i) => (
                  <div key={i} className="skeleton h-3.5 w-3/4" />
                ))}
              </div>
            ) : (
              <ul className="space-y-0.5">
                {folders.map((f) => {
                  const key = f.role === "INBOX" ? "inbox" : f.id;
                  const on = !searching && key === (folder?.role === "INBOX" ? "inbox" : folderKey);
                  return (
                    <li key={f.id}>
                      <button
                        type="button"
                        aria-current={on ? "page" : undefined}
                        onClick={() => {
                          setQ("");
                          setFilters(NO_FILTERS);
                          onFolder(key);
                        }}
                        className={`flex w-full cursor-pointer items-center gap-2.5 rounded-ctl px-3 py-2 text-left text-sm transition-colors duration-150 ${on ? "bg-brand-accent/12 font-semibold text-brand-primary-hover" : "text-ink hover:bg-subtle"}`}
                      >
                        {f.role === "STARRED" ? <Star on={false} size={16} /> : <Icon name={FOLDER_ICON[f.role] ?? "documents"} size={16} className="text-ink-muted" />}
                        <span className="min-w-0 flex-1 truncate">{f.name}</span>
                        {f.unread > 0 && (
                          <span className="num text-xs font-semibold text-brand-primary-hover">
                            {f.unread}
                            <span className="sr-only"> unread</span>
                          </span>
                        )}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </nav>
          <div className="mt-auto space-y-1 border-t border-line pt-3 text-xs text-ink-muted">
            <StatusChip tone={st.tone}>{st.label}</StatusChip>
            <p>{account.last_sync_at ? `Synced ${ago(account.last_sync_at)}` : "Never synced"}</p>
            {!account.history_complete && !neverSynced && <p>History still importing…</p>}
            {!account.can_send && <p>Read only: sending is not available.</p>}
          </div>
        </div>

        {/* ---------- pane 2: list ---------- */}
        <div className={`min-h-0 flex-col border-line md:flex md:border-r ${openId ? "hidden" : "flex"}`}>
          <div className="space-y-2 border-b border-line p-3">
            <div className="flex gap-2 xl:hidden">
              <div className="min-w-0 flex-1">{accountPicker(`${searchId}-acc2`)}</div>
              {folders && folders.length > 0 && (
                <div className="min-w-0 flex-1">
                  <label htmlFor={`${searchId}-fold`} className="sr-only">
                    Folder
                  </label>
                  <select
                    id={`${searchId}-fold`}
                    className={selectCls}
                    value={folder ? (folder.role === "INBOX" ? "inbox" : folder.id) : folderKey}
                    onChange={(e) => {
                      setQ("");
                      setFilters(NO_FILTERS);
                      onFolder(e.target.value);
                    }}
                  >
                    {folders.map((f) => (
                      <option key={f.id} value={f.role === "INBOX" ? "inbox" : f.id}>
                        {f.name}
                        {f.unread ? ` (${f.unread})` : ""}
                      </option>
                    ))}
                  </select>
                </div>
              )}
            </div>
            <div className="flex items-center gap-2">
              <label className="relative min-w-0 flex-1">
                <span className="sr-only">Search {account.label}</span>
                <Icon name="search" size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-ink-muted" />
                <input
                  data-page-search
                  type="search"
                  aria-controls={`${searchId}-list`}
                  aria-keyshortcuts="/"
                  className="input w-full rounded-full py-2 pl-10"
                  placeholder={`Search ${account.label.replace(/\s*\(synthetic\)/i, "")}`}
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                      e.preventDefault();
                      const i = rows.findIndex((r) => r.id === sel);
                      const n = rows[Math.min(rows.length - 1, Math.max(0, i + (e.key === "ArrowDown" ? 1 : -1)))];
                      if (n) setSel(n.id);
                    } else if (e.key === "Enter" && sel) {
                      e.preventDefault();
                      setOpenId(sel);
                    } else if (e.key === "Escape" && q) {
                      e.preventDefault();
                      setQ("");
                    }
                  }}
                />
              </label>
              <button type="button" className="icon-btn size-10 border-transparent bg-brand-secondary text-white hover:bg-ink/85 xl:hidden" aria-label="Compose" title="New email (c)" onClick={() => startCompose("new")}>
                <Icon name="plus" size={16} />
              </button>
              <button type="button" className={`icon-btn size-10 ${showFilters || searching ? "border-brand-primary text-brand-primary-hover" : ""}`} aria-expanded={showFilters} aria-label="Search filters" title="Search filters" onClick={() => setShowFilters((s) => !s)}>
                <Icon name="sort" size={16} />
              </button>
            </div>
            {showFilters && (
              <div className="grid grid-cols-2 gap-2 text-sm" role="group" aria-label="Search filters">
                <label className="col-span-2">
                  <span className="mb-1 block text-xs text-ink-muted">Folder</span>
                  <select className={selectCls} value={filters.folder} onChange={(e) => setFilters((f) => ({ ...f, folder: e.target.value }))}>
                    <option value="">All folders</option>
                    {folders?.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  <span className="mb-1 block text-xs text-ink-muted">From date</span>
                  <input type="date" className={selectCls} value={filters.date_from} max={filters.date_to || undefined} onChange={(e) => setFilters((f) => ({ ...f, date_from: e.target.value }))} />
                </label>
                <label>
                  <span className="mb-1 block text-xs text-ink-muted">To date</span>
                  <input type="date" className={selectCls} value={filters.date_to} min={filters.date_from || undefined} onChange={(e) => setFilters((f) => ({ ...f, date_to: e.target.value }))} />
                </label>
                <label className="col-span-2 flex items-center gap-2">
                  <input type="checkbox" className="size-4 accent-brand-primary" checked={filters.has_attachments} onChange={(e) => setFilters((f) => ({ ...f, has_attachments: e.target.checked }))} />
                  Has attachments
                </label>
                {searching && (
                  <button type="button" className="col-span-2 cursor-pointer justify-self-start text-sm font-medium text-brand-primary-hover hover:underline" onClick={() => {
                      setFilters(NO_FILTERS);
                      setQ("");
                    }}>
                    Clear search
                  </button>
                )}
              </div>
            )}
          </div>
          <OutboxStrip items={outgoing} onOpen={() => setOutbox(true)} />
          <div className="flex items-center gap-2 px-4 py-2 text-xs text-ink-muted">
            <span className="min-w-0 flex-1 truncate font-medium text-ink" aria-live="polite">
              {folderName}
              {!searching && folder && folder.total > 0 && <span className="num font-normal text-ink-muted"> · {folder.total}</span>}
            </span>
            {!searching && (
              <button type="button" aria-pressed={unreadOnly} className={`chip cursor-pointer ${unreadOnly ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:bg-subtle"}`} onClick={() => setUnreadOnly((u) => !u)}>
                Unread
              </button>
            )}
          </div>
          <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto border-t border-line">
            {isDrafts && !searching && drafts.length > 0 && (
              <section aria-label="Drafts saved in Pro Trading" className="border-b border-line">
                <p className="bg-subtle px-4 py-1.5 text-xs font-medium text-ink-muted">Saved in Pro Trading</p>
                <ul>
                  {drafts.map((d) => (
                    <li key={d.id} className="border-b border-line last:border-b-0">
                      <button type="button" className="block w-full cursor-pointer px-4 py-2.5 text-left hover:bg-subtle" onClick={() => setCompose({ mode: d.mode, draft: d })}>
                        <span className="flex items-baseline gap-2 text-sm">
                          <span className="font-medium text-danger">Draft</span>
                          <span className="min-w-0 flex-1 truncate">{d.to.map(addrText).join(", ") || "(no recipients)"}</span>
                          <time className="num shrink-0 text-xs text-ink-muted" dateTime={d.updated_at}>
                            {whenShort(d.updated_at)}
                          </time>
                        </span>
                        <span className="block truncate text-sm text-ink-muted">{d.subject || "(no subject)"}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {list.error && !list.rows ? (
              <ErrorState title="Couldn't load the emails" error={list.error} onRetry={() => void loadList()} />
            ) : list.rows === null ? (
              <LoadingRows rows={8} cols={1} />
            ) : rows.length === 0 ? (
              account.status === "NOT_CONFIGURED" && neverSynced && !searching ? (
                <EmptyState icon="mail" title="Not connected yet" hint="A Super Admin enters the Lark app password in Settings > Email Services. Mail appears here after the first sync." />
              ) : (
                <EmptyState
                  icon={searching ? "search" : "inbox"}
                  title={searching ? "No emails match" : isDrafts && drafts.length ? "No drafts in Lark" : unreadOnly ? "No unread emails" : "No emails here"}
                  hint={searching ? `Searched ${account.label} only. Try other words or fewer filters.` : !account.history_complete && !neverSynced ? "History still importing… older mail appears as it arrives." : undefined}
                />
              )
            ) : (
              <ul id={`${searchId}-list`} aria-label="Email list">
                {rows.map((r) => (
                  <Row key={r.id} r={r} active={r.id === openId} selected={r.id === sel} ws={searching ? ws : []} onOpen={() => open(r.id)} onStar={() => void toggleStar(r.id, r.starred)} />
                ))}
              </ul>
            )}
            {list.cursor && list.rows && (
              <div ref={sentinel} className="flex justify-center p-3">
                <button type="button" className="btn-ghost px-4 py-1.5 text-xs" onClick={() => void loadList(list.cursor)}>
                  Load older emails
                </button>
              </div>
            )}
          </div>
        </div>

        {/* ---------- pane 3: reading ---------- */}
        <div className={`min-h-0 min-w-0 flex-col md:flex ${openId ? "flex" : "hidden"}`}>
          {!openId ? (
            <div className="grid flex-1 place-items-center">
              <EmptyState icon="mail" title="Select an email" hint="j / k move through the list · r reply · f forward · c compose" />
            </div>
          ) : detailErr && !detail ? (
            <div>
              <button type="button" className="m-3 inline-flex cursor-pointer items-center gap-1 text-sm text-brand-primary-hover md:hidden" onClick={() => setOpenId(null)}>
                <Icon name="chevronLeft" size={16} />
                Back
              </button>
              <ErrorState title="Couldn't open this email" error={detailErr} onRetry={() => openMessage(openId, images)} />
            </div>
          ) : !detail ? (
            <LoadingRows rows={5} cols={1} />
          ) : (
            <ReadingPane
              m={detail}
              canSend={account.can_send}
              onBack={() => setOpenId(null)}
              onCompose={startCompose}
              onStar={() => void toggleStar(detail.id, detail.starred)}
              onOpen={open}
              onLoadImages={() => setImages(true)}
              onLinks={(links) => setDetail((d) => (d ? { ...d, links } : d))}
            />
          )}
        </div>
      </section>

      {compose && (
        <Compose
          accounts={accounts}
          accountId={accId}
          init={compose}
          onClose={() => {
            setCompose(null);
            if (isDrafts) loadDrafts();
          }}
          onSent={(o) => {
            setCompose(null);
            setOutgoing((x) => [o, ...x.filter((y) => y.id !== o.id)]);
            setNotice(`“${o.subject || "(no subject)"}” is queued for sending. Its delivery status is shown in the Outbox above the list.`);
            loadOutgoing();
            if (isDrafts) loadDrafts();
          }}
        />
      )}
      <OutboxDialog open={outbox} items={outgoing} onClose={() => setOutbox(false)} onChanged={loadOutgoing} />
    </>
  );
}

function Row({ r, active, selected, ws, onOpen, onStar }: { r: MailRow; active: boolean; selected: boolean; ws: string[]; onOpen: () => void; onStar: () => void }) {
  const hl = (t: string) => highlightParts(t, ws).map((p, i) => (p.hit ? <mark key={i} className="rounded-sm bg-brand-accent/25 px-0.5 text-ink">{p.text}</mark> : <span key={i}>{p.text}</span>));
  return (
    <li
      id={`mail-row-${r.id}`}
      data-selected={selected || undefined}
      className={`relative border-b border-line [contain-intrinsic-size:auto_4.75rem] [content-visibility:auto] last:border-b-0 ${active ? "bg-brand-accent/10" : selected ? "bg-subtle" : ""}`}
    >
      <button
        type="button"
        onClick={onOpen}
        aria-current={active ? "true" : undefined}
        className="grid w-full cursor-pointer grid-cols-[0.5rem_minmax(0,1fr)_auto] items-start gap-x-2.5 py-2.5 pl-3 pr-10 text-left transition-colors duration-150 hover:bg-subtle focus-visible:outline-2 focus-visible:-outline-offset-2"
      >
        <span aria-hidden className={`mt-1.5 size-2 rounded-full ${r.unread ? "bg-brand-primary" : ""}`} />
        <span className="min-w-0">
          <span className="flex items-center gap-1.5">
            <span className={`truncate text-sm ${r.unread ? "font-semibold" : ""}`}>{hl(senderOf(r))}</span>
            {r.unread && <span className="sr-only">(unread)</span>}
            {r.thread_count > 1 && (
              <span className="num shrink-0 rounded-full bg-subtle px-1.5 text-[11px] text-ink-muted" title={`${r.thread_count} emails in this conversation`}>
                {r.thread_count}
              </span>
            )}
          </span>
          <span className={`block truncate text-sm ${r.unread ? "text-ink" : "text-ink-muted"}`}>{hl(r.subject || "(no subject)")}</span>
          <span className="block truncate text-xs text-ink-muted">{hl(r.highlight || r.snippet)}</span>
        </span>
        <span className="flex flex-col items-end gap-1 pt-0.5">
          <time className="num whitespace-nowrap text-xs text-ink-muted" dateTime={r.received_at} title={whenFull(r.received_at)}>
            {whenShort(r.received_at)}
          </time>
          {r.attachment_count > 0 && (
            <span className="text-ink-muted" title={`${r.attachment_count} ${r.attachment_count === 1 ? "attachment" : "attachments"}`}>
              <Clip />
              <span className="sr-only">{r.attachment_count === 1 ? "has an attachment" : `${r.attachment_count} attachments`}</span>
            </span>
          )}
        </span>
      </button>
      <button type="button" className="absolute bottom-2 right-2 grid size-8 cursor-pointer place-items-center rounded-full hover:bg-line" aria-pressed={r.starred} aria-label={r.starred ? `Unstar ${r.subject}` : `Star ${r.subject}`} onClick={onStar}>
        <Star on={r.starred} size={15} />
      </button>
    </li>
  );
}

function Clip() {
  return (
    <svg width={14} height={14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M21 11.5l-8.6 8.6a5.5 5.5 0 01-7.8-7.8l8.6-8.6a3.7 3.7 0 015.2 5.2l-8.6 8.6a1.8 1.8 0 01-2.6-2.6l7.9-7.9" />
    </svg>
  );
}
