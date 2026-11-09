import { useEffect, useState, type FormEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, ApiError, type LinkInfo, type LinkPurpose } from "../api";
import { AuthFrame, NewPasswordFields } from "../components/AuthFrame";
import { passwordProblem, staffMessage, tokenFromHash } from "../lib/staff";

/* Signed-out account screens: forgot password, reset password, accept invitation.
   The link token travels in the URL fragment (#token=…), so it never reaches a server log. */

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())) return setError("Enter the email you sign in with.");
    setBusy(true);
    try {
      setSent((await api.forgotPassword(email.trim())).message);
    } catch (err) {
      setError(staffMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthFrame title="Forgot your password?" subtitle="We'll email you a link to choose a new one.">
      {sent ? (
        <>
          <p role="status" className="alert-success">
            {sent}
          </p>
          <Link className="btn-ghost w-full" to="/login">
            Back to sign in
          </Link>
        </>
      ) : (
        <form onSubmit={submit} className="space-y-4" noValidate aria-label="Forgot password">
          {error && (
            <p role="alert" className="alert-danger">
              {error}
            </p>
          )}
          <div>
            <label className="label" htmlFor="fp-email">
              Email
            </label>
            <input id="fp-email" className="input" type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
          </div>
          <button className="btn-primary w-full" disabled={busy}>
            {busy ? "Sending…" : "Send reset link"}
          </button>
          <p className="text-center text-sm">
            <Link to="/login" className="font-medium text-brand-primary hover:underline">
              Back to sign in
            </Link>
          </p>
        </form>
      )}
    </AuthFrame>
  );
}

/** Reads the token from the fragment, drops it from the address bar, and asks the API whose
 *  link it is. A new fragment (another link opened in the same tab) is picked up too. */
function useLink(purpose: LinkPurpose) {
  const loc = useLocation();
  const nav = useNavigate();
  const [token, setToken] = useState(() => tokenFromHash(loc.hash));
  const [info, setInfo] = useState<LinkInfo | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "bad">(token ? "loading" : "bad");
  useEffect(() => {
    const fresh = tokenFromHash(loc.hash);
    if (!fresh) return;
    setToken(fresh);
    nav(loc.pathname, { replace: true }); // keep the token out of history
  }, [loc.hash, loc.pathname, nav]);
  useEffect(() => {
    if (!token) return;
    let live = true;
    setState("loading");
    api
      .inspectLink(token, purpose)
      .then((i) => live && (setInfo(i), setState("ok")))
      .catch(() => live && setState("bad"));
    return () => {
      live = false;
    };
  }, [token, purpose]);
  return { token, info, state };
}

function SetPasswordPage({ purpose }: { purpose: LinkPurpose }) {
  const nav = useNavigate();
  const { token, info, state } = useLink(purpose);
  const [pw, setPw] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const invite = purpose === "INVITE";

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const problem = passwordProblem(pw, info?.email);
    if (problem) return setError(problem);
    if (pw !== again) return setError("The two passwords don't match.");
    if (!token) return;
    setBusy(true);
    try {
      await (invite ? api.acceptInvite(token, pw) : api.resetPassword(token, pw));
      nav("/login", { replace: true, state: { notice: invite ? "Your password is set. Sign in to start." : "Password changed. Sign in with your new password." } });
    } catch (err) {
      setError(err instanceof ApiError && err.code === "WEAK_PASSWORD" ? "Choose a less guessable password (not your email, not repeated characters)." : staffMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const title = invite ? "Welcome to Pro Trading" : "Choose a new password";
  if (state === "loading")
    return (
      <AuthFrame title={title}>
        <p role="status" className="text-sm text-ink-muted">
          Checking your link…
        </p>
      </AuthFrame>
    );
  if (state === "bad")
    return (
      <AuthFrame title={invite ? "Invitation link not valid" : "Reset link not valid"}>
        <p role="alert" className="alert-danger">
          {invite ? "This invitation is invalid, already used or expired. Ask your Super Admin to send a new one." : "This link is invalid, already used or expired."}
        </p>
        {invite ? (
          <Link className="btn-ghost w-full" to="/login">
            Go to sign in
          </Link>
        ) : (
          <Link className="btn-primary w-full" to="/forgot-password">
            Get a new link
          </Link>
        )}
      </AuthFrame>
    );
  return (
    <AuthFrame
      title={title}
      subtitle={
        invite ? (
          <>
            Hello {info?.display_name}. Choose the password you'll use with <strong className="text-ink">{info?.email}</strong>.
          </>
        ) : (
          <>For {info?.email}. You'll be signed out on every device.</>
        )
      }
    >
      <form onSubmit={submit} className="space-y-4" noValidate aria-label={invite ? "Set your password" : "Reset password"}>
        {error && (
          <p role="alert" className="alert-danger">
            {error}
          </p>
        )}
        <input type="email" autoComplete="username" value={info?.email ?? ""} readOnly hidden />
        <NewPasswordFields value={pw} again={again} onChange={setPw} onAgain={setAgain} label={invite ? "Password" : "New password"} />
        <button className="btn-primary w-full" disabled={busy || !pw || !again}>
          {busy ? "Saving…" : invite ? "Set password" : "Change password"}
        </button>
      </form>
    </AuthFrame>
  );
}

export const ResetPasswordPage = () => <SetPasswordPage purpose="PASSWORD_RESET" />;
export const AcceptInvitePage = () => <SetPasswordPage purpose="INVITE" />;
