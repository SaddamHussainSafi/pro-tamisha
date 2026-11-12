import { useState, type FormEvent, type ReactNode } from "react";
import { errorText } from "../lib/errors";
import { Link, Navigate, useLocation } from "react-router-dom";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { AuthHeading, AuthShell } from "../components/AuthFrame";
import { returnTo } from "../lib/returnTo";
import { Icon } from "../ui/Icon";

/** An input with a leading icon (and optional trailing control) in the sign-in style. */
function IconField({ icon, children, trailing }: { icon: ReactNode; children: ReactNode; trailing?: ReactNode }) {
  return (
    <div className="relative">
      <span aria-hidden className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-ink-muted">
        {icon}
      </span>
      {children}
      {trailing && <span className="absolute right-2 top-1/2 -translate-y-1/2">{trailing}</span>}
    </div>
  );
}

const LOCK = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M6 11h12v10H6zM8.5 11V7.5a3.5 3.5 0 017 0V11" />
  </svg>
);
const EYE_OFF = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 3l18 18M10.6 10.6a2 2 0 002.8 2.8M9.4 5.2A10.6 10.6 0 0112 5c6.5 0 10 7 10 7a17 17 0 01-3.2 4.1M6.6 6.6C3.9 8.4 2 12 2 12s3.5 7 10 7a9.6 9.6 0 004.4-1" />
  </svg>
);

export function LoginPage() {
  const { status, session, setSession } = useAuth();
  const loc = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const notice = (loc.state as { notice?: string } | null)?.notice;

  if (status === "authenticated" && session) return <Navigate to={session.mfa_required ? "/mfa" : returnTo(loc.state)} replace state={loc.state} />;

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
          : err instanceof ApiError && err.code === "TOO_MANY_ATTEMPTS"
            ? "Too many failed sign-ins from this network. Wait a few minutes and try again."
            : err instanceof ApiError && err.code === "INVALID_CREDENTIALS"
              ? "Email or password is incorrect."
              : errorText(err),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthShell labelledBy="login-title">
      <AuthHeading id="login-title" title="Welcome back" subtitle="Sign in to your Tamisha Trading account" />
      <form onSubmit={submit} className="space-y-5" aria-labelledby="login-title">
        {notice && !error && (
          <p role="status" className="alert-success">
            {notice}
          </p>
        )}
        {error && (
          <div role="alert" className="alert-danger">
            {error}
          </div>
        )}
        <div>
          <label className="label" htmlFor="email">
            Email
          </label>
          <IconField icon={<Icon name="mail" size={20} />}>
            <input
              id="email"
              className="input h-14 pl-12 text-base"
              type="email"
              autoComplete="username"
              placeholder="you@tamishatrading.com"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoFocus
            />
          </IconField>
        </div>
        <div>
          <label className="label" htmlFor="password">
            Password
          </label>
          <IconField
            icon={LOCK}
            trailing={
              <button
                type="button"
                className="grid size-10 cursor-pointer place-items-center rounded-full text-ink-muted transition-colors duration-150 hover:bg-subtle hover:text-ink"
                aria-pressed={show}
                aria-controls="password"
                onClick={() => setShow((s) => !s)}
                title={show ? "Hide password" : "Show password"}
              >
                {show ? EYE_OFF : <Icon name="eye" size={20} />}
                {/* Named by its content, not aria-label, so "Password" still finds only the field. */}
                <span className="sr-only">{show ? "Hide the typed characters" : "Show the typed characters"}</span>
              </button>
            }
          >
            <input
              id="password"
              className="input h-14 pl-12 pr-14 text-base"
              type={show ? "text" : "password"}
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </IconField>
          <div className="mt-2 flex justify-end">
            <Link to="/forgot-password" className="rounded-full text-sm font-medium text-brand-primary underline-offset-2 hover:underline">
              Forgot password? Reset it
            </Link>
          </div>
        </div>
        <button className="btn-accent h-14 w-full text-base font-semibold" type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
      <p className="mt-6 text-center text-sm text-ink-muted">
        New to Pro Trading? Ask your administrator for an invitation.
      </p>
    </AuthShell>
  );
}
