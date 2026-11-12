import { useCallback, useEffect, useId, useState, type FormEvent, type ReactNode } from "react";
import { has } from "../../api";
import { useAuth } from "../../auth";
import { ago, HEALTH, mailAdmin, mailError, whenFull, type AdminAccount, type CompanyUser, type LogEntry, type SyncRun, type TestResult } from "../../lib/mail";
import { Icon } from "../../ui/Icon";
import { EmptyState, ErrorState, LoadingRows, StatusChip, type Tone } from "../../ui/primitives";
import { Field, Modal, Notice, type Msg } from "./shared";

const STATUS: Record<string, { tone: Tone; label: string }> = {
  NOT_CONFIGURED: { tone: "neutral", label: "Not configured" },
  CONNECTED: { tone: "success", label: "Connected" },
  ERROR: { tone: "warning", label: "Error" },
  AUTH_FAILED: { tone: "danger", label: "Password rejected" },
  PAUSED: { tone: "neutral", label: "Paused" },
};
const statusOf = (s: string) => STATUS[s] ?? { tone: "neutral" as Tone, label: s };
const isInfo = (address: string) => /^info[.@]/i.test(address.trim());
const INFO_NOTE = "Check in the Lark admin console whether info@ is its own mailbox, a public mailbox or an alias before connecting it.";
const HELP = "Use the Lark app authorization code (Lark → Settings → Email → third-party clients), not the employee's normal password.";
const every = (s: number) => (s % 3600 === 0 ? `${s / 3600} h` : s % 60 === 0 ? `${s / 60} min` : `${s} s`);

/**
 * Settings › Integrations › Email Services › Lark Mail (Super Admin only, mail.manage).
 * Every state shown is what the API reports: a mailbox is only "Connected" once Lark accepted
 * its login. The Lark app password is typed, sent once, and never shown, kept or pre-filled.
 */
