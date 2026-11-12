import { useEffect, useMemo, useState } from "react";
import { ago, FOLDER_ICON, mailboxState, whenFull, type MailAccount, type MailFolder } from "../../lib/mail";
import { Icon } from "../../ui/Icon";
import { Avatar, Star } from "./Bits";
import { Menu } from "./Menu";

const PRIMARY = new Set(["INBOX", "STARRED", "SENT", "DRAFTS"]);

/** The mailbox rail: mailbox card + switcher, Compose, folders with counts, a warning when
 * something could not be sent, and a small status line. `compact`: icons only (tablets). */
export function Rail({
  compact,
  account,
  accounts,
  folders,
  folderKey,
  drafts,
  problems,
  onAccount,
  onFolder,
  onCompose,
  onProblems,
  onHelp,
  onSignature,
}: {
  compact: boolean;
  account: MailAccount;
  accounts: MailAccount[];
  folders: MailFolder[] | null;
  folderKey: string;
  drafts: number;
  problems: number;
  onAccount: (id: string) => void;
  onFolder: (key: string) => void;
  onCompose: () => void;
  onProblems: () => void;
  onHelp: () => void;
  onSignature: () => void;
}) {
  const keyOf = (f: MailFolder) => (f.role === "INBOX" ? "inbox" : f.id);
  const all = useMemo(() => folders ?? [], [folders]);
  // A short folder list is shown whole; a long one keeps the rarely used ones under More.
  const few = all.length <= 9;
  const primary = useMemo(() => (few ? all : all.filter((f) => PRIMARY.has(f.role))), [all, few]);
  const more = useMemo(() => (few ? [] : all.filter((f) => !PRIMARY.has(f.role))), [all, few]);
  const [showMore, setShowMore] = useState(false);
  useEffect(() => {
    if (more.some((f) => (f.role === "INBOX" ? "inbox" : f.id) === folderKey)) setShowMore(true);
  }, [folderKey, more]);
  const st = mailboxState(account);
  const short = account.label.replace(/\s*\(synthetic\)/i, "");
  const pill = (on: boolean) =>
    `flex w-full cursor-pointer items-center rounded-full text-sm transition-colors duration-150 ${compact ? "h-9 justify-center" : "h-8 gap-3.5 pl-4 pr-3"} ${on ? "bg-brand-accent/20 font-semibold text-ink" : "text-ink hover:bg-ink/5"}`;
  const item = (f: MailFolder) => {
    const on = keyOf(f) === folderKey;
    const count = f.role === "DRAFTS" ? f.total + drafts : f.role === "SENT" || f.role === "ALL" ? 0 : f.unread;
    return (
      <li key={f.id}>
        <button type="button" aria-current={on ? "page" : undefined} className={pill(on)} onClick={() => onFolder(keyOf(f))} title={compact ? f.name : undefined}>
          <span className="relative">
            {f.role === "STARRED" ? <Star on={false} size={17} /> : <Icon name={FOLDER_ICON[f.role] ?? "documents"} size={17} className={on ? "text-brand-primary-hover" : "text-ink-muted"} />}
            {compact && count > 0 && <span className="absolute -right-1 -top-1 size-2 rounded-full bg-brand-primary" aria-hidden />}
          </span>
          <span className={compact ? "sr-only" : "min-w-0 flex-1 truncate text-left"}>{f.name}</span>
          {count > 0 && (
            <span className={`num text-xs ${compact ? "sr-only" : ""} ${f.role === "DRAFTS" ? "text-ink-muted" : "font-bold"}`}>
              {count}
              <span className="sr-only">{f.role === "DRAFTS" ? " drafts" : " unread"}</span>
            </span>
          )}
        </button>
      </li>
    );
  };
  const switcher = accounts.length > 1 && (
    <Menu
      label={`Mailbox: ${account.label}`}
      button={compact ? <Avatar name={short} size={34} /> : <Icon name="chevronDown" size={16} />}
      items={[{ heading: "Switch mailbox" }, ...accounts.map((a) => ({ label: `${a.label}${a.unread ? ` · ${a.unread} unread` : ""}`, checked: a.id === account.id, onSelect: () => onAccount(a.id) }))]}
    />
  );
  return (
    <div className="flex flex-col gap-2.5 pb-3">
      {compact ? (
        switcher && <div className="mx-auto">{switcher}</div>
      ) : (
        <div className="flex items-center gap-2.5 rounded-2xl bg-card py-2 pl-2.5 pr-1">
          <Avatar name={short} size={34} />
          <div className="min-w-0 flex-1 leading-tight">
            <p className="truncate text-sm font-semibold" title={account.label}>
              {account.label}
            </p>
            <p className="truncate text-xs text-ink-muted" title={account.address}>
              {account.address}
            </p>
          </div>
          {switcher}
        </div>
      )}
      <button
        type="button"
        onClick={onCompose}
        aria-keyshortcuts="c"
        title="New email (c)"
        aria-label="Compose"
        className={`inline-flex cursor-pointer items-center gap-3 self-start rounded-2xl bg-brand-accent/25 text-sm font-semibold text-ink shadow-[0_1px_2px_rgb(24_24_27/0.08)] transition-shadow duration-150 hover:shadow-[0_2px_8px_rgb(24_24_27/0.16)] ${compact ? "mx-auto size-12 justify-center" : "h-12 px-5"}`}
      >
        <Icon name="pencil" size={18} />
        {!compact && "Compose"}
      </button>
      {problems > 0 && (
        <button
          type="button"
          onClick={onProblems}
          className={`inline-flex cursor-pointer items-center gap-1.5 self-start rounded-full bg-warning/12 text-xs font-medium text-ink hover:bg-warning/20 ${compact ? "mx-auto size-9 justify-center" : "px-3 py-1.5"}`}
          aria-label={`${problems} not sent: see details`}
        >
          <Icon name="alert" size={14} className="text-warning" />
          {!compact && `${problems} not sent`}
        </button>
      )}
      <nav aria-label="Folders">
        {folders === null ? (
          <div className="space-y-3.5 px-4 pt-2" aria-hidden>
            {[1, 2, 3, 4, 5, 6].map((i) => (
              <div key={i} className="skeleton h-3 w-3/4" />
            ))}
          </div>
        ) : (
          <ul>
            {primary.map(item)}
            {showMore && more.map(item)}
            {more.length > 0 && (
              <li>
                <button type="button" className={pill(false)} aria-expanded={showMore} onClick={() => setShowMore((x) => !x)} title={compact ? (showMore ? "Less" : "More") : undefined}>
                  <Icon name="chevronDown" size={17} className={`text-ink-muted ${showMore ? "rotate-180" : ""}`} />
                  <span className={compact ? "sr-only" : "flex-1 text-left"}>{showMore ? "Less" : "More"}</span>
                </button>
              </li>
            )}
          </ul>
        )}
      </nav>
      {!compact && (
        <div className="space-y-1.5 border-t border-line px-4 pt-3 text-xs text-ink-muted">
          <p className="flex items-center gap-1.5" title={account.last_sync_at ? whenFull(account.last_sync_at) : undefined}>
            <span className={`size-2 shrink-0 rounded-full ${st.tone === "success" ? "bg-success" : st.tone === "danger" ? "bg-danger" : st.tone === "warning" ? "bg-warning" : "bg-brand-gray"}`} aria-hidden />
            <span className="truncate">
              {st.label} · {account.last_sync_at ? `synced ${ago(account.last_sync_at)}` : "never synced"}
            </span>
          </p>
          {!account.history_complete && account.last_sync_at && <p>History still importing…</p>}
          {!account.can_send && <p>Read only: sending is not available.</p>}
          <div className="flex flex-wrap gap-x-3 gap-y-1">
            <button type="button" className="inline-flex cursor-pointer items-center gap-1.5 hover:text-ink hover:underline" onClick={onSignature}>
              <Icon name="signature" size={14} />
              Signature
            </button>
            <button type="button" className="inline-flex cursor-pointer items-center gap-1.5 hover:text-ink hover:underline" onClick={onHelp}>
              <Icon name="keyboard" size={14} />
              Shortcuts
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
