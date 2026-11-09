import { useCallback, useEffect, useRef, useState } from "react";
import { merge } from "../../lib/notifications";
import { notificationsApi, type AppNotification, type ListParams } from "../../lib/notificationsApi";
import { useNotifications } from "./NotificationProvider";

/** A filtered, paged inbox that live events update in place. Shared by the panel and the page. */
export function useInbox(params: ListParams, enabled = true) {
  const { subscribe, refreshSummary } = useNotifications();
  const [items, setItems] = useState<AppNotification[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [more, setMore] = useState(false);
  const key = JSON.stringify(params);
  const req = useRef(0);
  const paramsRef = useRef(params);
  paramsRef.current = params;

  const load = useCallback(() => {
    const id = ++req.current;
    setState("loading");
    notificationsApi.list(paramsRef.current).then(
      (p) => {
        if (id !== req.current) return;
        setItems(p.items);
        setNext(p.next_before);
        setState("ready");
      },
      () => id === req.current && setState("error"),
    );
  }, []);

  useEffect(() => {
    if (enabled) load();
  }, [key, enabled, load]);

  const loadMore = useCallback(() => {
    if (next == null || more) return;
    setMore(true);
    notificationsApi.list({ ...paramsRef.current, before: next }).then(
      (p) => {
        setItems((cur) => merge(cur, p.items));
        setNext(p.next_before);
        setMore(false);
      },
      () => setMore(false),
    );
  }, [next, more]);

  // Live: a new notification that matches the current filters appears at the top.
  useEffect(() => {
    if (!enabled) return;
    return subscribe((e) => {
      if (e.type === "resync") return load();
      const p = paramsRef.current;
      const n = e.item;
      if (p.status === "archived") return;
      if (p.category?.length && !p.category.includes(n.category)) return;
      if (p.severity?.length && !p.severity.includes(n.severity)) return;
      if (p.q?.trim()) {
        const q = p.q.trim().toLowerCase();
        if (![n.title, n.body, n.source_module].some((s) => s.toLowerCase().includes(q))) return;
      }
      setItems((cur) => merge(cur, [n]));
    });
  }, [enabled, subscribe, load]);

  const patch = useCallback((n: AppNotification) => setItems((cur) => cur.map((x) => (x.id === n.id ? n : x))), []);
  const drop = useCallback((ids: string[]) => setItems((cur) => cur.filter((x) => !ids.includes(x.id))), []);

  /** Optimistic change; on failure the server's truth is reloaded. */
  const act = useCallback(
    async (n: AppNotification, change: "read" | "unread" | "archive" | "unarchive") => {
      if (change === "archive" || change === "unarchive") drop([n.id]);
      else patch({ ...n, read: change === "read" });
      try {
        await notificationsApi[change](n.id);
      } catch {
        load();
      }
      refreshSummary();
    },
    [patch, drop, load, refreshSummary],
  );

  const archiveMany = useCallback(
    async (list: AppNotification[]) => {
      drop(list.map((n) => n.id));
      try {
        await notificationsApi.archiveMany(list.map((n) => n.id));
      } catch {
        load();
      }
      refreshSummary();
    },
    [drop, load, refreshSummary],
  );

  const readAll = useCallback(async () => {
    setItems((cur) => cur.map((x) => ({ ...x, read: true })));
    try {
      await notificationsApi.readAll();
    } catch {
      load();
    }
    refreshSummary();
  }, [load, refreshSummary]);

  return { items, state, hasMore: next != null, loadingMore: more, load, loadMore, act, archiveMany, readAll };
}
