import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { errorText } from "../lib/errors";

/**
 * Shown over the page when the sign-in ends while someone is working (timed out, signed out in
 * another tab, password changed). The page stays underneath with what was typed; signing in again
 * here carries on from the same place.
 */
export function SessionExpiredDialog() {
  const { expired, session, setSession, logout } = useAuth();
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const nav = useNavigate();
  const loc = useLocation();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const email = session?.employee.email ?? "";

  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (expired && !d.open) d.showModal();
    if (!expired && d.open) d.close();
  }, [expired]);

  // Development builds sign in automatically (as on first load); then nothing needs typing.
  useEffect(() => {
    if (!expired || !import.meta.env.DEV || navigator.webdriver) return;
    api.devLogin().then(setSession, () => undefined);
  }, [expired, setSession]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setSession(await api.login(email, password));
      setPassword("");
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function other() {
    await logout();
    nav("/login", { replace: true, state: { from: loc.pathname + loc.search } });
  }

  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      data-testid="session-expired"
      onCancel={(e) => e.preventDefault()}
      className="m-auto w-[min(28rem,calc(100vw-2rem))] rounded-card border-0 bg-card p-0 text-ink shadow-[0_8px_24px_rgb(24_24_27/0.12)] backdrop:bg-ink/40"
    >
      <form onSubmit={submit}>
        <div className="space-y-3 p-6">
          <h2 id={titleId} className="text-lg font-semibold">Your sign-in has ended</h2>
          <p className="text-sm text-ink-muted">
            For your safety you were signed out (it timed out, or you signed out somewhere else). Sign in again to carry on — this page and anything you typed on it stay as they are.
          </p>
          {error && <p role="alert" className="alert-danger">{error}</p>}
          <div className="text-sm">Signed in as <strong>{email}</strong></div>
          <label className="block space-y-1.5 text-sm">
            <span className="font-medium">Password</span>
            <input className="input w-full" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} autoFocus />
          </label>
        </div>
        <div className="flex flex-wrap justify-end gap-2 border-t border-line px-6 py-4">
          <button type="button" className="btn-ghost" onClick={() => void other()}>Use a different account</button>
          <button type="submit" className="btn-primary" disabled={busy || !password}>{busy ? "Signing in…" : "Sign in"}</button>
        </div>
      </form>
    </dialog>
  );
}
