import { forwardRef, useId, useImperativeHandle, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { NAV_GROUPS, SETTINGS } from "../nav";
import { Icon, type IconName } from "../ui/Icon";

type Target = { label: string; group: string; to: string; icon: IconName };

const MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.userAgent);

// Every screen in the sidebar, sub-pages included, as one flat list.
const TARGETS: Target[] = [
  ...NAV_GROUPS.flatMap((g) =>
    g.items.flatMap((item) => {
      const group = g.title ?? "General";
      const own: Target = { label: item.label, group, to: item.to, icon: item.icon };
      const kids = (item.children ?? [])
        .filter((c) => c.to !== item.to)
        .map((c) => ({ label: `${item.label} › ${c.label}`, group, to: c.to, icon: item.icon }));
      return [own, ...kids];
    }),
  ),
  { label: SETTINGS.label, group: "General", to: SETTINGS.to, icon: SETTINGS.icon },
];

/** "Go to" box in the top bar: type part of a screen name, Enter opens it. Ctrl/⌘ K focuses it. */
export const QuickJump = forwardRef<HTMLInputElement>(function QuickJump(_, ref) {
  const nav = useNavigate();
  const listId = useId();
  const input = useRef<HTMLInputElement>(null);
  useImperativeHandle(ref, () => input.current as HTMLInputElement, []);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);

  const matches = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t) return TARGETS;
    return TARGETS.filter((x) => x.label.toLowerCase().includes(t) || x.group.toLowerCase().includes(t));
  }, [q]);

  function go(t: Target | undefined) {
    if (!t) return;
    nav(t.to);
    setQ("");
    setOpen(false);
    input.current?.blur();
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      setOpen(true);
      const step = e.key === "ArrowDown" ? 1 : -1;
      setActive((a) => (matches.length ? (a + step + matches.length) % matches.length : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      go(matches[active]);
    } else if (e.key === "Escape") {
      setOpen(false);
      setQ("");
      e.currentTarget.blur();
    }
  }

  const show = open && matches.length > 0;
  const optionId = (i: number) => `${listId}-o${i}`;
  return (
    <div className="relative">
      <label className="relative block">
        <span className="sr-only">Go to a screen</span>
        <Icon name="search" size={18} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-ink-muted" />
        <input
          ref={input}
          type="text"
          role="combobox"
          aria-expanded={show}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={show ? optionId(active) : undefined}
          className="input w-64 rounded-full border-transparent py-2.5 pl-11 pr-16 xl:w-72"
          placeholder="Go to…"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setActive(0);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={onKey}
        />
        <kbd aria-hidden className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 rounded-md border border-line bg-subtle px-1.5 py-px text-[11px] text-ink-muted">
          {MAC ? "⌘K" : "Ctrl K"}
        </kbd>
      </label>
      <ul
        id={listId}
        role="listbox"
        aria-label="Screens"
        className={`absolute right-0 top-full z-40 mt-2 max-h-[min(24rem,70vh)] w-full min-w-72 overflow-y-auto rounded-ctl border border-line bg-card p-1.5 ${show ? "animate-pop" : "hidden"}`}
      >
        {matches.map((t, i) => (
          <li
            key={t.to + t.label}
            id={optionId(i)}
            role="option"
            aria-selected={i === active}
            // mousedown, not click: keeps focus in the input until we navigate
            onMouseDown={(e) => {
              e.preventDefault();
              go(t);
            }}
            onMouseEnter={() => setActive(i)}
            className={`flex cursor-pointer items-center gap-3 rounded-ctl px-3 py-2 text-sm ${i === active ? "bg-brand-accent/12 text-brand-primary-hover" : "text-ink"}`}
          >
            <Icon name={t.icon} size={18} className={i === active ? "text-brand-primary" : "text-ink-muted"} />
            <span className="min-w-0 flex-1 truncate">{t.label}</span>
            <span className="text-xs text-ink-muted">{t.group}</span>
          </li>
        ))}
      </ul>
    </div>
  );
});
