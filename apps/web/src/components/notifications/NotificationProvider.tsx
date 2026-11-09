import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../auth";
import { desktopState, safeLink, shouldShowDesktop, type DesktopState } from "../../lib/notifications";
import { notificationsApi, type LiveNotification, type NotificationPrefs, type NotificationSummary } from "../../lib/notificationsApi";

/*
 * One live connection per browser, not per tab: the tab holding a Web Lock ("leader") opens the
 * SSE stream and relays each event to the other tabs over a BroadcastChannel. When the leader
 * tab closes, the lock passes to another tab, which reconnects and resumes after the last seq it
 * saw, so nothing is missed. (Browsers allow only ~6 connections per host over HTTP/1.1.)
 */

export type LiveEvent = { type: "notification"; item: LiveNotification } | { type: "resync" };
type Msg =
  | { type: "notification"; data: LiveNotification }
  | { type: "unread" | "ready"; data: NotificationSummary }
  | { type: "resync" }
  | { type: "status"; live: boolean }
  | { type: "hello" };

type Ctx = {
  summary: NotificationSummary | null;
  live: boolean;
  prefs: NotificationPrefs | null;
  setPrefs: (p: NotificationPrefs) => void;
  refreshSummary: () => void;
  subscribe: (fn: (e: LiveEvent) => void) => () => void;
  desktop: DesktopState;
  requestDesktop: () => Promise<DesktopState>;
  banner: LiveNotification | null;
  dismissBanner: () => void;
};

const NotifyCtx = createContext<Ctx | null>(null);

// eslint-disable-next-line react-refresh/only-export-components
export function useNotifications(): Ctx {
  const v = useContext(NotifyCtx);
  if (!v) throw new Error("useNotifications outside NotificationProvider");
  return v;
}

/** A short, soft two-note chime made in the browser (no audio file). */
function chime() {
  try {
    const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return;
    const ac = new Ctor();
    const t = ac.currentTime;
    [880, 1318.5].forEach((f, i) => {
      const o = ac.createOscillator();
      const g = ac.createGain();
      o.type = "sine";
      o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, t + i * 0.09);
      g.gain.exponentialRampToValueAtTime(0.06, t + i * 0.09 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + i * 0.09 + 0.35);
      o.connect(g).connect(ac.destination);
      o.start(t + i * 0.09);
      o.stop(t + i * 0.09 + 0.4);
    });
    setTimeout(() => void ac.close(), 800);
  } catch {
    /* audio blocked until the page has been interacted with: stay silent */
  }
}

