import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Icon, type IconName } from "./Icon";
import { MoreMenu, type MenuAction } from "./MoreMenu";

// Product card (owner's target design, 2026-11-09): photo tile on a soft green ground, name with a
// status pill and a "⋯" menu, then icon rows. Product cards alone get the soft shadow and the
// tighter p-4; the other master-data grids keep EntityCard.

export type ProductFact = { icon: IconName; label: string; value: ReactNode; /** "Not set"-style value */ empty?: boolean };

const SHADOW = "shadow-[0_1px_2px_rgb(24_24_27/0.04),0_10px_28px_-12px_rgb(24_24_27/0.14)]";
/** As many columns of at least 26rem as fit (2 at 1440 px with the sidebar open, 1 on phones). */
export const PRODUCT_GRID = "grid gap-5 grid-cols-[repeat(auto-fill,minmax(min(100%,26rem),1fr))]";

export function StatusPill({ active }: { active: boolean }) {
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium leading-none ${
        active ? "bg-success/10 text-success" : "bg-subtle text-ink-muted"
      }`}
    >
      <span aria-hidden className="size-1.5 rounded-full bg-current" />
      {active ? "Active" : "Inactive"}
    </span>
  );
}

export function ProductCard({
  to,
  name,
  subtitle,
  image,
  active,
  facts,
  actions,
}: {
  to: string;
  name: string;
  subtitle: ReactNode;
  image: ReactNode;
  active: boolean;
  facts: ProductFact[];
  actions: MenuAction[];
}) {
  return (
    // @container: on narrow cards (phones) the pill moves under the menu and the photo shrinks.
    <article
      className={`@container relative flex w-full gap-4 rounded-card bg-card p-4 ${SHADOW} outline outline-1 outline-transparent transition-[outline-color] duration-150 hover:outline-brand-gray/40 has-[[data-card-link]:focus-visible]:outline-2 has-[[data-card-link]:focus-visible]:outline-brand-primary has-[[aria-expanded=true]]:z-20 ${active ? "" : "opacity-80"}`}
    >
      <div className="grid size-24 shrink-0 place-items-center overflow-hidden rounded-ctl bg-brand-accent/[0.07] @md:size-34">{image}</div>
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="relative grid grid-cols-[minmax(0,1fr)_auto] items-start gap-x-2 gap-y-1 @md:grid-cols-[minmax(0,1fr)_auto_auto]">
          <h3 className="col-span-2 row-start-1 truncate pr-8 pt-0.5 text-base font-semibold leading-6 tracking-tight @md:col-span-1 @md:pr-0" title={name}>
            <Link
              data-card-link
              to={to}
              className="text-ink outline-none after:absolute after:inset-0 after:rounded-[var(--radius-card)] after:content-[''] hover:text-brand-primary"
            >
              {name}
            </Link>
          </h3>
          <p className="col-start-1 row-start-2 truncate text-[13px] text-ink-muted">{subtitle}</p>
          <div className="col-start-2 row-start-2 justify-self-end @md:row-start-1 @md:pt-0.5">
            <StatusPill active={active} />
          </div>
          {/* Narrow cards: pinned top-right so the name can use the full width. */}
          <div className="absolute -right-1 top-0 z-10 @md:relative @md:right-auto @md:col-start-3 @md:row-start-1 @md:-mr-1 @md:justify-self-end">
            <MoreMenu label={`More for ${name}`} actions={actions} />
          </div>
        </div>
        <div className="my-3 border-t border-line" />
        <dl className="space-y-2.5 text-[13px]">
          {facts.map((f) => (
            <div key={f.label} className="flex items-center gap-3">
              <dt className="flex shrink-0 items-center gap-2.5 text-ink-muted">
                <Icon name={f.icon} size={16} />
                {f.label}
              </dt>
              <dd className={`ml-auto min-w-0 truncate text-right ${f.empty ? "text-ink-muted" : "num font-semibold text-ink"}`}>{f.value}</dd>
            </div>
          ))}
        </dl>
      </div>
    </article>
  );
}

/** Product-shaped placeholders in the same grid while the list loads. */
export function ProductGridSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div role="status" aria-busy="true" aria-label="Loading">
      <ul className={PRODUCT_GRID} aria-hidden>
        {Array.from({ length: count }, (_, i) => (
          <li key={i} className={`@container flex gap-4 rounded-card bg-card p-4 ${SHADOW}`}>
            <div className="skeleton size-24 shrink-0 rounded-ctl @md:size-34" />
            <div className="flex-1 space-y-3 pt-1">
              <div className="skeleton h-4 w-2/3" />
              <div className="skeleton h-3 w-1/3" />
              <div className="border-t border-line" />
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
