import { QRCodeSVG } from "qrcode.react";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, type SecurityEvent, type SessionInfo } from "../../api";
import { useAuth } from "../../auth";
import { NewPasswordFields } from "../../components/AuthFrame";
import { RecoveryCodes } from "../../components/RecoveryCodes";
import { groupPermissions, passwordProblem, roleLabel, staffMessage } from "../../lib/staff";
import { CardTitle, ConfirmDialog, ErrorState, LoadingRows, StatusChip } from "../../ui/primitives";
import { EventList, Field, Notice, SessionList, type Msg } from "./shared";

export function MyAccount() {
  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
      <Profile />
      <ChangePassword />
      <TwoStep />
      <MySessions />
      <MyActivity />
    </div>
  );
}

function Profile() {
  const { session, refresh } = useAuth();
  const [name, setName] = useState(session?.employee.display_name ?? "");
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  if (!session) return null;
  const e = session.employee;
  const areas = groupPermissions(session.permissions.map((code) => ({ code }))).map((g) => g.area);

  async function save(ev: FormEvent) {
    ev.preventDefault();
    setMsg(null);
    if (!name.trim()) return setMsg({ tone: "error", text: "Enter your name." });
    setBusy(true);
    try {
      await api.updateMe({ display_name: name.trim() });
      await refresh();
      setMsg({ tone: "ok", text: "Name saved." });
    } catch (err) {
      setMsg({ tone: "error", text: staffMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card space-y-4 p-6" aria-labelledby="set-account">
      <CardTitle id="set-account" title="My account" />
      <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-[10rem_1fr]">
        <dt className="text-ink-muted">Email</dt>
        <dd className="min-w-0 break-words">{e.email}</dd>
        <dt className="text-ink-muted">Role</dt>
        <dd>{(session.roles ?? []).map(roleLabel).join(", ") || "No role yet"}</dd>
        <dt className="text-ink-muted">You can work with</dt>
        <dd>{areas.length ? areas.join(", ") : "Nothing yet. Ask your Super Admin for a role."}</dd>
      </dl>
      <form className="space-y-3" onSubmit={save} aria-label="Your name">
        <Field id="me-name" label="Name">
          <input id="me-name" className="input" value={name} maxLength={200} autoComplete="name" onChange={(ev) => setName(ev.target.value)} />
        </Field>
        <Notice msg={msg} />
        <div className="flex justify-end">
          <button className="btn-primary" disabled={busy || name.trim() === e.display_name}>
            Save name
          </button>
        </div>
      </form>
    </section>
  );
}

function ChangePassword() {
  const { session } = useAuth();
  const [pw, setPw] = useState({ current: "", next: "", again: "" });
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  if (!session) return null;

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    setMsg(null);
    if (!pw.current) return setMsg({ tone: "error", text: "Enter your current password." });
    const problem = passwordProblem(pw.next, session?.employee.email);
    if (problem) return setMsg({ tone: "error", text: problem });
    if (pw.next !== pw.again) return setMsg({ tone: "error", text: "The two new passwords don't match." });
    setBusy(true);
    try {
      await api.changePassword({ current_password: pw.current, new_password: pw.next });
      setPw({ current: "", next: "", again: "" });
      setMsg({ tone: "ok", text: "Password changed. Other devices were signed out." });
    } catch (err) {
      setMsg({ tone: "error", text: staffMessage(err).replace("Your password is not right.", "Your current password is not right.") });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card space-y-4 p-6" aria-labelledby="set-password">
      <CardTitle id="set-password" title="Change password" />
      <form className="space-y-3" onSubmit={submit} aria-labelledby="set-password" noValidate>
        <input type="email" autoComplete="username" value={session.employee.email} readOnly hidden />
        <Field id="pw-current" label="Current password">
          <input id="pw-current" type="password" autoComplete="current-password" className="input" value={pw.current} onChange={(ev) => setPw({ ...pw, current: ev.target.value })} />
        </Field>
        <NewPasswordFields idPrefix="pw" value={pw.next} again={pw.again} onChange={(v) => setPw({ ...pw, next: v })} onAgain={(v) => setPw({ ...pw, again: v })} />
        <Notice msg={msg} />
        <div className="flex justify-end">
          <button className="btn-primary" disabled={busy || !pw.current || !pw.next}>
            {busy ? "Changing…" : "Change password"}
          </button>
        </div>
      </form>
    </section>
  );
}

type TwoStepStep = { kind: "idle" } | { kind: "password" } | { kind: "scan"; uri: string } | { kind: "codes"; codes: string[] } | { kind: "change"; what: "off" | "codes" };

function TwoStep() {
  const { session, refresh } = useAuth();
  const [step, setStep] = useState<TwoStepStep>({ kind: "idle" });
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  if (!session) return null;
  const on = session.mfa_enrolled;

  const reset = (next: TwoStepStep = { kind: "idle" }) => {
    setStep(next);
    setPassword("");
    setCode("");
    setUseRecovery(false);
  };

  async function run(fn: () => Promise<void>) {
    setMsg(null);
    setBusy(true);
    try {
      await fn();
    } catch (err) {
      setMsg({ tone: "error", text: staffMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  const factor = () => (useRecovery ? { recovery_code: code.trim() } : { code });
  const secret = step.kind === "scan" ? new URL(step.uri).searchParams.get("secret") : null;

  return (
    <section className="card space-y-4 p-6" aria-labelledby="set-2fa">
      <CardTitle id="set-2fa" title="Two-step sign-in" action={on ? <StatusChip tone="success">On</StatusChip> : <StatusChip tone="neutral">Off</StatusChip>} />
      <p className="text-sm text-ink-muted">
        Optional. When it's on, signing in also asks for a 6-digit code from an authenticator app on your phone (Google Authenticator, Microsoft Authenticator, 1Password…). It stops anyone who learns your password.
      </p>
      <Notice msg={msg} />

      {step.kind === "idle" && !on && (
        <button type="button" className="btn-primary" onClick={() => reset({ kind: "password" })}>
          Turn on two-step sign-in
        </button>
      )}
      {step.kind === "idle" && on && (
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn-ghost" onClick={() => reset({ kind: "change", what: "codes" })}>
            New recovery codes
          </button>
          <button type="button" className="btn-ghost" onClick={() => reset({ kind: "change", what: "off" })}>
            Turn off
          </button>
        </div>
      )}

      {step.kind === "password" && (
        <form
          className="space-y-3"
          aria-label="Confirm your password"
          onSubmit={(ev) => {
            ev.preventDefault();
            void run(async () => {
              const r = await api.mfaEnroll(password);
              reset({ kind: "scan", uri: r.otpauth_uri });
            });
          }}
        >
          <Field id="tf-pw" label="Your password" hint="Enter it again to turn this on.">
            <input id="tf-pw" type="password" autoComplete="current-password" className="input" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus />
          </Field>
          <div className="flex justify-end gap-2">
            <button type="button" className="btn-ghost" onClick={() => reset()}>
              Cancel
            </button>
            <button className="btn-primary" disabled={busy || !password}>
              Continue
            </button>
          </div>
        </form>
      )}

      {step.kind === "scan" && (
        <form
          className="space-y-4"
          aria-label="Scan and confirm"
          onSubmit={(ev) => {
            ev.preventDefault();
            void run(async () => {
              const r = await api.mfaConfirm(code);
              reset({ kind: "codes", codes: r.recovery_codes });
            });
          }}
        >
          <ol className="list-decimal space-y-1 pl-5 text-sm">
            <li>Open your authenticator app and scan this code.</li>
            <li>Type the 6-digit code it shows.</li>
          </ol>
          <div className="flex flex-col items-center gap-2 sm:flex-row sm:items-start">
            <div className="rounded-ctl border border-line bg-card p-3">
              <QRCodeSVG value={step.uri} size={160} aria-label="Authenticator QR code" />
            </div>
            <p className="text-sm text-ink-muted">
              Can't scan? Enter this key:{" "}
              <code className="num break-all text-ink" data-testid="mfa-secret">
                {secret}
              </code>
            </p>
          </div>
          <Field id="tf-code" label="6-digit code">
            <input id="tf-code" className="input num tracking-widest sm:w-48" inputMode="numeric" maxLength={6} autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} />
          </Field>
          <div className="flex justify-end gap-2">
            <button type="button" className="btn-ghost" onClick={() => reset()}>
              Cancel
            </button>
            <button className="btn-primary" disabled={busy || code.length !== 6}>
              Turn on
            </button>
          </div>
        </form>
      )}

      {step.kind === "codes" && (
        <RecoveryCodes
          codes={step.codes}
          email={session.employee.email}
          onDone={() => {
            reset();
            void refresh();
            setMsg({ tone: "ok", text: "Two-step sign-in is on. Your other devices were signed out." });
          }}
        />
      )}

      {step.kind === "change" && (
        <form
          className="space-y-3"
          aria-label={step.what === "off" ? "Turn off two-step sign-in" : "Make new recovery codes"}
          onSubmit={(ev) => {
            ev.preventDefault();
            void run(async () => {
              if (step.what === "off") {
                await api.mfaDisable({ password, ...factor() });
                reset();
                await refresh();
                setMsg({ tone: "ok", text: "Two-step sign-in is off." });
              } else {
                const r = await api.mfaRecoveryCodes({ password, ...factor() });
                reset({ kind: "codes", codes: r.recovery_codes });
              }
            });
          }}
        >
          <Field id="tc-pw" label="Your password">
            <input id="tc-pw" type="password" autoComplete="current-password" className="input" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus />
          </Field>
          <Field id="tc-code" label={useRecovery ? "Recovery code" : "6-digit code from your app"}>
            <input
              id="tc-code"
              className="input num tracking-wider sm:w-64"
              inputMode={useRecovery ? "text" : "numeric"}
              maxLength={useRecovery ? 32 : 6}
              autoComplete="one-time-code"
              value={code}
              onChange={(e) => setCode(useRecovery ? e.target.value : e.target.value.replace(/\D/g, ""))}
            />
          </Field>
          <button type="button" className="text-sm font-medium text-brand-primary hover:underline" onClick={() => (setUseRecovery(!useRecovery), setCode(""))}>
            {useRecovery ? "Use the app code instead" : "Use a recovery code instead"}
          </button>
          <div className="flex justify-end gap-2">
            <button type="button" className="btn-ghost" onClick={() => reset()}>
              Cancel
            </button>
            <button className={step.what === "off" ? "btn bg-danger text-white hover:opacity-90" : "btn-primary"} disabled={busy || !password || !code}>
              {step.what === "off" ? "Turn off" : "Make new codes"}
            </button>
          </div>
        </form>
      )}
    </section>
  );
}

function MySessions() {
  const [rows, setRows] = useState<SessionInfo[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState(false);
  const [confirmAll, setConfirmAll] = useState(false);
  const load = useCallback(async () => {
    try {
      setRows(await api.mySessions());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);
  useEffect(() => void load(), [load]);

  async function run(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    setMsg(null);
    try {
      await fn();
      setMsg({ tone: "ok", text: ok });
      await load();
    } catch (err) {
      setMsg({ tone: "error", text: staffMessage(err) });
    } finally {
      setBusy(false);
    }
  }
  const others = (rows ?? []).filter((r) => !r.current).length;

  return (
    <section className="card space-y-4 p-6" aria-labelledby="set-sessions">
      <CardTitle
        id="set-sessions"
        title="Where you're signed in"
        action={
          others > 0 && (
            <button type="button" className="btn-ghost px-3 py-1.5 text-xs" disabled={busy} onClick={() => setConfirmAll(true)}>
              Sign out other devices
            </button>
          )
        }
      />
      <Notice msg={msg} />
      {failed ? <ErrorState title="Couldn't load your sessions" onRetry={() => void load()} /> : rows === null ? <LoadingRows rows={2} cols={2} /> : <SessionList rows={rows} busy={busy} onRevoke={(s) => void run(() => api.revokeMySession(s.id), "That device was signed out.")} />}
      <ConfirmDialog open={confirmAll} title="Sign out your other devices?" confirmLabel="Sign out others" onClose={() => setConfirmAll(false)} onConfirm={() => (setConfirmAll(false), void run(() => api.revokeMyOtherSessions(), "Your other devices were signed out."))}>
        <p>Every other browser and device has to sign in again. This one stays signed in.</p>
      </ConfirmDialog>
    </section>
  );
}

function MyActivity() {
  const [rows, setRows] = useState<SecurityEvent[] | null>(null);
  const [failed, setFailed] = useState(false);
  const load = useCallback(async () => {
    try {
      setRows(await api.myActivity());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);
  useEffect(() => void load(), [load]);
  return (
    <section className="card space-y-4 p-6" aria-labelledby="set-activity">
      <CardTitle id="set-activity" title="Recent sign-in activity" />
      <p className="text-sm text-ink-muted">If you see something you don't recognize, change your password and tell your Super Admin.</p>
      {failed ? <ErrorState title="Couldn't load your activity" onRetry={() => void load()} /> : rows === null ? <LoadingRows rows={3} cols={2} /> : <EventList rows={rows.slice(0, 12)} label="Your recent sign-in activity" />}
    </section>
  );
}
