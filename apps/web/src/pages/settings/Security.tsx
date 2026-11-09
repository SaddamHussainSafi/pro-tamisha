import { useCallback, useEffect, useState } from "react";
import { api, type EventKind, type SecurityEvent, type SecuritySettings, type SessionInfo } from "../../api";
import { staffMessage } from "../../lib/staff";
import { CardTitle, ConfirmDialog, ErrorState, LoadingRows, StatusChip } from "../../ui/primitives";
import { EventList, Notice, SessionList, type Msg } from "./shared";

const PAGE = 30;
const KINDS: { value: EventKind; label: string }[] = [
  { value: "all", label: "All" },
  { value: "sign_in", label: "Sign-ins" },
  { value: "account", label: "Account changes" },
  { value: "denied", label: "Refused" },
];

/** Security & sessions (Super Admin): sign-in rules, who is signed in, security events. */
export function Security() {
  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
      <div className="space-y-5">
        <Policy />
        <Sessions />
      </div>
      <Events />
    </div>
  );
}

function Policy() {
  const [s, setS] = useState<SecuritySettings | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    api.securitySettings().then(setS, () => setFailed(true));
  }, []);
  const rows: [string, string][] = s
    ? [
        ["Session length", `${Math.round(s.session_ttl_minutes / 60)} hours at most`],
        ["Idle sign-out", `after ${s.session_idle_minutes} minutes without activity`],
        ["Wrong passwords", `${s.login_max_failures} in a row lock the account for ${s.login_lockout_minutes} minutes`],
        ["Per network", `${s.login_ip_max_failures} failed sign-ins per ${s.login_ip_window_minutes} minutes`],
        ["Passwords", `at least ${s.password_min_length} characters, stored as Argon2id hashes`],
        ["Invitation links", `work once, expire after ${s.invite_ttl_hours} hours`],
        ["Reset links", `work once, expire after ${s.password_reset_ttl_minutes} minutes`],
      ]
    : [];
  return (
    <section className="card space-y-4 p-6" aria-labelledby="sec-policy">
      <CardTitle id="sec-policy" title="Sign-in rules" />
      {failed ? (
        <ErrorState title="Couldn't load the sign-in rules" />
      ) : !s ? (
        <LoadingRows rows={4} cols={2} />
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-ctl border border-line p-4">
            <div className="min-w-0">
              <p className="font-medium">Two-step sign-in</p>
              <p className="text-sm text-ink-muted">{s.two_step_required ? "Required for everyone." : "Optional: each person can turn it on in My account."}</p>
            </div>
            {s.two_step_required ? <StatusChip tone="success">Required</StatusChip> : <StatusChip tone="neutral">Optional</StatusChip>}
          </div>
          <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[9rem_1fr]">
            {rows.map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-ink-muted">{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        </>
      )}
    </section>
  );
}

function Sessions() {
  const [rows, setRows] = useState<SessionInfo[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  const [target, setTarget] = useState<SessionInfo | null>(null);
  const load = useCallback(async () => {
    try {
      setRows(await api.securitySessions());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);
  useEffect(() => void load(), [load]);

  async function revoke(s: SessionInfo) {
    setTarget(null);
    setBusy(true);
    setMsg(null);
    try {
      await api.revokeSecuritySession(s.id);
      setMsg({ tone: "ok", text: `${s.employee_name ?? "That person"} was signed out of that device.` });
      await load();
    } catch (err) {
      setMsg({ tone: "error", text: staffMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card space-y-4 p-6" aria-labelledby="sec-sessions">
      <CardTitle id="sec-sessions" title={rows ? `Active sessions (${rows.length})` : "Active sessions"} />
      <Notice msg={msg} />
      {failed ? <ErrorState title="Couldn't load sessions" onRetry={() => void load()} /> : rows === null ? <LoadingRows rows={3} cols={2} /> : <SessionList rows={rows} busy={busy} showPerson onRevoke={setTarget} />}
      <ConfirmDialog open={target !== null} title={`Sign out ${target?.employee_name ?? "this session"}?`} confirmLabel="Sign out" danger onClose={() => setTarget(null)} onConfirm={() => target && void revoke(target)}>
        <p>That browser has to sign in again. Their other sessions stay open.</p>
      </ConfirmDialog>
    </section>
  );
}

function Events() {
  const [kind, setKind] = useState<EventKind>("all");
  const [failedOnly, setFailedOnly] = useState(false);
  const [rows, setRows] = useState<SecurityEvent[] | null>(null);
  const [more, setMore] = useState(false);
  const [failed, setFailed] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  const load = useCallback(async () => {
    setRows(null);
    try {
      const r = await api.securityEvents({ kind, failed_only: failedOnly, limit: PAGE });
      setRows(r);
      setMore(r.length === PAGE);
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [kind, failedOnly]);
  useEffect(() => void load(), [load]);

  async function loadMore() {
    const last = rows?.[rows.length - 1];
    if (!rows || !last) return;
    setLoadingMore(true);
    try {
      const r = await api.securityEvents({ kind, failed_only: failedOnly, limit: PAGE, before: last.created_at });
      setRows([...rows, ...r]);
      setMore(r.length === PAGE);
    } catch {
      setFailed(true);
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <section className="card space-y-4 p-6" aria-labelledby="sec-events">
      <CardTitle id="sec-events" title="Security events" />
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Event type">
          {KINDS.map((k) => (
            <button
              key={k.value}
              type="button"
              aria-pressed={kind === k.value}
              onClick={() => setKind(k.value)}
              className={`chip cursor-pointer px-3.5 py-1.5 text-[13px] transition-colors ${kind === k.value ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:bg-subtle"}`}
            >
              {k.label}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2 text-sm sm:ml-auto">
          <input type="checkbox" className="size-4 accent-brand-primary" checked={failedOnly} onChange={(e) => setFailedOnly(e.target.checked)} />
          Failures only
        </label>
      </div>
      {failed ? (
        <ErrorState title="Couldn't load security events" onRetry={() => void load()} />
      ) : rows === null ? (
        <LoadingRows rows={6} cols={2} />
      ) : (
        <>
          <EventList rows={rows} showPerson label="Security events" />
          {more && (
            <div className="flex justify-center">
              <button type="button" className="btn-ghost" disabled={loadingMore} onClick={() => void loadMore()}>
                {loadingMore ? "Loading…" : "Load more"}
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
