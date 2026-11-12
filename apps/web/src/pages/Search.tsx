import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Highlight } from "../components/search/Highlight";
import { SearchPreview } from "../components/search/SearchPreview";
import { useDebounced } from "../lib/useDebounced";
import {
  formatMoney,
  isAppPath,
  latestOnly,
  runSearch,
  searchApi,
  SearchError,
  STATUS_WORDS,
  SYNTAX_HINTS,
  typeIcon,
  type SearchHit,
  type SearchResponse,
  type SearchSort,
} from "../lib/search";
import { Icon } from "../ui/Icon";
import { EmptyState, LoadingRows, PageHeader } from "../ui/primitives";

const PAGE = 25;
const SORTS: { value: SearchSort; label: string }[] = [
  { value: "relevance", label: "Best match" },
  { value: "newest", label: "Newest" },
  { value: "oldest", label: "Oldest" },
];

/** All results for a search, with category filter, sort and paging (opened from "See all"). */
export function SearchPage() {
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const type = params.get("type") ?? "";
  const sort = (SORTS.some((s) => s.value === params.get("sort")) ? params.get("sort") : "relevance") as SearchSort;
  const [text, setText] = useState(q);
  const settled = useDebounced(text, 200);
  const [res, setRes] = useState<SearchResponse | null>(null);
  const [items, setItems] = useState<SearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<SearchHit | null>(null);
  const guard = useRef(latestOnly());

  useEffect(() => setText(q), [q]);
  useEffect(() => {
    if (settled === q) return;
    const next = new URLSearchParams(params);
    if (settled) next.set("q", settled);
    else next.delete("q");
    setParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settled]);

  useEffect(() => {
    const { token, signal } = guard.current.next();
    setLoading(true);
    setError(null);
    runSearch({ q, type: type || undefined, limit: PAGE, sort }, signal).then(
      (r) => {
        if (!guard.current.isCurrent(token)) return;
        setRes(r);
        setItems(r.items);
        setSelected(r.items[0] ?? null);
        setLoading(false);
      },
      (e: unknown) => {
        if (!guard.current.isCurrent(token) || (e instanceof DOMException && e.name === "AbortError")) return;
        setError(e instanceof SearchError && e.code === "BAD_QUERY" ? e.message : "Search is not available right now.");
        setLoading(false);
      },
    );
  }, [q, type, sort]);

  function more() {
    if (!res?.next_cursor) return;
    const { token, signal } = guard.current.next();
    setLoading(true);
    runSearch({ q, type: type || undefined, limit: PAGE, sort, cursor: res.next_cursor }, signal).then(
      (r) => {
        if (!guard.current.isCurrent(token)) return;
        setRes(r);
        setItems((xs) => [...xs, ...r.items]);
        setLoading(false);
      },
      () => guard.current.isCurrent(token) && setLoading(false),
    );
  }

  function set(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  function open(url: string, t?: string, id?: string) {
    if (!isAppPath(url)) return;
    if (t && id) void searchApi.remember(t, id).catch(() => undefined);
    nav(url);
  }

  const terms = res?.terms ?? [];
  const chips = res?.types ?? [];
  return (
    <div className="space-y-5">
      <PageHeader title="Search" subtitle="Every record you may see: invoices, orders, batches, receiving, customers, products, payments, scans and more." />
      <div className="card space-y-3 p-4 sm:p-5">
        <label className="relative block">
          <span className="sr-only">Search</span>
          <Icon name="search" size={18} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-ink-muted" />
          <input
            data-page-search
            className="input rounded-full py-3 pl-11 text-base"
            type="search"
            value={text}
            maxLength={200}
            placeholder="Invoice or batch number, customer, product…"
            onChange={(e) => setText(e.target.value)}
          />
        </label>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex min-w-0 flex-wrap gap-1.5" role="group" aria-label="Only search in">
            <button type="button" aria-pressed={!type} onClick={() => set("type", "")} className={`chip cursor-pointer px-3.5 py-1.5 text-[13px] ${!type ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:bg-subtle"}`}>
              All
            </button>
            {chips.map((c) => (
              <button
                key={c.type}
                type="button"
                aria-pressed={type === c.type}
                onClick={() => set("type", type === c.type ? "" : c.type)}
                className={`chip cursor-pointer px-3.5 py-1.5 text-[13px] ${type === c.type ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:bg-subtle"}`}
              >
                {c.label}
              </button>
            ))}
          </div>
          <label className="ml-auto flex items-center gap-2 text-sm">
            <span className="text-ink-muted">Sort</span>
            <select className="input w-auto py-1.5" value={sort} onChange={(e) => set("sort", e.target.value === "relevance" ? "" : e.target.value)}>
              {SORTS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <p className="text-xs text-ink-muted">
          Narrow with: {SYNTAX_HINTS.map((h) => <code key={h} className="mr-1.5 rounded bg-subtle px-1 py-0.5">{h}</code>)}
        </p>
        {res?.index.degraded && <p className="alert-warning" role="status">Full search is unavailable right now. Exact numbers and names still work.</p>}
        {res?.notes.map((n) => <p key={n} className="text-xs text-ink-muted">{n}</p>)}
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <section className="card min-w-0 overflow-hidden" aria-label="Results" aria-busy={loading}>
          <div className="flex items-center justify-between border-b border-line px-5 py-3 text-sm text-ink-muted">
            <span role="status" aria-live="polite">
              {error ? <span className="text-danger">{error}</span> : res && q ? `${res.total}${res.total_capped ? "+" : ""} results` : ""}
            </span>
          </div>
          {!q && !type ? (
            <EmptyState icon="search" title="Type something to search" hint="An invoice, order, batch or airway bill number finds it straight away." />
          ) : loading && !items.length ? (
            <LoadingRows rows={6} cols={3} />
          ) : !items.length && !error ? (
            <EmptyState icon="search" title="Nothing found" hint="Check the number, or try fewer words or another category." />
          ) : (
            <ul>
              {items.map((h) => (
                <li key={`${h.type}:${h.id}`} className="border-b border-line last:border-b-0">
                  <div className={`flex items-center gap-3 px-4 py-3 sm:px-5 ${selected?.id === h.id && selected.type === h.type ? "bg-brand-accent/8" : ""}`}>
                    <Icon name={typeIcon(h.type)} size={18} className="shrink-0 text-ink-muted" />
                    <Link
                      to={h.url}
                      className="min-w-0 flex-1"
                      onClick={(e) => {
                        e.preventDefault();
                        open(h.url, h.type, h.id);
                      }}
                      onFocus={() => setSelected(h)}
                      onMouseEnter={() => setSelected(h)}
                    >
                      <span className="block truncate font-medium">
                        <Highlight text={h.title} terms={terms} />
                      </span>
                      <span className="block truncate text-sm text-ink-muted">
                        <Highlight text={h.snippet ?? h.subtitle} terms={terms} />
                      </span>
                    </Link>
                    <span className="hidden shrink-0 flex-col items-end gap-0.5 text-xs text-ink-muted sm:flex">
                      {h.amount && <span className="num text-sm text-ink">{formatMoney(h.amount, h.currency)}</span>}
                      <span>{[h.date, STATUS_WORDS(h.status)].filter(Boolean).join(" · ")}</span>
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
          {res?.next_cursor && (
            <div className="border-t border-line p-3 text-center">
              <button type="button" className="btn-ghost" onClick={more} disabled={loading}>
                {loading ? "Loading…" : "Show more"}
              </button>
            </div>
          )}
        </section>
        <aside className="card hidden self-start lg:block" aria-label="Preview">
          {selected ? <SearchPreview key={`${selected.type}:${selected.id}`} type={selected.type} id={selected.id} onOpen={open} /> : <p className="p-5 text-sm text-ink-muted">Select a result to preview it.</p>}
        </aside>
      </div>
    </div>
  );
}
