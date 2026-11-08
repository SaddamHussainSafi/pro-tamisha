import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, setUnauthorizedHandler, type Session } from "./api";

type AuthState = {
  status: "loading" | "anonymous" | "authenticated";
  session: Session | null;
  setSession: (s: Session | null) => void;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
};

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [status, setStatus] = useState<AuthState["status"]>("loading");

  const apply = useCallback((s: Session | null) => {
    setSession(s);
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
    setUnauthorizedHandler(() => apply(null));
    void refresh();
  }, [apply, refresh]);

  const value = useMemo(
    () => ({ status, session, setSession: apply, refresh, logout }),
    [status, session, apply, refresh, logout],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth outside AuthProvider");
  return v;
}