export function NotificationProvider({ children }: { children: ReactNode }) {
  const { session } = useAuth();
  const navigate = useNavigate();
  const empId = session?.employee.id;
  const [summary, setSummary] = useState<NotificationSummary | null>(null);
  const [live, setLive] = useState(false);
  const [prefs, setPrefs] = useState<NotificationPrefs | null>(null);
  const [desktop, setDesktop] = useState<DesktopState>(() => desktopState());
  const [banner, setBanner] = useState<LiveNotification | null>(null);
  const listeners = useRef(new Set<(e: LiveEvent) => void>());
  const prefsRef = useRef<NotificationPrefs | null>(null);
  prefsRef.current = prefs;
  const navRef = useRef(navigate);
  navRef.current = navigate;

  const refreshSummary = useCallback(() => {
    notificationsApi.summary().then(setSummary, () => undefined);
  }, []);
  const subscribe = useCallback((fn: (e: LiveEvent) => void) => {
    listeners.current.add(fn);
    return () => void listeners.current.delete(fn);
  }, []);
  const emit = (e: LiveEvent) => listeners.current.forEach((fn) => fn(e));

  useEffect(() => {
    if (!empId) return;
    refreshSummary();
    notificationsApi.prefs().then(setPrefs, () => undefined);
  }, [empId, refreshSummary]);

  // The permission can change in the browser's own site settings while the app is open.
  useEffect(() => {
    const sync = () => setDesktop(desktopState());
    window.addEventListener("focus", sync);
    return () => window.removeEventListener("focus", sync);
  }, []);

  useEffect(() => {
    if (!empId || typeof EventSource === "undefined") return;
    let stopped = false;
    let es: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let release: (() => void) | null = null;
    let lastSeq = 0;
    let leader = false;
    const bc = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel(`pt-notify-${empId}`) : null;

    const handle = (m: Msg, fromLeader: boolean) => {
      switch (m.type) {
        case "notification": {
          const item = m.data;
          lastSeq = Math.max(lastSeq, item.seq);
          emit({ type: "notification", item });
          const visible = document.visibilityState === "visible";
          if (visible && item.alert?.banner !== false) setBanner(item);
          if (!fromLeader) break; // desktop + sound: once per browser, from the leader tab
          const p = prefsRef.current;
          if (item.alert?.sound && p?.sound) chime();
          if (shouldShowDesktop({ prefOn: !!p?.desktop, state: desktopState(), alert: !!item.alert?.desktop, visible })) {
            try {
              const n = new Notification(item.title, { body: item.body, tag: item.id, icon: "/brand/tamisha-icon-192.png" });
              n.onclick = () => {
                window.focus();
                const to = safeLink(item.link);
                if (to) navRef.current(to);
                n.close();
              };
            } catch {
              /* some browsers only allow notifications from a service worker */
            }
          }
          break;
        }
        case "unread":
        case "ready":
          lastSeq = Math.max(lastSeq, m.data.latest_seq);
          setSummary(m.data);
          setLive(true);
          break;
        case "resync":
          emit({ type: "resync" });
          refreshSummary();
          break;
        case "status":
          setLive(m.live);
          break;
        case "hello":
          if (leader) bc?.postMessage({ type: "status", live: es?.readyState === EventSource.OPEN });
          break;
      }
    };

    const relay = (m: Msg) => {
      handle(m, true);
      bc?.postMessage(m);
    };

    const connect = () => {
      if (stopped) return;
      const url = lastSeq ? `${notificationsApi.streamUrl}?after=${lastSeq}` : notificationsApi.streamUrl;
      es = new EventSource(url);
      for (const type of ["notification", "unread", "ready"] as const)
        es.addEventListener(type, (e) => relay({ type, data: JSON.parse((e as MessageEvent).data) } as Msg));
      es.addEventListener("resync", () => relay({ type: "resync" }));
      es.onerror = () => {
        setLive(false);
        bc?.postMessage({ type: "status", live: false });
        // The browser retries on its own after a dropped connection. A refused one (signed out,
        // too many streams) is final for that EventSource: check the session, then try again.
        if (es?.readyState === EventSource.CLOSED) {
          refreshSummary();
          retry = setTimeout(connect, 15000);
        }
      };
    };

    if (bc) bc.onmessage = (e) => handle(e.data as Msg, false);
    const locks = (navigator as Navigator & { locks?: LockManager }).locks;
    if (locks?.request) {
      bc?.postMessage({ type: "hello" });
      locks
        .request(`pt-notify-leader-${empId}`, () =>
          new Promise<void>((resolve) => {
            release = resolve;
            if (stopped) return resolve();
            leader = true;
            connect();
          }),
        )
        .catch(() => undefined);
    } else {
      leader = true;
      connect();
    }
    return () => {
      stopped = true;
      clearTimeout(retry);
      es?.close();
      release?.();
      bc?.close();
    };
  }, [empId, refreshSummary]);

  // In-app banner: a few seconds, longer for critical alerts.
  useEffect(() => {
    if (!banner) return;
    const t = setTimeout(() => setBanner(null), banner.severity === "critical" ? 12000 : 6000);
    return () => clearTimeout(t);
  }, [banner]);

  const requestDesktop = useCallback(async () => {
    if (desktopState() === "unsupported") return "unsupported" as const;
    try {
      await Notification.requestPermission();
    } catch {
      /* older Safari: callback form only */
    }
    const s = desktopState();
    setDesktop(s);
    return s;
  }, []);

  const value = useMemo<Ctx>(
    () => ({
      summary,
      live,
      prefs,
      setPrefs,
      refreshSummary,
      subscribe,
      desktop,
      requestDesktop,
      banner,
      dismissBanner: () => setBanner(null),
    }),
    [summary, live, prefs, refreshSummary, subscribe, desktop, requestDesktop, banner],
  );
  return <NotifyCtx.Provider value={value}>{children}</NotifyCtx.Provider>;
}
