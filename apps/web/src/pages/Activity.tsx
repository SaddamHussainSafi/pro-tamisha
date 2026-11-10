import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ActivitySkeleton, ActivityTimeline } from "../components/activity/ActivityTimeline";
import { useNotifications } from "../components/notifications/NotificationProvider";
import { activityApi, appendPage, type ActivityItem } from "../lib/activity";
import { Icon } from "../ui/Icon";
import { ErrorState, PageHeader } from "../ui/primitives";
import { NotificationsView } from "./Notifications";

const TAB = "chip px-4 py-2 text-sm";
const ON = "border-brand-secondary bg-brand-secondary text-white";
const OFF = "border-line bg-card text-ink hover:bg-subtle";

/** Activity (who did what across the business) and your Notifications, on one page. */
export function ActivityPage() {
  const [params] = useSearchParams();
  const { summary } = useNotifications();
  const view = params.get("view") === "notifications" ? "notifications" : "activity";
  const unread = summary?.unread ?? 0;
  return (
    <div className="space-y-5">
      <PageHeader title="Activity" subtitle={view === "activity" ? "What's been happening, newest first." : "Alerts for you and your areas."} />
      <nav aria-label="Activity sections" className="flex flex-wrap gap-1.5">
        <Link to="/activity" aria-current={view === "activity" ? "page" : undefined} className={`${TAB} ${view === "activity" ? ON : OFF}`}>
          <Icon name="clock" size={16} />
          Activity
        </Link>
        <Link to="/activity?view=notifications" aria-current={view === "notifications" ? "page" : undefined} className={`${TAB} ${view === "notifications" ? ON : OFF}`}>
          <Icon name="bell" size={16} />
          Notifications
          {unread > 0 && <span className="num rounded-full bg-brand-primary px-1.5 text-[11px] font-semibold leading-5 text-white">{unread > 99 ? "99+" : unread}</span>}
        </Link>
      </nav>
      {view === "activity" ? <ActivityView /> : <NotificationsView />}
    </div>
  );
}

function Toggle({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <label className={`flex items-center gap-3 text-sm ${disabled ? "text-ink-muted" : "cursor-pointer text-ink"}`}>
      {label}
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-7 w-12 shrink-0 cursor-pointer items-center rounded-full border transition-colors duration-200 disabled:cursor-not-allowed disabled:opacity-60 ${checked ? "border-brand-primary bg-brand-primary" : "border-line bg-line"}`}
      >
        <span aria-hidden className={`inline-block size-5 rounded-full bg-white transition-transform duration-200 ${checked ? "translate-x-[1.45rem]" : "translate-x-1"}`} />
      </button>
    </label>
  );
}

function ActivityView() {
  const { subscribe } = useNotifications();
  const [mine, setMine] = useState(false);
  const [person, setPerson] = useState<{ id: string; name: string } | null>(null);
  const [items, setItems] = useState<ActivityItem[]>([]);
  const [total, setTotal] = useState(0);
  const [next, setNext] = useState<string | null>(null);
  const [canOthers, setCanOthers] = useState(true);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [more, setMore] = useState(false);
  const req = useRef(0);

  const load = useCallback(
    (quiet = false) => {
      const id = ++req.current;
      if (!quiet) setState("loading");
      activityApi.list({ mine, actor: person?.id }).then(
        (p) => {
          if (id !== req.current) return;
          setItems(p.items);
          setTotal(p.total);
          setNext(p.next_before);
          setCanOthers(p.can_see_others);
          setState("ready");
        },
        () => id === req.current && !quiet && setState("error"),
      );
    },
    [mine, person],
  );
  useEffect(() => load(), [load]);

  // New business events usually come with a notification; refresh quietly then, and on focus.
  useEffect(() => {
    let t: ReturnType<typeof setTimeout> | undefined;
    const soon = () => {
      clearTimeout(t);
      t = setTimeout(() => load(true), 800);
    };
    const off = subscribe(soon);
    const onFocus = () => document.visibilityState === "visible" && load(true);
    window.addEventListener("focus", onFocus);
    return () => {
      off();
      clearTimeout(t);
      window.removeEventListener("focus", onFocus);
    };
  }, [subscribe, load]);

  const loadMore = useCallback(() => {
    if (!next || more) return;
    setMore(true);
    activityApi.list({ mine, actor: person?.id, before: next }).then(
      (p) => {
        setItems((cur) => appendPage(cur, p.items));
        setNext(p.next_before);
        setMore(false);
      },
      () => setMore(false),
    );
  }, [next, more, mine, person]);

  const sentinel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !next || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((es) => es.some((e) => e.isIntersecting) && loadMore(), { rootMargin: "240px" });
    io.observe(el);
    return () => io.disconnect();
  }, [next, loadMore]);

  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 60000);
    return () => clearInterval(t);
  }, []);

  return (
    // Phones: the rows sit straight on the page so the sentence gets the width; sm+ one card.
    <section aria-labelledby="activity-count" className="sm:card sm:p-6">
      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-3">
        <h2 id="activity-count" className="text-base font-semibold" aria-live="polite">
          {state === "loading" && items.length === 0 ? "Activity" : `${total.toLocaleString("en-CA")} ${total === 1 ? "Activity" : "Activities"}`}
        </h2>
        {person && (
          <span className="chip border-line bg-subtle text-ink">
            {person.name}
            <button type="button" className="-mr-1 grid size-5 cursor-pointer place-items-center rounded-full hover:bg-line" onClick={() => setPerson(null)} aria-label={`Stop showing only ${person.name}`}>
              <Icon name="x" size={12} />
            </button>
          </span>
        )}
        <div className="ml-auto">
          {canOthers ? (
            <Toggle
              checked={mine}
              onChange={(v) => {
                setPerson(null);
                setMine(v);
              }}
              label="Show my activities only"
            />
          ) : (
            <p className="text-sm text-ink-muted">Showing your own activity</p>
          )}
        </div>
      </div>

      {state === "loading" && items.length === 0 ? (
        <ActivitySkeleton />
      ) : state === "error" ? (
        <ErrorState title="Couldn't load activity" detail="Check your connection and try again." onRetry={() => load()} />
      ) : items.length === 0 ? (
        <div className="grid place-items-center gap-2 px-6 py-12 text-center">
          <span aria-hidden className="mb-1 grid size-12 place-items-center rounded-full bg-subtle text-ink-muted">
            <Icon name="clock" size={22} />
          </span>
          <div className="font-medium">No activity yet</div>
          <p className="max-w-sm text-sm text-ink-muted">Orders, invoices, payments, stock and price changes will appear here as people work.</p>
        </div>
      ) : (
        <>
          <ActivityTimeline items={items} now={now} onlyPerson={canOthers ? (a) => a.actor.id && (setMine(false), setPerson({ id: a.actor.id, name: a.actor.name })) : undefined} />
          {next && (
            <div ref={sentinel} className="flex justify-center pt-4">
              <button type="button" className="btn-ghost" onClick={loadMore} disabled={more}>
                {more ? "Loading…" : "Load older"}
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
