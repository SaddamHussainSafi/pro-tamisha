import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, setUnauthorizedHandler, type Session } from "./api";

type AuthState = {
  status: "loading" | "anonymous" | "authenticated";
  session: Session | null;
  /** The sign-in ended while someone was working: the page stays, a dialog asks to sign in again. */
  expired: boolean;
  setSession: (s: Session | null) => void;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
};

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [status, setStatus] = useState<AuthState["status"]>("loading");
  const [expired, setExpired] = useState(false);
  const current = useRef<Session | null>(null);

  const apply = useCallback((s: Session | null) => {
    current.current = s;
    setSession(s);
    setExpired(false);
    setStatus(s ? "authenticated" : "anonymous");
  }, []);

  const refresh = useCallback(async () => {
    try {
      apply(await api.session());
    } catch {
      // Dev builds sign in automatically when the API allows it (DEV_AUTO_LOGIN_EMAIL).
      // Automated browsers (Playwright) keep the real login so the e2e tests still cover it.
      if (import.meta.env.DEV && !navigator.webdriver) {
        try {
          apply(await api.devLogin());
          return;
        } catch {
          /* not enabled: fall through to the login page */
        }
      }
      apply(null);
    }
  }, [apply]);

  const logout = useCallback(async () => {
    await api.logout().catch(() => undefined);
    apply(null);
  }, [apply]);

  useEffect(() => {
    // Signed in and working: keep the page (and what was typed) and ask to sign in again.
    // Not signed in yet (first load, sign-in pages): just show the sign-in page.
    setUnauthorizedHandler(() => (current.current && !current.current.mfa_required ? setExpired(true) : apply(null)));
    void refresh();
  }, [apply, refresh]);

  const value = useMemo(
    () => ({ status, session, expired, setSession: apply, refresh, logout }),
    [status, session, expired, apply, refresh, logout],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
