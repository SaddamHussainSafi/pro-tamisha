import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../../auth";
import {
  formatMoney,
  isAppPath,
  latestOnly,
  quickActions,
  runSearch,
  searchApi,
  searchPageUrl,
  SearchError,
  SYNTAX_HINTS,
  typeIcon,
  type RecentItem,
  type SearchResponse,
} from "../../lib/search";
import { NAV_GROUPS, SETTINGS } from "../../nav";
import { Icon, type IconName } from "../../ui/Icon";
import { Highlight } from "./Highlight";
import { SearchPreview } from "./SearchPreview";

const DEBOUNCE_MS = 160;
const PER_GROUP = 4;

type Option = {
  key: string;
  group: string;
  label: string;
  sub?: string;
  meta?: string;
  icon: IconName;
  to: string;
  type?: string; // record type: previewable + remembered
  id?: string;
  match?: string;
  snippet?: string | null;
};

type Screen = { label: string; group: string; to: string; icon: IconName };
const SCREENS: Screen[] = [
  ...NAV_GROUPS.flatMap((g) =>
    g.items.flatMap((item) => {
      const group = g.title ?? "General";
      const own: Screen = { label: item.label, group, to: item.to, icon: item.icon };
      const kids = (item.children ?? []).filter((c) => c.to !== item.to).map((c) => ({ label: `${item.label} › ${c.label}`, group, to: c.to, icon: item.icon }));
      return [own, ...kids];
    }),
  ),
  { label: SETTINGS.label, group: "General", to: SETTINGS.to, icon: SETTINGS.icon },
];

/**
 * Universal search (⌘K / Ctrl K). Spotlight-style dialog: results grouped by kind with a few per
 * group and "See all", a lazy preview on wide screens, recent records and quick actions when
 * the box is empty, and the old "Go to" screen list kept as the "Screens" group.
 */
