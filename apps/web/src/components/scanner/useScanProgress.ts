import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../../api";
import { scanProgressApi, type ScanProgress } from "../../lib/scanProgress";

const POLL_MS = 2000;

/** Follows one scan live: the server's SSE stream when the browser has EventSource, polling
 * otherwise (and after the stream was refused). `restart()` after an action (read again, read
 * now) starts following again. 404 → `missing`. */
export function useScanProgress(jobId: string | null) {
  const [snap, setSnap] = useState<ScanProgress | null>(null);
  const [missing, setMissing] = useState(false);
  const [live, setLive] = useState(false);
  const [round, setRound] = useState(0);
  const last = useRef<string>("");

  const accept = useCallback((s: ScanProgress) => {
    const key = `${s.version}|${s.outcome}`;
    if (key === last.current) return;
    last.current = key;
    setSnap(s);
  }, []);

  useEffect(() => {
    if (!jobId) return;
    let stopped = false;
    let es: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setMissing(false);

    const poll = async () => {
      try {
        const s = await scanProgressApi.get(jobId);
        if (stopped) return;
        accept(s);
        if (s.outcome === "WORKING") timer = setTimeout(poll, POLL_MS);
      } catch (e) {
        if (stopped) return;
        if (e instanceof ApiError && (e.status === 404 || e.status === 403)) setMissing(true);
        else timer = setTimeout(poll, POLL_MS * 2);
      }
    };

    if (typeof EventSource === "undefined") void poll();
    else {
      es = new EventSource(scanProgressApi.streamUrl(jobId));
      es.addEventListener("progress", (e) => {
        setLive(true);
        accept(JSON.parse((e as MessageEvent).data) as ScanProgress);
      });
      es.addEventListener("end", () => {
        es?.close();
        es = null;
        setLive(false);
      });
      es.onerror = () => {
        setLive(false);
        // A dropped stream reconnects by itself; a refused one is closed: fall back to polling,
        // which also tells a missing scan from a network hiccup.
        if (es?.readyState === EventSource.CLOSED) {
          es = null;
          void poll();
        }
      };
    }
    return () => {
      stopped = true;
      es?.close();
      if (timer) clearTimeout(timer);
    };
  }, [jobId, round, accept]);

  const restart = useCallback(() => setRound((n) => n + 1), []);
  return { snap, missing, live, restart };
}
