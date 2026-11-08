import { useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { Wordmark } from "../components/Wordmark";

export function LoginPage() {
  const { status, session, setSession } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (status === "authenticated" && session) return <Navigate to={session.mfa_required ? "/mfa" : "/"} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setSession(await api.login(email, password));
    } catch (err) {
      setError(
        err instanceof ApiError && err.code === "ACCOUNT_LOCKED"
          ? "Too many failed attempts. Your account is temporarily locked."
          : err instanceof ApiError && err.code === "INVALID_CREDENTIALS"
            ? "Email or password is incorrect."
            : "Sign-in failed. Check your connection and try again.",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-full place-items-center p-6">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex justify-center">
          <Wordmark />
        </div>
        <form onSubmit={submit} className="card space-y-4 p-6" aria-labelledby="login-title">
          <div>
            <h1 id="login-title" className="text-lg font-semibold">Sign in</h1>
            <p className="text-sm text-ink-muted">Tamisha Trading Inc. staff only</p>
          </div>
          {error && <div role="alert" className="alert-danger">{error}</div>}
          <div>
            <label className="label" htmlFor="email">Email</label>
            <input id="email" className="input" type="email" autoComplete="username" required
              value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
          </div>
          <div>
            <label className="label" htmlFor="password">Password</label>
            <input id="password" className="input" type="password" autoComplete="current-password" required
              value={password} onChange={(e) => setPassword(e.target.value)} />
          </div>
          <button className="btn-primary w-full" type="submit" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </main>
  );
}
