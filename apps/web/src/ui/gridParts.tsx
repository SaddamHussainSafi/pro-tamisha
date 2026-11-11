import type { ReactNode } from "react";
import { money2 } from "../lib/gridData";

// The problem list and the sticky totals bar under <LineGrid>.

/** Problems to fix, listed under the grid ("Line 3: ..."), the first few. */
export function GridProblems({ items }: { items: { row: number; text: string }[] }) {
  if (!items.length) return null;
  const shown = items.slice(0, 5);
  return (
    <ul className="space-y-0.5 text-sm text-danger" aria-label="Lines to fix">
      {shown.map((p) => (
        <li key={p.row}>{`Line ${p.row + 1}: ${p.text}`}</li>
      ))}
      {items.length > shown.length && <li className="text-ink-muted">{`…and ${items.length - shown.length} more`}</li>}
    </ul>
  );
}

/** Sticky bottom bar: Subtotal, Tax, Net total and the page's actions. */
export function TotalsBar({
  left,
  subtotal,
  tax,
  taxLabel,
  total,
  status,
  checked,
  children,
}: {
  left?: ReactNode;
  subtotal: string | null;
  tax: string | null;
  taxLabel: string;
  total: string | null;
  status: ReactNode;
  checked: boolean;
  children: ReactNode;
}) {
  const fig = (v: string | null) => (v === null ? "—" : money2(v));
  return (
    <div className="sticky bottom-0 z-20 -mx-6 -mb-6 mt-2 rounded-b-[var(--radius-card)] border-t border-line bg-card/95 px-6 py-4 backdrop-blur supports-[backdrop-filter]:bg-card/85">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 lg:flex-nowrap">
        <div className="flex shrink-0 items-center gap-3">{left}</div>
        <div className="flex min-w-0 flex-wrap items-center justify-end gap-x-6 gap-y-3">
          <dl className="flex items-end gap-6 border-l border-line pl-6" aria-live="polite">
            <div>
              <dt className="text-xs text-ink-muted">Subtotal</dt>
              <dd className="num text-lg font-semibold">{fig(subtotal)}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-muted">{taxLabel}</dt>
              <dd className="num text-lg font-semibold">{fig(tax)}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-muted">Net total (CAD)</dt>
              <dd className={`num text-xl font-semibold ${checked ? "text-brand-primary" : "text-ink"}`} data-testid="net-total">
                {total === null ? "—" : `CAD ${money2(total)}`}
              </dd>
            </div>
          </dl>
          <div className="flex items-center gap-3">{children}</div>
        </div>
      </div>
      <p className="mt-1.5 text-right text-xs text-ink-muted" role="status">
        {status}
      </p>
    </div>
  );
}
