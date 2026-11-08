import { useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { Icon } from "./Icon";
import { EmptyState } from "./primitives";

export type Column<R> = {
  key: string;
  header: string;
  cell: (r: R) => ReactNode;
  sort?: (r: R) => string | bigint;
  align?: "left" | "right";
  width?: string;
};

/** Dense, sticky-header grid with sortable columns and arrow-key row navigation. */
export function DataGrid<R>({
  rows,
  columns,
  rowKey,
  caption,
  onOpen,
  selectedKey,
  empty,
}: {
  rows: R[];
  columns: Column<R>[];
  rowKey: (r: R) => string;
  caption: string;
  onOpen?: (r: R) => void;
  selectedKey?: string;
  empty?: ReactNode;
}) {
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null);

  const sorted = useMemo(() => {
    const col = sort && columns.find((c) => c.key === sort.key);
    if (!col?.sort || !sort) return rows;
    const f = col.sort;
    return [...rows].sort((a, b) => {
      const x = f(a);
      const y = f(b);
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
    });
  }, [rows, columns, sort]);

  function onKey(e: KeyboardEvent<HTMLTableRowElement>, r: R) {
    const tr = e.currentTarget;
    if (e.key === "ArrowDown") (tr.nextElementSibling as HTMLElement | null)?.focus();
    else if (e.key === "ArrowUp") (tr.previousElementSibling as HTMLElement | null)?.focus();
    else if (e.key === "Enter" && onOpen) onOpen(r);
    else return;
    e.preventDefault();
  }

  if (rows.length === 0) return <>{empty ?? <EmptyState title="Nothing to show" />}</>;

  return (
    <div className="relative overflow-auto">
      <table className="w-full border-separate border-spacing-0">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => {
              const active = sort?.key === c.key;
              return (
                <th
                  key={c.key}
                  scope="col"
                  className={`th ${c.align === "right" ? "text-right" : ""}`}
                  style={c.width ? { width: c.width } : undefined}
                  aria-sort={active ? (sort.dir === 1 ? "ascending" : "descending") : undefined}
                >
                  {c.sort ? (
                    <button
                      type="button"
                      className={`inline-flex cursor-pointer items-center gap-1 ${c.align === "right" ? "flex-row-reverse" : ""} ${active ? "text-ink" : ""}`}
                      onClick={() => setSort((s) => (s?.key === c.key ? { key: c.key, dir: s.dir === 1 ? -1 : 1 } : { key: c.key, dir: 1 }))}
                    >
                      {c.header}
                      <Icon name="sort" size={12} />
                    </button>
                  ) : (
                    c.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => {
            const k = rowKey(r);
            return (
              <tr
                key={k}
                tabIndex={0}
                onKeyDown={(e) => onKey(e, r)}
                onClick={onOpen ? () => onOpen(r) : undefined}
                aria-selected={selectedKey === k}
                className={`outline-offset-[-2px] transition-colors duration-150 ${onOpen ? "cursor-pointer" : ""} ${selectedKey === k ? "bg-brand-accent/8" : "hover:bg-subtle"}`}
              >
                {columns.map((c) => (
                  <td key={c.key} className={`td ${c.align === "right" ? "num text-right" : ""}`}>{c.cell(r)}</td>
                ))}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
