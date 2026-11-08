import { QRCodeSVG } from "qrcode.react";
import { useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { Wordmark } from "../components/Wordmark";

export function MfaPage() {
  const { status, session, setSession, logout } = useAuth();
  const [uri, setUri] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (status !== "authenticated" || !session) return <Navigate to="/login" replace />;
  if (!session.mfa_required) return <Navigate to="/" replace />;

  async function enroll() {
    setError(null);
    try {
      setUri((await api.mfaEnroll()).otpauth_uri);
    } catch {
      setError("Could not start enrollment. Try again.");
    }
  }

  async function verify(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setSession(await api.mfaVerify(code));
    } catch (err) {
      setError(err instanceof ApiError && err.code === "MFA_INVALID" ? "That code is invalid or already used." : "Verification failed.");
    } finally {
      setBusy(false);
    }
  }

  const secret = uri ? new URL(uri).searchParams.get("secret") : null;
  const needsEnroll = !session.mfa_enrolled && !uri;

  return (
    <main className="grid min-h-full place-items-center p-6">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex justify-center"><Wordmark /></div>
        <div className="card space-y-4 p-6">
          <div>
            <h1 className="text-lg font-semibold">Two-step verification</h1>
            <p className="text-sm text-ink-muted">
              Your role has privileged access. Verify with your authenticator app to continue.
            </p>
          </div>
          {error && <div role="alert" className="alert-danger">{error}</div>}
          {needsEnroll ? (
            <button className="btn-primary w-full" onClick={enroll}>Set up authenticator</button>
          ) : (
            <form onSubmit={verify} className="space-y-4">
              {uri && (
                <div className="space-y-2 text-center">
                  <div className="inline-block rounded-lg border border-line bg-card p-3">
                    <QRCodeSVG value={uri} size={168} aria-label="Authenticator QR code" />
                  </div>
                  <p className="text-xs text-ink-muted">
                    Can't scan? Enter this key: <code className="num break-all text-ink" data-testid="mfa-secret">{secret}</code>
                  </p>
                </div>
              )}
              <div>
                <label className="label" htmlFor="code">6-digit code</label>
                <input id="code" className="input num tracking-widest" inputMode="numeric" pattern="\d{6}"
                  maxLength={6} autoComplete="one-time-code" required value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))} autoFocus />
              </div>
              <button className="btn-primary w-full" disabled={busy || code.length !== 6}>Verify</button>
            </form>
          )}
          <button className="btn-ghost w-full" onClick={() => void logout()}>Sign out</button>
        </div>
      </div>
    </main>
  );
}
