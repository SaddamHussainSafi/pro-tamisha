import { useEffect, useRef } from "react";
import { useNotifications } from "../components/notifications/NotificationProvider";

/** Run `fn` when the server says something about Gmail or a reading changed (the Notification
 * Center's live feed), when the tab comes back, and on a slow timer: ordinary mail raises no
 * notification, so the timer is what makes it appear by itself. */
export function useGmailLive(fn: () => void, everyMs = 20_000) {
  const ref = useRef(fn);
  ref.current = fn;
  const { subscribe } = useNotifications();
  useEffect(() => {
    const run = () => ref.current();
    const off = subscribe((e) => {
      if (e.type === "resync" || e.item.kind.startsWith("gmail.") || e.item.kind.startsWith("ocr.")) run();
    });
    const onFocus = () => document.visibilityState === "visible" && run();
    window.addEventListener("focus", onFocus);
    const t = setInterval(() => document.visibilityState === "visible" && run(), everyMs);
    return () => {
      off();
      window.removeEventListener("focus", onFocus);
      clearInterval(t);
    };
  }, [subscribe, everyMs]);
}
