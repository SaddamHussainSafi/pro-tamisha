import { useEffect, useRef } from "react";
import { useNotifications } from "../components/notifications/NotificationProvider";
import { OC_CHANGED } from "./orderConnect";

/**
 * Calls `fn` when Order Connect data may have changed: a website-order alert arrives on the live
 * notification stream (SSE, one connection per browser), another screen changed something, the
 * tab regains focus, or (fallback) every `everyMs`.
 */
export function useOcLive(fn: () => void, everyMs = 60_000) {
  const ref = useRef(fn);
  ref.current = fn;
  const { subscribe } = useNotifications();
  useEffect(() => {
    const run = () => ref.current();
    const off = subscribe((e) => {
      if (e.type === "resync" || e.item.category === "website_order") run();
    });
    const onFocus = () => document.visibilityState === "visible" && run();
    window.addEventListener(OC_CHANGED, run);
    window.addEventListener("focus", onFocus);
    const t = setInterval(() => document.visibilityState === "visible" && run(), everyMs);
    return () => {
      off();
      window.removeEventListener(OC_CHANGED, run);
      window.removeEventListener("focus", onFocus);
      clearInterval(t);
    };
  }, [subscribe, everyMs]);
}
