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

/** Sticky bottom bar under an entry grid: actions on the left, figures, then the main actions. */
export function ActionBar({
  left,
  figures,
  status,
  children,
  oneRow,
}: {
  left?: ReactNode;
  figures: { label: string; value: ReactNode; strong?: boolean; green?: boolean; testId?: string }[];
  status?: ReactNode;
  children?: ReactNode;
  /** Keep everything on one row from lg up (a wide card, e.g. Create invoice). */
  oneRow?: boolean;
}) {
  return (
    <div className="sticky bottom-0 z-20 -mx-6 -mb-6 mt-2 rounded-b-[var(--radius-card)] border-t border-line bg-card/95 px-6 py-4 backdrop-blur supports-[backdrop-filter]:bg-card/85">
      {/* left actions, then figures and main actions packed right; a narrow card wraps the main
          actions onto a second row, still on the right */}
      <div className={`flex flex-wrap items-center justify-end gap-x-6 gap-y-3 ${oneRow ? "lg:flex-nowrap" : ""}`}>
        <div className="mr-auto flex shrink-0 flex-wrap items-center gap-3">{left}</div>
        <dl className="flex items-end gap-6 border-l border-line pl-6" aria-live="polite">
          {figures.map((f) => (
            <div key={f.label}>
              <dt className="whitespace-nowrap text-xs text-ink-muted">{f.label}</dt>
              <dd className={`num font-semibold ${f.strong ? "text-xl" : "text-lg"} ${f.green ? "text-brand-primary" : "text-ink"}`} data-testid={f.testId}>
                {f.value}
              </dd>
            </div>
          ))}
        </dl>
        {children && <div className="flex flex-wrap items-center justify-end gap-3">{children}</div>}
      </div>
      {status && (
        <p className="mt-1.5 text-right text-xs text-ink-muted" role="status">
          {status}
        </p>
      )}
    </div>
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
    <ActionBar
      oneRow
      left={left}
      status={status}
      figures={[
        { label: "Subtotal", value: fig(subtotal) },
        { label: taxLabel, value: fig(tax) },
        { label: "Net total (CAD)", value: total === null ? "—" : `CAD ${money2(total)}`, strong: true, green: checked, testId: "net-total" },
      ]}
    >
      {children}
    </ActionBar>
  );
}
