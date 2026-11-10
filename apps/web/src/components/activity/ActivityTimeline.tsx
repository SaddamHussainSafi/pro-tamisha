import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { activityIcon, initials, sentence, TONE, whenParts, type ActivityItem } from "../../lib/activity";
import { Icon } from "../../ui/Icon";

/**
 * Activity as a vertical timeline: when (left), a coloured dot on a rail, and a row card with
 * the person, what they did, the record, a tag, and a "…" menu.
 */
export function ActivityTimeline({ items, onlyPerson, now }: { items: ActivityItem[]; onlyPerson?: (a: ActivityItem) => void; now?: Date }) {
  return (
    <ol aria-label="Activity">
      {items.map((a) => (
        <ActivityRow key={a.id} a={a} onlyPerson={onlyPerson} now={now} />
      ))}
    </ol>
  );
}

function ActivityRow({ a, onlyPerson, now }: { a: ActivityItem; onlyPerson?: (a: ActivityItem) => void; now?: Date }) {
  const { day, time } = whenParts(a.at, now);
  const tone = TONE[a.tone] ?? TONE.neutral;
  // Record numbers (SO-20261110-0004) shouldn't break at their hyphens; names may wrap.
  const code = !!a.object && !a.object.includes(" ") && a.object.length <= 24;
  return (
    <li className="group/row relative grid grid-cols-[28px_minmax(0,1fr)] gap-x-3 py-1.5 sm:grid-cols-[6.5rem_28px_minmax(0,1fr)] sm:gap-x-4" data-testid="activity-row">
      <time dateTime={a.at} className="hidden pt-2.5 text-right text-sm leading-tight text-ink-muted sm:block">
        <span className="block">{day}</span>
        <span className="block">{time}</span>
      </time>
      <span aria-hidden className="relative flex justify-center">
        {/* The rail: each row draws its piece, through the gap to the next row. */}
        <span className="absolute -bottom-1.5 -top-1.5 left-1/2 w-px -translate-x-1/2 bg-line group-first/row:top-6 group-last/row:bottom-auto group-last/row:h-6" />
        <span className={`relative mt-3.5 grid size-7 place-items-center rounded-full text-white ring-4 ring-canvas sm:ring-card ${tone.dot}`}>
          <Icon name={activityIcon(a.icon)} size={14} className="stroke-[2.4]" />
        </span>
      </span>
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 rounded-ctl border border-line bg-card px-4 py-3 transition-colors duration-150 hover:border-brand-gray/50">
        <span aria-hidden className="hidden size-9 shrink-0 place-items-center rounded-full bg-brand-accent/15 text-xs font-semibold text-brand-primary-hover sm:grid">
          {a.actor.id ? initials(a.actor.name) : <Icon name="store" size={16} />}
        </span>
        <p className="min-w-0 grow basis-[12rem] text-[15px] leading-snug" title={sentence(a)}>
          <span className="mb-0.5 block text-xs text-ink-muted sm:hidden">
            {day}, {time}
          </span>
          <span className="font-semibold">{a.actor.name}</span> {a.verb}
          {a.object && (
            <>
              {" — "}
              {a.link ? (
                <Link to={a.link} className={`font-semibold text-ink underline-offset-2 hover:underline ${code ? "whitespace-nowrap" : ""}`}>
                  {a.object}
                </Link>
              ) : (
                <span className={`font-semibold ${code ? "whitespace-nowrap" : ""}`}>{a.object}</span>
              )}
            </>
          )}
          {a.extra && <span className="text-ink-muted"> ({a.extra})</span>}
        </p>
        <span className={`chip ml-auto border-transparent ${tone.chip}`}>
          <span aria-hidden className={`size-1.5 rounded-full ${tone.dot}`} />
          {a.tag}
        </span>
        <RowMenu a={a} onlyPerson={onlyPerson} />
      </div>
    </li>
  );
}

/** "…" menu: open the record, or show only this person's activity. Arrow keys, Esc and outside clicks work. */
function RowMenu({ a, onlyPerson }: { a: ActivityItem; onlyPerson?: (a: ActivityItem) => void }) {
  const [open, setOpen] = useState(false);
  const btn = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const id = useId();
  const canFilter = !!onlyPerson && !!a.actor.id;
  useEffect(() => {
    if (!open) return;
    menu.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus();
    const down = (e: PointerEvent) => {
      if (!menu.current?.contains(e.target as Node) && !btn.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", down);
    return () => document.removeEventListener("pointerdown", down);
  }, [open]);
  if (!a.link && !canFilter) return <span className="size-9 shrink-0" aria-hidden />;
  const onKey = (e: React.KeyboardEvent) => {
    const items = [...(menu.current?.querySelectorAll<HTMLElement>("[role=menuitem]") ?? [])];
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === "Escape") {
      e.stopPropagation();
      setOpen(false);
      btn.current?.focus();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      items[(i + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]?.focus();
    }
  };
  const item = "flex w-full cursor-pointer items-center gap-2 rounded-ctl px-3 py-2 text-left text-sm text-ink hover:bg-subtle focus-visible:bg-subtle";
  return (
    <div className="relative shrink-0">
      <button
        ref={btn}
        type="button"
        className="grid size-9 cursor-pointer place-items-center rounded-full text-ink-muted transition-colors duration-150 hover:bg-subtle hover:text-ink"
        aria-label={`More for: ${sentence(a)}`}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((o) => !o)}
      >
        <svg aria-hidden width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
          <circle cx="5" cy="12" r="1.8" />
          <circle cx="12" cy="12" r="1.8" />
          <circle cx="19" cy="12" r="1.8" />
        </svg>
      </button>
      {open && (
        <div ref={menu} id={id} role="menu" onKeyDown={onKey} className="absolute right-0 top-10 z-20 w-56 animate-pop rounded-ctl border border-line bg-card p-1.5 shadow-[0_8px_24px_rgb(24_24_27/0.12)]">
          {a.link && (
            <Link role="menuitem" to={a.link} className={item} onClick={() => setOpen(false)}>
              <Icon name="arrowUpRight" size={16} />
              Open {a.object ?? "record"}
            </Link>
          )}
          {canFilter && (
            <button
              role="menuitem"
              type="button"
              className={item}
              onClick={() => {
                setOpen(false);
                onlyPerson?.(a);
              }}
            >
              <Icon name="user" size={16} />
              Only {a.actor.name.split(" ")[0]}'s activity
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** Same footprint as rows, while the first page loads. */
export function ActivitySkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div role="status" aria-label="Loading activity" className="space-y-3">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} aria-hidden className="grid grid-cols-[28px_minmax(0,1fr)] gap-x-3 sm:grid-cols-[6.5rem_28px_minmax(0,1fr)] sm:gap-x-4">
          <div className="hidden space-y-1.5 pt-3 sm:block">
            <div className="skeleton ml-auto h-3 w-14" />
            <div className="skeleton ml-auto h-3 w-16" />
          </div>
          <div className="mt-3.5 size-7 animate-pulse rounded-full bg-line/80" />
          <div className="flex items-center gap-3 rounded-ctl border border-line px-4 py-3.5">
            <div className="size-9 animate-pulse rounded-full bg-line/80" />
            <div className="skeleton h-3.5 flex-1" />
            <div className="skeleton h-6 w-24" />
          </div>
        </div>
      ))}
    </div>
  );
}