export function CommandPalette({ open, onClose, initial = "" }: { open: boolean; onClose: () => void; initial?: string }) {
  const nav = useNavigate();
  const { session } = useAuth();
  const listId = useId();
  const input = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const [q, setQ] = useState(initial);
  const [type, setType] = useState<string>("");
  const [res, setRes] = useState<SearchResponse | null>(null);
  const [types, setTypes] = useState<{ type: string; label: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [recent, setRecent] = useState<RecentItem[] | null>(null);
  const [active, setActive] = useState(0);
  const guard = useRef(latestOnly());

  // Open: fresh state, focus the box, load recents + allowed categories.
  useEffect(() => {
    if (!open) return;
    setQ(initial);
    setType("");
    setActive(0);
    setError(null);
    requestAnimationFrame(() => input.current?.focus());
    searchApi.recent().then(setRecent, () => setRecent([]));
    void runSearch({ q: "" }).then((r) => setTypes(r.types), () => setTypes([]));
  }, [open, initial]);

  // Search: debounced; each request cancels the previous one; stale answers are ignored.
  useEffect(() => {
    if (!open) return;
    const text = q.trim();
    if (!text && !type) {
      guard.current.cancel();
      setRes(null);
      setLoading(false);
      setError(null);
      return;
    }
    setLoading(true);
    const t = setTimeout(() => {
      const { token, signal } = guard.current.next();
      runSearch({ q: text, type: type || undefined, perGroup: PER_GROUP, limit: 1 }, signal).then(
        (r) => {
          if (!guard.current.isCurrent(token)) return;
          setRes(r);
          if (r.types.length) setTypes(r.types);
          setError(null);
          setLoading(false);
          setActive(0);
        },
        (e: unknown) => {
          if (!guard.current.isCurrent(token) || (e instanceof DOMException && e.name === "AbortError")) return;
          setError(e instanceof SearchError && e.code === "BAD_QUERY" ? e.message : "Search is not available right now. Try again in a moment.");
          setLoading(false);
        },
      );
    }, DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [q, type, open]);

  useEffect(() => () => guard.current.cancel(), []);

  const options = useMemo<Option[]>(() => {
    const text = q.trim();
    const out: Option[] = [];
    if (!text && !type) {
      for (const r of recent ?? [])
        out.push({ key: `r:${r.type}:${r.id}`, group: "Recent", label: r.title, sub: r.subtitle, icon: typeIcon(r.type), to: r.url, type: r.type, id: r.id });
      for (const a of quickActions(session)) out.push({ key: `a:${a.label}`, group: "Quick actions", label: a.label, sub: a.hint, icon: a.icon, to: a.to });
      return out;
    }
    for (const g of res?.groups ?? []) {
      for (const h of g.items)
        out.push({
          key: `h:${h.type}:${h.id}`,
          group: g.label,
          label: h.title,
          sub: h.subtitle,
          meta: formatMoney(h.amount, h.currency) ?? undefined,
          icon: typeIcon(h.type),
          to: h.url,
          type: h.type,
          id: h.id,
          match: h.match,
          snippet: h.snippet,
        });
      if (g.count > g.items.length)
        out.push({ key: `s:${g.type}`, group: g.label, label: `See all ${g.count}${res?.total_capped ? "+" : ""} ${g.label.toLowerCase()}`, icon: "arrowUpRight", to: searchPageUrl(text, g.type) });
    }
    const t = text.toLowerCase();
    if (t && !type) {
      const screens: Option[] = SCREENS.filter((x) => x.label.toLowerCase().includes(t))
        .slice(0, 4)
        .map((s) => ({ key: `n:${s.to}:${s.label}`, group: "Screens", label: s.label, sub: s.group, icon: s.icon, to: s.to }));
      // "pack" + Enter still jumps to Packing (the old Go-to habit) unless a record matched
      // its number or name exactly.
      const exact = res?.groups.some((g) => g.items.some((h) => h.match === "number" || h.match === "name"));
      const screenFirst = !exact && t.length >= 3 && SCREENS.some((x) => x.label.toLowerCase().startsWith(t));
      if (screenFirst) out.unshift(...screens);
      else out.push(...screens);
    }
    if (text) out.push({ key: "all", group: "", label: `Search all results for “${text}”`, icon: "search", to: searchPageUrl(text, type || undefined) });
    return out;
  }, [q, type, res, recent, session]);

  const current = options[Math.min(active, options.length - 1)];
  const optionId = (i: number) => `${listId}-o${i}`;

  const go = useCallback(
    (to: string, recType?: string, recId?: string) => {
      if (!isAppPath(to)) return;
      if (recType && recId) void searchApi.remember(recType, recId).catch(() => undefined);
      onClose();
      nav(to);
    },
    [nav, onClose],
  );

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!options.length) return;
      const step = e.key === "ArrowDown" ? 1 : -1;
      setActive((a) => (a + step + options.length) % options.length);
    } else if (e.key === "Home" && e.ctrlKey) {
      setActive(0);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (e.metaKey || e.ctrlKey) {
        if (q.trim()) go(searchPageUrl(q.trim(), type || undefined));
      } else if (current) go(current.to, current.type, current.id);
    } else if (e.key === "Escape") {
      e.preventDefault();
      if (q) setQ("");
      else onClose();
    } else if (e.key === "Backspace" && !q && type) {
      setType("");
    }
  }

  // Keep the active row in view.
  useEffect(() => {
    document.getElementById(optionId(active))?.scrollIntoView({ block: "nearest" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active]);

  // Focus trap: Tab / Shift+Tab cycle inside the dialog; Esc anywhere closes.
  function trap(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape" && e.target !== input.current) {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key !== "Tab" || !dialog.current) return;
    const items = Array.from(dialog.current.querySelectorAll<HTMLElement>('input, button:not([disabled]), a[href], [tabindex="0"]')).filter((el) => el.offsetParent !== null);
    const first = items[0];
    const last = items[items.length - 1];
    if (!first || !last) return;
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  if (!open) return null;

  const grouped: { name: string; items: { o: Option; i: number }[] }[] = [];
  options.forEach((o, i) => {
    const last = grouped[grouped.length - 1];
    if (last && last.name === o.group) last.items.push({ o, i });
    else grouped.push({ name: o.group, items: [{ o, i }] });
  });
  const terms = res?.terms ?? [];
  const showPreview = !!current?.type && !!current.id && current.type !== "activity";
  const status = loading ? "Searching…" : error ? error : res ? `${res.total}${res.total_capped ? "+" : ""} results` : "";

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-start justify-center sm:p-4 sm:pt-[8vh]" onKeyDown={trap}>
      <div className="absolute inset-0 animate-fade bg-ink/40" aria-hidden onMouseDown={onClose} />
      <div
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-label="Search"
        className="relative flex h-full w-full animate-pop flex-col overflow-hidden bg-card shadow-xl sm:h-auto sm:max-h-[min(42rem,84vh)] sm:max-w-5xl sm:rounded-[var(--radius-card)]"
      >
        <div className="flex items-center gap-2 border-b border-line px-3 py-2.5 sm:px-4">
          <Icon name="search" size={20} className="shrink-0 text-ink-muted" />
          {type && (
            <button type="button" className="chip shrink-0 cursor-pointer border-brand-secondary bg-brand-secondary py-1 text-white" onClick={() => setType("")} aria-label={`Only ${types.find((t) => t.type === type)?.label ?? type}; remove filter`}>
              {types.find((t) => t.type === type)?.label ?? type} <Icon name="x" size={12} />
            </button>
          )}
          <input
            ref={input}
            type="text"
            role="combobox"
            aria-expanded={options.length > 0}
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={options.length ? optionId(Math.min(active, options.length - 1)) : undefined}
            aria-describedby={`${listId}-status`}
            autoComplete="off"
            spellCheck={false}
            className="min-w-0 flex-1 bg-transparent py-2 text-base text-ink outline-none placeholder:text-ink-muted"
            placeholder="Search invoices, batches, customers, products… or type invoice:, batch:, sku:"
            value={q}
            maxLength={200}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKey}
          />
          {loading && <span aria-hidden className="size-4 shrink-0 animate-spin rounded-full border-2 border-line border-t-brand-primary" />}
          <button type="button" className="grid size-9 shrink-0 cursor-pointer place-items-center rounded-full text-ink-muted hover:bg-subtle sm:hidden" onClick={onClose} aria-label="Close search">
            <Icon name="x" size={18} />
          </button>
          <kbd aria-hidden className="hidden rounded-md border border-line bg-subtle px-1.5 py-px text-[11px] text-ink-muted sm:block">Esc</kbd>
        </div>

        {types.length > 1 && (
          <div className="flex gap-1.5 overflow-x-auto border-b border-line px-3 py-2 sm:px-4" role="group" aria-label="Only search in">
            <button type="button" aria-pressed={!type} onClick={() => setType("")} className={`chip cursor-pointer px-3 py-1 ${!type ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:bg-subtle"}`}>
              All
            </button>
            {types.map((t) => (
              <button
                key={t.type}
                type="button"
                aria-pressed={type === t.type}
                onClick={() => {
                  setType(type === t.type ? "" : t.type);
                  input.current?.focus();
                }}
                className={`chip cursor-pointer px-3 py-1 ${type === t.type ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:bg-subtle"}`}
              >
                {t.label}
                {res && !type && res.groups.find((g) => g.type === t.type) && <span className="num opacity-70">{res.groups.find((g) => g.type === t.type)?.count}</span>}
              </button>
            ))}
          </div>
        )}

        {res?.index.degraded && (
          <p className="alert-warning mx-3 mt-2 sm:mx-4" role="status">Full search is unavailable right now. Exact numbers and names still work.</p>
        )}
        {!res?.index.degraded && res && (res.index.behind_seconds ?? 0) > 60 && (
          <p className="mx-3 mt-2 text-xs text-ink-muted sm:mx-4" role="status">Search is catching up on recent changes. Exact numbers are always current.</p>
        )}

        <div className="flex min-h-0 flex-1">
          <ul id={listId} role="listbox" aria-label="Search results" className="min-h-0 flex-1 overflow-y-auto p-2 sm:max-h-[min(34rem,70vh)]">
            {grouped.map((g) => (
              <li key={g.name || "_end"} role="presentation">
                {g.name && (
                  <div className="flex items-center justify-between px-3 pb-1 pt-2.5 text-xs font-medium text-ink-muted" role="presentation">
                    <span>{g.name}</span>
                    {g.name === "Recent" && (recent?.length ?? 0) > 0 && (
                      <button
                        type="button"
                        className="cursor-pointer rounded-full px-2 py-0.5 hover:bg-subtle hover:text-ink"
                        onClick={() => {
                          void searchApi.clearRecent().then(() => setRecent([]));
                          input.current?.focus();
                        }}
                      >
                        Clear
                      </button>
                    )}
                  </div>
                )}
                <ul role="group" aria-label={g.name || "More"}>
                  {g.items.map(({ o, i }) => (
                    <li
                      key={o.key}
                      id={optionId(i)}
                      role="option"
                      aria-selected={i === active}
                      data-type={o.type}
                      onMouseDown={(e) => {
                        e.preventDefault();
                        go(o.to, o.type, o.id);
                      }}
                      onMouseMove={() => i !== active && setActive(i)}
                      className={`flex cursor-pointer items-center gap-3 rounded-ctl px-3 py-2 text-sm ${i === active ? "bg-brand-accent/12 text-brand-primary-hover" : "text-ink"}`}
                    >
                      <Icon name={o.icon} size={18} className={`shrink-0 ${i === active ? "text-brand-primary" : "text-ink-muted"}`} />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-medium">
                          <Highlight text={o.label} terms={terms} />
                        </span>
                        {(o.snippet || o.sub) && (
                          <span className="block truncate text-xs text-ink-muted">
                            <Highlight text={o.snippet ?? o.sub ?? ""} terms={terms} />
                          </span>
                        )}
                      </span>
                      {o.meta && <span className="num shrink-0 text-xs text-ink-muted">{o.meta}</span>}
                      {o.match && o.match !== "number" && o.match !== "name" && (
                        <span className="hidden shrink-0 rounded-full bg-subtle px-2 py-0.5 text-[11px] text-ink-muted sm:inline">{o.match}</span>
                      )}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
            {!loading && !error && res && res.total === 0 && (
              <li role="presentation" className="px-3 py-8 text-center text-sm text-ink-muted">
                Nothing found for “{q.trim()}”. Check the number, or try fewer words.
              </li>
            )}
            {error && (
              <li role="presentation" className="px-3 py-6 text-center text-sm text-danger">
                {error}
              </li>
            )}
            {!q.trim() && !type && (
              <li role="presentation" className="px-3 pb-2 pt-4 text-xs text-ink-muted">
                Try: {SYNTAX_HINTS.map((h) => (
                  <button key={h} type="button" className="mr-1.5 cursor-pointer rounded-md bg-subtle px-1.5 py-0.5 font-mono text-[11px] hover:bg-line" onClick={() => { setQ(h.endsWith(":") ? h : `${h} `); input.current?.focus(); }}>
                    {h}
                  </button>
                ))}
              </li>
            )}
          </ul>
          {showPreview && current?.type && current.id && (
            <aside aria-label="Preview" className="hidden w-[22rem] shrink-0 overflow-y-auto border-l border-line lg:block">
              <SearchPreview key={`${current.type}:${current.id}`} type={current.type} id={current.id} onOpen={go} />
            </aside>
          )}
        </div>

        <div className="flex items-center gap-3 border-t border-line px-4 py-2 text-xs text-ink-muted">
          <span id={`${listId}-status`} role="status" aria-live="polite" className="min-w-0 flex-1 truncate">
            {status}
          </span>
          <span className="hidden gap-3 sm:flex" aria-hidden>
            <span><kbd className="font-sans">↑↓</kbd> move</span>
            <span><kbd className="font-sans">↵</kbd> open</span>
            <span><kbd className="font-sans">Ctrl ↵</kbd> all results</span>
            <span><kbd className="font-sans">Tab</kbd> filters</span>
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
