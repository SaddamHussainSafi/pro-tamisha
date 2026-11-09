import { QRCodeSVG } from "qrcode.react";
import { useState, type FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { api, type Session } from "../api";
import { useAuth } from "../auth";
import { AuthFrame } from "../components/AuthFrame";
import { RecoveryCodes } from "../components/RecoveryCodes";
import { returnTo } from "../lib/returnTo";
import { staffMessage } from "../lib/staff";

/** Second step of sign-in. Two-step sign-in is optional: this page appears only for people who
 *  turned it on, or (if the company ever requires it) to set it up at sign-in. */
export function MfaPage() {
  const { status, session, setSession, logout } = useAuth();
  const loc = useLocation();
  const nav = useNavigate();
  const [uri, setUri] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [codes, setCodes] = useState<{ codes: string[]; session: Session } | null>(null);

  if (codes && session)
    return (
      <AuthFrame title="Two-step sign-in is on" subtitle="One last thing before you continue.">
        <RecoveryCodes
          codes={codes.codes}
          email={session.employee.email}
          doneLabel="Continue"
          onDone={() => {
            setSession(codes.session);
            nav(returnTo(loc.state), { replace: true });
          }}
        />
      </AuthFrame>
    );
  if (status !== "authenticated" || !session) return <Navigate to="/login" replace state={loc.state} />;
  if (!session.mfa_required) return <Navigate to={returnTo(loc.state)} replace />;
  const setup = !!session.mfa_setup_required;

  async function start() {
    setError(null);
    try {
      setUri((await api.mfaEnroll()).otpauth_uri);
    } catch (err) {
      setError(staffMessage(err));
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (setup) {
        const r = await api.mfaConfirm(code);
        setCodes({ codes: r.recovery_codes, session: r.session });
      } else {
        setSession(await api.mfaVerify(useRecovery ? { recovery_code: code.trim() } : { code }));
      }
    } catch (err) {
      setError(staffMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const secret = uri ? new URL(uri).searchParams.get("secret") : null;
  const ready = useRecovery ? code.trim().length >= 8 : code.length === 6;

  return (
    <AuthFrame
      title="Two-step verification"
      subtitle={
        setup
          ? "Your company requires two-step sign-in. Set up an authenticator app to continue."
          : useRecovery
            ? "Enter one of the recovery codes you saved. Each code works once."
            : "Enter the 6-digit code from your authenticator app."
      }
    >
      {error && (
        <p role="alert" className="alert-danger">
          {error}
        </p>
      )}
      {setup && !uri ? (
        <button className="btn-primary w-full" onClick={() => void start()}>
          Set up authenticator
        </button>
      ) : (
        <form onSubmit={submit} className="space-y-4" aria-label="Two-step code">
          {uri && (
            <div className="space-y-2 text-center">
              <div className="inline-block rounded-ctl border border-line bg-card p-3">
                <QRCodeSVG value={uri} size={168} aria-label="Authenticator QR code" />
              </div>
              <p className="text-xs text-ink-muted">
                Can't scan? Enter this key:{" "}
                <code className="num break-all text-ink" data-testid="mfa-secret">
                  {secret}
                </code>
              </p>
            </div>
          )}
          <div>
            <label className="label" htmlFor="code">
              {useRecovery ? "Recovery code" : "6-digit code"}
            </label>
            {useRecovery ? (
              <input id="code" className="input num tracking-wider" autoComplete="off" spellCheck={false} maxLength={32} required value={code} onChange={(e) => setCode(e.target.value)} autoFocus />
            ) : (
              <input
                id="code"
                className="input num tracking-widest"
                inputMode="numeric"
                pattern="\d{6}"
                maxLength={6}
                autoComplete="one-time-code"
                required
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                autoFocus
              />
            )}
          </div>
          <button className="btn-primary w-full" disabled={busy || !ready}>
            {busy ? "Checking…" : "Verify"}
          </button>
          {!setup && (
            <button
              type="button"
              className="w-full text-sm font-medium text-brand-primary hover:underline"
              onClick={() => {
                setUseRecovery(!useRecovery);
                setCode("");
                setError(null);
              }}
            >
              {useRecovery ? "Use the authenticator app instead" : "Lost your phone? Use a recovery code"}
            </button>
          )}
        </form>
      )}
      <button className="btn-ghost w-full" onClick={() => void logout()}>
        Sign out
      </button>
    </AuthFrame>
  );
}