export function LarkMail() {
  const { session } = useAuth();
  const allowed = !!session && has(session, "mail.manage");
  const [accounts, setAccounts] = useState<AdminAccount[] | null>(null);
  const [users, setUsers] = useState<CompanyUser[]>([]);
  const [failed, setFailed] = useState<unknown>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(() => {
    mailAdmin.accounts().then(
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
    mailAdmin.users().then(setUsers, () => setUsers([]));
  }, [allowed, load]);
  const replace = (a: AdminAccount) => setAccounts((x) => x?.map((y) => (y.id === a.id ? a : y)) ?? x);

  if (!allowed)
    return (
      <section className="card">
        <EmptyState icon="shield" title="Only a Super Admin can open Email Services" hint="Company mailboxes, Lark passwords and who may use each mailbox are managed by a Super Admin." />
      </section>
    );

  return (
    <div className="space-y-5">
      <section className="card space-y-4 p-6" aria-labelledby="lark-title">
        <div className="flex flex-wrap items-start gap-4">
          <div className="min-w-0 flex-1 space-y-1">
            <p className="kicker">Integrations › Email Services</p>
            <h2 id="lark-title" className="text-xl font-semibold tracking-tight">
              Lark Mail
            </h2>
            <p className="max-w-2xl text-sm text-ink-muted">Company mailboxes read and sent through Lark. Each mailbox is given to people one by one; nobody sees a mailbox they were not given, and there is no combined inbox.</p>
          </div>
          <button type="button" className="btn-primary" onClick={() => setAdding(true)}>
            <Icon name="plus" size={16} />
            Add mailbox
          </button>
        </div>
        <div className="grid gap-4 rounded-[var(--radius-ctl)] bg-subtle p-4 text-sm md:grid-cols-[auto_minmax(0,1fr)]">
          <dl className="grid grid-cols-[auto_auto] gap-x-4 gap-y-1">
            <dt className="text-ink-muted">Incoming (IMAP)</dt>
            <dd className="num font-medium">imap.larksuite.com : 993 · SSL</dd>
            <dt className="text-ink-muted">Outgoing (SMTP)</dt>
            <dd className="num font-medium">smtp.larksuite.com : 465 · SSL</dd>
            <dt className="text-ink-muted">User name</dt>
            <dd className="font-medium">the full email address</dd>
          </dl>
          <p className="flex items-start gap-2 md:border-l md:border-line md:pl-4">
            <Icon name="info" size={16} className="mt-0.5 text-info" />
            <span>{HELP}</span>
          </p>
        </div>
      </section>

      <section className="card overflow-hidden" aria-label="Mailboxes">
        {failed && !accounts ? (
          <ErrorState title="Couldn't load the mailboxes" error={failed} onRetry={load} />
        ) : !accounts ? (
          <LoadingRows rows={4} cols={4} />
        ) : accounts.length === 0 ? (
          <EmptyState icon="mail" title="No mailbox is set up yet" hint="Add a company mailbox, enter its Lark app password, then give it to the people who use it." />
        ) : (
          <ul className="divide-y divide-line">
            {accounts.map((a) => (
              <li key={a.id}>
                <AccountRow a={a} open={open === a.id} onToggle={() => setOpen(open === a.id ? null : a.id)} />
                {open === a.id && <AccountPanel a={a} users={users} onChanged={replace} onReload={load} />}
              </li>
            ))}
          </ul>
        )}
      </section>
      <AddMailbox
        open={adding}
        users={users}
        onClose={() => setAdding(false)}
        onAdded={(a) => {
          setAdding(false);
          setAccounts((x) => [...(x ?? []), a]);
          setOpen(a.id);
        }}
      />
    </div>
  );
}

function AccountRow({ a, open, onToggle }: { a: AdminAccount; open: boolean; onToggle: () => void }) {
  const s = statusOf(a.status);
  const h = HEALTH[a.health];
  return (
    <button type="button" onClick={onToggle} aria-expanded={open} className="grid w-full cursor-pointer grid-cols-1 items-center gap-x-6 gap-y-2 px-6 py-4 text-left hover:bg-subtle md:grid-cols-[minmax(0,1.4fr)_auto_minmax(0,1fr)_auto]">
      <span className="min-w-0">
        <span className="block truncate font-semibold">{a.label}</span>
        <span className="block truncate text-sm text-ink-muted">
          {a.address} · {a.kind === "SHARED" ? "Shared" : "Personal"}
        </span>
      </span>
      <span className="flex flex-wrap items-center gap-1.5">
        <StatusChip tone={s.tone}>{s.label}</StatusChip>
        {h && a.health !== a.status.toLowerCase() && a.health !== "not_configured" && a.health !== "paused" && a.health !== "password_rejected" && <StatusChip tone={h.tone}>{h.label}</StatusChip>}
        {a.syncing && <StatusChip tone="info">Syncing now</StatusChip>}
      </span>
      <span className="min-w-0 text-sm text-ink-muted">
        <span className="block truncate">{a.users.length ? a.users.map((u) => u.name).join(", ") : "Not given to anyone"}</span>
        <span className="block">
          {a.last_sync_at ? `Synced ${ago(a.last_sync_at)}` : "Never synced"} · <span className="num">{a.message_count}</span> emails{!a.history_complete && a.last_sync_at ? " · history importing" : ""}
        </span>
      </span>
      <span className="flex items-center gap-1 text-sm font-medium text-brand-primary-hover">
        {open ? "Close" : "Manage"}
        <Icon name="chevronDown" size={16} className={open ? "rotate-180" : ""} />
      </span>
    </button>
  );
}

function Section({ title, children, className = "" }: { title: string; children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 space-y-3 rounded-[var(--radius-ctl)] border border-line bg-card p-4 ${className}`} aria-label={title}>
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function Fact({ k, children }: { k: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-ink-muted">{k}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </>
  );
}

function AccountPanel({ a, users, onChanged, onReload }: { a: AdminAccount; users: CompanyUser[]; onChanged: (a: AdminAccount) => void; onReload: () => void }) {
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const run = async (key: string, fn: () => Promise<AdminAccount>, ok: string) => {
    setBusy(key);
    setMsg(null);
    try {
      onChanged(await fn());
      setMsg({ tone: "ok", text: ok });
    } catch (err) {
      setMsg({ tone: "error", text: mailError(err) });
    } finally {
      setBusy(null);
    }
  };
  const canSync = a.status === "CONNECTED" || a.status === "ERROR";
  return (
    <div className="space-y-4 border-t border-line bg-subtle px-4 py-5 sm:px-6">
      {isInfo(a.address) && <p className="alert-warning">{INFO_NOTE}</p>}
      <Notice msg={msg} />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Section title="Connection">
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
            <Fact k="Status">
              <StatusChip tone={statusOf(a.status).tone}>{statusOf(a.status).label}</StatusChip>
            </Fact>
            <Fact k="Server">
              <span className="num">
                {a.imap_host}:{a.imap_port} · {a.smtp_host}:{a.smtp_port}
              </span>
            </Fact>
            <Fact k="User name">{a.username}</Fact>
            <Fact k="App password">{a.has_password ? `Saved${a.password_set_at ? ` ${whenFull(a.password_set_at)}` : ""}` : "Not entered"}</Fact>
            <Fact k="IMAP (reading)">{a.imap_ok_at ? `Login accepted ${ago(a.imap_ok_at)}` : "Not confirmed yet"}</Fact>
            {a.imap_error && <Fact k="IMAP problem"><span className="text-danger">{a.imap_error}</span></Fact>}
            <Fact k="SMTP (sending)">{a.smtp_ok_at ? `Login accepted ${ago(a.smtp_ok_at)}` : "Not confirmed yet"}</Fact>
            {a.smtp_error && <Fact k="SMTP problem"><span className="text-danger">{a.smtp_error}</span></Fact>}
            <Fact k="Last sync">{a.last_sync_at ? whenFull(a.last_sync_at) : "Never"}</Fact>
            <Fact k="Last attempt">{a.last_sync_attempt_at ? whenFull(a.last_sync_attempt_at) : "Never"}</Fact>
            <Fact k="Checks">{a.sync_enabled ? `Every ${every(a.sync_interval_seconds)}${a.idle_supported ? " (and instantly: Lark supports IDLE)" : ""}` : "Off"}</Fact>
            <Fact k="History">{a.history_complete ? "Fully imported" : a.last_sync_at ? "Still importing…" : "Not started"}</Fact>
            <Fact k="Emails">
              <span className="num">
                {a.message_count} ({a.unread_count} unread in Lark)
              </span>
            </Fact>
            {a.failure_count > 0 && <Fact k="Failures in a row"><span className="num">{a.failure_count}</span></Fact>}
            {a.last_error && (
              <Fact k="Last problem">
                <span className={a.status === "PAUSED" ? "" : "text-danger"}>
                  {a.last_error}
                  {a.last_error_code ? ` (${a.last_error_code})` : ""}
                </span>
              </Fact>
            )}
          </dl>
          <div className="flex flex-wrap gap-2 pt-1">
            <button type="button" className="btn-ghost px-4 py-2" disabled={!canSync || !!busy} title={canSync ? undefined : "Connect the mailbox first (or resume it)"} onClick={() => void run("sync", () => mailAdmin.sync(a.id), "A sync was requested; new mail appears within a minute.")}>
              <Icon name="refresh" size={16} />
              Sync now
            </button>
            {a.status === "PAUSED" ? (
              <button type="button" className="btn-ghost px-4 py-2" disabled={!a.has_password || !!busy} title={a.has_password ? undefined : "Enter the app password first"} onClick={() => void run("resume", () => mailAdmin.resume(a.id), "Resumed. It shows as connected only after Lark accepts the next login.")}>
                Resume
              </button>
            ) : (
              a.status !== "NOT_CONFIGURED" && (
                <button type="button" className="btn-ghost px-4 py-2" disabled={!!busy} onClick={() => void run("pause", () => mailAdmin.pause(a.id), "Paused. No new mail is fetched until you resume.")}>
                  <Icon name="pause" size={16} />
                  Pause
                </button>
              )
            )}
          </div>
        </Section>
        <PasswordForm a={a} onDone={onReload} />
        <AccessEditor a={a} users={users} onChanged={onChanged} />
        <SettingsForm a={a} onChanged={onChanged} />
        <RunsAndLog a={a} />
      </div>
    </div>
  );
}

function TestOutcome({ r }: { r: TestResult }) {
  const line = (ok: boolean, name: string, err: string | null) => (
    <li className="flex items-start gap-2">
      <Icon name={ok ? "check" : "alert"} size={16} className={`mt-0.5 ${ok ? "text-success" : "text-danger"}`} />
      <span>
        {name}: {ok ? "Lark accepted the login" : `failed${err ? ` — ${err}` : ""}`}
      </span>
    </li>
  );
  return (
    <div role="status" className={r.imap_ok && r.smtp_ok ? "alert-success" : "alert-warning"}>
      <ul className="space-y-1">
        {line(r.imap_ok, "Reading (IMAP)", r.imap_error)}
        {line(r.smtp_ok, "Sending (SMTP)", r.smtp_error)}
      </ul>
      {r.imap_ok && r.folders.length > 0 && <p className="mt-1 text-xs">{r.folders.length} folders found.</p>}
      <p className="mt-1 font-medium">{r.saved ? "The password was saved (encrypted)." : "The password was not saved."}</p>
    </div>
  );
}

/** The Lark app password: typed, sent once, cleared right after. Never pre-filled or stored. */
function PasswordForm({ a, onDone }: { a: AdminAccount; onDone: () => void }) {
  const id = useId();
  const [pw, setPw] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function go(kind: "test" | "save" | "saved") {
    const typed = pw;
    setPw(""); // cleared at once: the value only lives in this request
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const r = kind === "save" ? await mailAdmin.credentials(a.id, typed) : kind === "test" ? await mailAdmin.test(a.id, typed) : await mailAdmin.reconnect(a.id);
      setResult(r);
      onDone();
    } catch (err) {
      setError(mailError(err));
    } finally {
      setBusy(false);
    }
  }
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (pw) void go("save");
  };
  return (
    <Section title="Lark app password">
      <form className="space-y-3" onSubmit={submit} autoComplete="off">
        <Field id={id} label={a.has_password ? "New app password (replaces the saved one)" : "App password"} hint={HELP}>
          <input id={id} name="lark-app-password" type="password" autoComplete="new-password" className="input" value={pw} onChange={(e) => setPw(e.target.value)} spellCheck={false} />
        </Field>
        <div className="flex flex-wrap gap-2">
          <button type="submit" className="btn-primary px-4 py-2" disabled={!pw || busy}>
            Test and save
          </button>
          <button type="button" className="btn-ghost px-4 py-2" disabled={!pw || busy} onClick={() => void go("test")}>
            Test only (don't save)
          </button>
          {a.has_password && (
            <button type="button" className="btn-ghost px-4 py-2" disabled={busy} onClick={() => void go("saved")}>
              Test saved password
            </button>
          )}
        </div>
        <p className="text-xs text-ink-muted">Saving logs in to Lark first and keeps the password only if Lark accepts it. No email is sent by a test.</p>
        {busy && (
          <p role="status" className="text-sm text-ink-muted">
            Contacting Lark…
          </p>
        )}
        {result && <TestOutcome r={result} />}
        {error && (
          <p role="alert" className="alert-danger">
            {error}
          </p>
        )}
      </form>
    </Section>
  );
}

function AccessEditor({ a, users, onChanged }: { a: AdminAccount; users: CompanyUser[]; onChanged: (a: AdminAccount) => void }) {
  const start = () => Object.fromEntries(a.users.map((u) => [u.employee_id, u.can_send]));
  const [grants, setGrants] = useState<Record<string, boolean>>(start);
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setGrants(Object.fromEntries(a.users.map((u) => [u.employee_id, u.can_send]))), [a.users]);
  const personal = a.kind === "PERSONAL";
  const toggle = (id: string, on: boolean) =>
    setGrants((g) => {
      const rest = Object.fromEntries(Object.entries(g).filter(([k]) => k !== id && !(personal && on)));
      return on ? { ...rest, [id]: g[id] ?? true } : rest;
    });
  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      onChanged(await mailAdmin.access(a.id, Object.entries(grants).map(([employee_id, can_send]) => ({ employee_id, can_send }))));
      setMsg({ tone: "ok", text: "Access saved." });
    } catch (err) {
      setMsg({ tone: "error", text: mailError(err) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <Section title="Who can use this mailbox">
      <p className="text-xs text-ink-muted">{personal ? "Personal mailbox: one person." : "Shared mailbox: several people, each listed here."} People also need the Email permission (Admin and Super Admin roles have it).</p>
      <ul className="max-h-64 divide-y divide-line overflow-y-auto rounded-[var(--radius-ctl)] border border-line">
        {users.map((u) => {
          const on = u.id in grants;
          return (
            <li key={u.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2 text-sm">
              <label className="flex min-w-0 flex-1 items-center gap-2.5">
                <input type={personal ? "radio" : "checkbox"} name={`acc-${a.id}`} className="size-4 accent-brand-primary" checked={on} onChange={(e) => toggle(u.id, e.target.checked)} />
                <span className="min-w-0">
                  <span className="block truncate">{u.name}</span>
                  <span className="block truncate text-xs text-ink-muted">{u.email}</span>
                </span>
              </label>
              {on && (
                <label className="flex items-center gap-2 text-xs">
                  <input type="checkbox" className="size-4 accent-brand-primary" checked={grants[u.id]} onChange={(e) => setGrants((g) => ({ ...g, [u.id]: e.target.checked }))} />
                  Can send
                </label>
              )}
            </li>
          );
        })}
      </ul>
      <div className="flex flex-wrap items-center gap-2">
        {personal && Object.keys(grants).length > 0 && (
          <button type="button" className="btn-ghost px-4 py-2" onClick={() => setGrants({})}>
            Clear
          </button>
        )}
        <button type="button" className="btn-primary px-4 py-2" disabled={busy} onClick={() => void save()}>
          Save access
        </button>
      </div>
      <Notice msg={msg} />
    </Section>
  );
}

function SettingsForm({ a, onChanged }: { a: AdminAccount; onChanged: (a: AdminAccount) => void }) {
  const id = useId();
  const [label, setLabel] = useState(a.label);
  const [name, setName] = useState(a.display_name);
  const [kind, setKind] = useState(a.kind);
  const [interval, setInterval_] = useState(String(Math.round(a.sync_interval_seconds / 60)));
  const [sig, setSig] = useState(a.signature_html);
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      onChanged(await mailAdmin.patch(a.id, { label, display_name: name, kind, sync_interval_seconds: Math.max(1, Number(interval) || 5) * 60, signature_html: sig }));
      setMsg({ tone: "ok", text: "Saved." });
    } catch (err) {
      setMsg({ tone: "error", text: mailError(err) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <Section title="Mailbox details">
      <form className="grid gap-3 sm:grid-cols-2" onSubmit={(e) => void save(e)}>
        <Field id={`${id}-l`} label="Label">
          <input id={`${id}-l`} className="input" value={label} maxLength={60} onChange={(e) => setLabel(e.target.value)} />
        </Field>
        <Field id={`${id}-n`} label="Sender name">
          <input id={`${id}-n`} className="input" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field id={`${id}-k`} label="Kind">
          <select id={`${id}-k`} className="input" value={kind} onChange={(e) => setKind(e.target.value as AdminAccount["kind"])}>
            <option value="PERSONAL">Personal (one person)</option>
            <option value="SHARED">Shared</option>
          </select>
        </Field>
        <Field id={`${id}-i`} label="Check every (minutes)">
          <input id={`${id}-i`} className="input num" type="number" min={1} max={1440} value={interval} onChange={(e) => setInterval_(e.target.value)} />
        </Field>
        <div className="sm:col-span-2">
          <Field id={`${id}-s`} label="Signature (HTML)" hint="Added under new emails sent from this mailbox. Cleaned by the server when saved.">
            <textarea id={`${id}-s`} className="input min-h-24 font-mono text-xs" value={sig} onChange={(e) => setSig(e.target.value)} />
          </Field>
        </div>
        <div className="flex items-center gap-3 sm:col-span-2">
          <button type="submit" className="btn-primary px-4 py-2" disabled={busy}>
            Save details
          </button>
          <Notice msg={msg} />
        </div>
      </form>
    </Section>
  );
}

function RunsAndLog({ a }: { a: AdminAccount }) {
  const [runs, setRuns] = useState<SyncRun[] | null>(null);
  const [log, setLog] = useState<LogEntry[] | null>(null);
  const [tab, setTab] = useState<"runs" | "log">("runs");
  useEffect(() => {
    mailAdmin.runs(a.id).then(setRuns, () => setRuns([]));
    mailAdmin.log(a.id).then(setLog, () => setLog([]));
  }, [a.id, a.last_sync_attempt_at, a.status]);
  const chip = (on: boolean) => `chip cursor-pointer px-3 py-1 ${on ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:bg-subtle"}`;
  return (
    <Section title="History" className="lg:col-span-2">
      <div className="flex gap-1.5" role="group" aria-label="History view">
        <button type="button" aria-pressed={tab === "runs"} className={chip(tab === "runs")} onClick={() => setTab("runs")}>
          Sync runs
        </button>
        <button type="button" aria-pressed={tab === "log"} className={chip(tab === "log")} onClick={() => setTab("log")}>
          Security log
        </button>
      </div>
      {tab === "runs" ? (
        runs === null ? (
          <LoadingRows rows={3} cols={4} />
        ) : runs.length === 0 ? (
          <p className="text-sm text-ink-muted">No sync has run yet.</p>
        ) : (
          <div className="max-h-72 overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr>
                  <th className="th py-2">Started</th>
                  <th className="th py-2">Trigger</th>
                  <th className="th py-2">Result</th>
                  <th className="th py-2 text-right">New</th>
                  <th className="th py-2 text-right">Older</th>
                </tr>
              </thead>
              <tbody>
                {runs.slice(0, 20).map((r) => (
                  <tr key={r.id}>
                    <td className="td py-2">{whenFull(r.started_at)}</td>
                    <td className="td py-2">{r.trigger}</td>
                    <td className="td py-2">
                      <StatusChip tone={r.status === "OK" ? "success" : r.status === "RUNNING" ? "info" : "danger"}>{r.status}</StatusChip>
                      {r.error && <span className="ml-2 text-xs text-ink-muted">{r.error}</span>}
                    </td>
                    <td className="td num py-2 text-right">{r.new_messages}</td>
                    <td className="td num py-2 text-right">{r.backfilled}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : log === null ? (
        <LoadingRows rows={3} cols={3} />
      ) : log.length === 0 ? (
        <p className="text-sm text-ink-muted">Nothing recorded yet.</p>
      ) : (
        <ul className="max-h-72 divide-y divide-line overflow-auto text-sm">
          {log.slice(0, 40).map((e, i) => (
            <li key={`${e.at}-${i}`} className="flex flex-wrap gap-x-3 py-1.5">
              <span className="num text-ink-muted">{whenFull(e.at)}</span>
              <span className={e.success ? "" : "text-danger"}>{e.action.replace(/^mail\./, "").replace(/_/g, " ")}</span>
              <span className="text-ink-muted">by {e.by}</span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  );
}

function AddMailbox({ open, users, onClose, onAdded }: { open: boolean; users: CompanyUser[]; onClose: () => void; onAdded: (a: AdminAccount) => void }) {
  const id = useId();
  const [address, setAddress] = useState("");
  const [name, setName] = useState("");
  const [label, setLabel] = useState("");
  const [kind, setKind] = useState<"PERSONAL" | "SHARED">("PERSONAL");
  const [picked, setPicked] = useState<string[]>([]);
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      onAdded(await mailAdmin.create({ address: address.trim(), display_name: name.trim(), label: label.trim(), kind, user_ids: picked }));
      setAddress("");
      setName("");
      setLabel("");
      setPicked([]);
    } catch (err) {
      setMsg({ tone: "error", text: mailError(err) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal open={open} title="Add a mailbox" onClose={onClose} wide>
      <form className="space-y-4" onSubmit={(e) => void save(e)}>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field id={`${id}-a`} label="Email address">
            <input id={`${id}-a`} type="email" required className="input" value={address} onChange={(e) => setAddress(e.target.value)} placeholder="name@tamishatrading.com" />
          </Field>
          <Field id={`${id}-k`} label="Kind">
            <select id={`${id}-k`} className="input" value={kind} onChange={(e) => {
                const k = e.target.value as "PERSONAL" | "SHARED";
                setKind(k);
                setPicked((p) => p.slice(0, k === "PERSONAL" ? 1 : 20));
              }}>
              <option value="PERSONAL">Personal (one person)</option>
              <option value="SHARED">Shared (e.g. info@)</option>
            </select>
          </Field>
          <Field id={`${id}-n`} label="Sender name" hint="Shown to people who receive mail from it.">
            <input id={`${id}-n`} className="input" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Field id={`${id}-l`} label="Label" hint="What people see in Email, e.g. “Info Mail”.">
            <input id={`${id}-l`} className="input" value={label} maxLength={60} onChange={(e) => setLabel(e.target.value)} />
          </Field>
        </div>
        {isInfo(address) && <p className="alert-warning">{INFO_NOTE}</p>}
        <fieldset className="space-y-2">
          <legend className="label">Give it to</legend>
          <ul className="max-h-48 divide-y divide-line overflow-y-auto rounded-[var(--radius-ctl)] border border-line">
            {users.map((u) => (
              <li key={u.id} className="px-3 py-2 text-sm">
                <label className="flex items-center gap-2.5">
                  <input
                    type={kind === "PERSONAL" ? "radio" : "checkbox"}
                    name={`${id}-u`}
                    className="size-4 accent-brand-primary"
                    checked={picked.includes(u.id)}
                    onChange={(e) => setPicked((p) => (kind === "PERSONAL" ? [u.id] : e.target.checked ? [...p, u.id] : p.filter((x) => x !== u.id)))}
                  />
                  {u.name} <span className="text-ink-muted">{u.email}</span>
                </label>
              </li>
            ))}
          </ul>
        </fieldset>
        <p className="text-sm text-ink-muted">The mailbox starts as “Not configured”. Enter its Lark app password next; it shows as connected only after Lark accepts it.</p>
        <Notice msg={msg} />
        <div className="flex justify-end gap-2.5">
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="btn-primary" disabled={busy || !address.trim()}>
            Add mailbox
          </button>
        </div>
      </form>
    </Modal>
  );
}
