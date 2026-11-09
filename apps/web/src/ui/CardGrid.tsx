import type { ReactNode } from "react";
import { Link } from "react-router-dom";

/** Responsive card grid used for master data (products, customers, suppliers, …). */
const GRID = "grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4";
/** Cards with a picture column need more width: as many ≥ 26rem columns as fit. */
const WIDE = "grid gap-4 grid-cols-[repeat(auto-fill,minmax(min(100%,26rem),1fr))]";

export function CardGrid<T>({ items, keyOf, caption, empty, render, wide = false }: { items: T[]; keyOf: (t: T) => string; caption: string; empty: ReactNode; render: (t: T) => ReactNode; wide?: boolean }) {
  if (!items.length) return <div className="card">{empty}</div>;
  return (
    <ul aria-label={caption} className={wide ? WIDE : GRID}>
      {items.map((t) => (
        <li key={keyOf(t)} className="flex">
          {render(t)}
        </li>
      ))}
    </ul>
  );
}

/** Card-shaped placeholders in the same grid, so the page keeps its shape while loading. */
export function CardGridSkeleton({ count = 8 }: { count?: number }) {
  return (
    <div role="status" aria-busy="true" aria-label="Loading">
      <ul className={GRID} aria-hidden>
        {Array.from({ length: count }, (_, i) => (
          <li key={i} className="card flex flex-col gap-4 p-6">
            <div className="flex items-center gap-3">
              <div className="skeleton size-12 rounded-ctl" />
              <div className="flex-1 space-y-2">
                <div className="skeleton h-4 w-2/3" />
                <div className="skeleton h-3 w-1/3" />
              </div>
            </div>
            <div className="space-y-2.5">
              <div className="skeleton h-3 w-full" />
              <div className="skeleton h-3 w-5/6" />
              <div className="skeleton h-3 w-2/3" />
            </div>
          </li>
        ))}
      </ul>
      <span className="sr-only">Loading…</span>
    </div>
  );
}

/** Initials tile when there is no avatar picture. */
export function Tile({ text, muted = false }: { text: string; muted?: boolean }) {
  const parts = text.replace(/[^\p{L}\p{N}\s-]/gu, "").trim().split(/[\s-]+/);
  const ini = ((parts[0]?.[0] ?? "") + (parts[1]?.[0] ?? "")).toUpperCase() || "?";
  return (
    <span aria-hidden className={`grid size-12 shrink-0 place-items-center rounded-ctl text-base font-semibold ${muted ? "bg-subtle text-ink-muted" : "bg-brand-accent/12 text-brand-primary"}`}>
      {ini}
    </span>
  );
}

/**
 * One item as a card. The title link stretches over the whole card (one tab stop); `actions`
 * sit above it so their buttons stay clickable and focusable on their own.
 */
export function EntityCard({
  to,
  title,
  subtitle,
  media,
  image,
  facts,
  chips,
  actions,
  dim = false,
}: {
  to?: string;
  title: ReactNode;
  subtitle?: ReactNode;
  media?: ReactNode;
  /** A picture down the left of the card (e.g. a product photo), filling that column. */
  image?: ReactNode;
  facts?: { label: string; value: ReactNode }[];
  chips?: ReactNode;
  actions?: ReactNode;
  dim?: boolean;
}) {
  const body = (
    <>
      <header className="flex items-start gap-3">
        {media}
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-base font-semibold">
            {to ? (
              <Link to={to} className="text-ink after:absolute after:inset-0 after:rounded-[var(--radius-card)] after:content-[''] hover:text-brand-primary focus-visible:outline-none focus-visible:after:outline-2 focus-visible:after:outline-brand-primary">
                {title}
              </Link>
            ) : (
              title
            )}
          </h3>
          {subtitle && <p className="truncate text-sm text-ink-muted">{subtitle}</p>}
        </div>
      </header>
      {facts && facts.length > 0 && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
          {facts.map((f) => (
            <div key={f.label} className="contents">
              <dt className="text-ink-muted">{f.label}</dt>
              <dd className="min-w-0 truncate text-right">{f.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {(chips || actions) && (
        <footer className="mt-auto flex flex-wrap items-center gap-2">
          {chips}
          {actions && <div className="relative z-10 ml-auto flex flex-wrap gap-1.5">{actions}</div>}
        </footer>
      )}
    </>
  );
  return (
    <article className={`card relative flex w-full gap-4 p-6 outline outline-1 outline-transparent transition-colors duration-150 ${image ? "flex-row gap-5" : "flex-col"} ${to ? "hover:outline-brand-gray/50 has-[a:focus-visible]:outline-brand-primary" : ""} ${dim ? "opacity-75" : ""}`}>
      {image ? (
        <>
          <div className="relative grid min-h-32 w-28 shrink-0 place-items-center overflow-hidden rounded-ctl bg-subtle sm:w-36">{image}</div>
          <div className="flex min-w-0 flex-1 flex-col gap-4">{body}</div>
        </>
      ) : (
        body
      )}
    </article>
  );
}
