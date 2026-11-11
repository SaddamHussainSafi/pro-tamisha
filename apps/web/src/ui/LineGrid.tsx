import { useImperativeHandle, useRef, useState, type ClipboardEvent, type FocusEvent, type KeyboardEvent, type ReactNode, type Ref } from "react";
import { flushSync } from "react-dom";
import type { UnitCode } from "../api";
import {
  applyPaste,
  ensureRows,
  isBlock,
  isFilled,
  keyMove,
  move,
  parseClipboard,
  pickProduct,
  resolveProduct,
  searchProducts,
  type GridLine,
  type GridProduct,
  type Move,
  type NavCol,
  type PasteField,
  type Pos,
} from "../lib/lineGrid";

/** A grid column. `sn` row number; `item` product autocomplete; `qty`, `note` text cells;
 * `unit` and `lot` dropdown cells; `display` read-only figures (LB, rate, gross, ...). */
export type GridColumn = {
  key: string;
  header: ReactNode;
  kind: "sn" | "item" | "qty" | "unit" | "lot" | "note" | "display";
  /** CSS width for the <col>; the Item column takes the rest. */
  width?: string;
  align?: "right";
  /** Enter stops here (the entry path: Item → Qty → next row). */
  enter?: boolean;
  /** Accessible name of the cell, e.g. "Quantity". "line N" is added. */
  label?: string;
  /** Header tooltip / description. */
  title?: string;
  render?: (l: GridLine, i: number) => ReactNode;
  options?: (l: GridLine, i: number) => { value: string; label: string }[];
  /** Tint the cell (e.g. not enough stock in the batch). */
  warn?: (l: GridLine) => boolean;
};

export type LineGridHandle = { focusFirstEmpty: () => void; focusRow: (row: number) => void };

const UNIT_LABEL: Record<UnitCode, string> = { BOX: "boxes", PIECE: "pieces", LB: "lb", KG: "kg" };
const PASTE: Record<GridColumn["kind"], PasteField> = { sn: null, item: "item", qty: "qty", unit: "unit", lot: null, note: "note", display: null };
const editable = (c: GridColumn) => !["sn", "display"].includes(c.kind);
/** Dropdown cells show their chevron only on the row being worked on (hover / cursor). */
const CHEVRON = (
  <svg aria-hidden="true" viewBox="0 0 16 16" className="pointer-events-none absolute right-1.5 top-1/2 size-3.5 -translate-y-1/2 text-ink-muted opacity-0 group-focus-within:opacity-100 group-hover:opacity-100"><path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
);

/** Spreadsheet-style line entry: 20+ rows always open, more appear as you fill them. Enter goes
 * Item → Qty → next row, Tab walks every cell, arrows move around, a block pasted from Excel
 * fills rows. The parent owns the rows; the server prices them (see POST /sales-orders/preview). */
export function LineGrid({
  label,
  columns,
  lines,
  setLines,
  products,
  maxRows,
  problem,
  onPasted,
  onDone,
  onActiveRow,
  handle,
}: {
  label: string;
  columns: GridColumn[];
  lines: GridLine[];
  setLines: (fn: (ls: GridLine[]) => GridLine[]) => void;
  products: GridProduct[];
  maxRows: number;
  problem: (l: GridLine, i: number) => string | null;
  /** After a block paste: how many rows' items matched no product. */
  onPasted?: (rows: number, unmatched: number) => void;
  /** Enter on an empty row's Item: the lines are done (e.g. focus "Check prices and stock"). */
  onDone?: () => void;
  /** The row the cursor is in (null when focus leaves the grid). */
  onActiveRow?: (row: number | null) => void;
  handle?: Ref<LineGridHandle>;
}) {
  const cells = useRef(new Map<string, HTMLInputElement | HTMLSelectElement>());
  const now = useRef(lines);
  now.current = lines;
  const [menu, setMenu] = useState<{ row: number; hits: GridProduct[]; hl: number } | null>(null);
  const nav: NavCol[] = columns.map((c) => ({ editable: editable(c), enter: c.enter }));
  const fields = columns.map((c) => PASTE[c.kind]);
  const byId = new Map(products.map((p) => [p.id, p]));
  const itemCol = columns.findIndex((c) => c.kind === "item");

  const focusCell = (p: Pos) => {
    const el = cells.current.get(`${p.row}:${p.col}`);
    if (!el) return;
    el.focus();
    if (el instanceof HTMLInputElement) el.select();
    el.scrollIntoView({ block: "nearest" });
  };
  useImperativeHandle(handle, () => ({
    focusFirstEmpty: () => {
      const row = lines.findIndex((l) => !isFilled(l) || (l.prefilled && !l.qty.trim()));
      const r = row === -1 ? lines.length - 1 : row;
      const pre = lines[r]?.prefilled;
      focusCell({ row: r, col: pre ? columns.findIndex((c) => c.kind === "qty") : itemCol });
    },
    focusRow: (row) => focusCell({ row, col: itemCol }),
  }));

  const update = (row: number, fn: (l: GridLine) => GridLine) => setLines((ls) => ensureRows(ls.map((l, i) => (i === row ? fn(l) : l)), maxRows));
  /** Apply pending edits (and the spare rows they add) now, then move the cursor, so the next
   * key press already lands in the new cell. */
  const go = (from: Pos, m: Move, edits?: () => void) => {
    flushSync(() => {
      edits?.();
      setMenu(null);
    });
    const to = move(from, m, nav, Math.max(now.current.length, from.row + 1));
    const target = now.current[to.row];
    // Enter onto a last-invoice row (product already there): straight to its quantity.
    const qtyCol = columns.findIndex((c) => c.kind === "qty");
    if (m === "enter" && to.col === itemCol && target?.prefilled && !target.qty.trim() && qtyCol !== -1) to.col = qtyCol;
    focusCell(to);
  };
  const choose = (row: number, p: GridProduct) => {
    update(row, (l) => pickProduct(l, p));
    setMenu(null);
  };
  /** Leaving the Item cell: a typed name that means one product is taken; an emptied cell
   * drops the product; otherwise what was typed stays and the row shows a problem. */
  const settle = (row: number) => {
    const l = lines[row];
    if (!l) return;
    const chosen = l.product_id ? byId.get(l.product_id) : undefined;
    if (chosen && l.text === chosen.name) return;
    if (!l.text.trim()) {
      if (l.product_id) update(row, (x) => ({ ...x, product_id: "", unit_code: "", lot_id: "" }));
      return;
    }
    const p = resolveProduct(products, l.text);
    if (p) choose(row, p);
  };

  function onKey(e: KeyboardEvent<HTMLInputElement | HTMLSelectElement>, row: number, col: number, kind: GridColumn["kind"]) {
    const pos = { row, col };
    const l = lines[row] as GridLine;
    if (kind === "item") {
      const open = menu?.row === row && menu.hits.length > 0 ? menu : null;
      if (open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
        e.preventDefault();
        const n = open.hits.length;
        setMenu({ ...open, hl: (open.hl + (e.key === "ArrowDown" ? 1 : n - 1)) % n });
        return;
      }
      if (e.key === "Escape") {
        setMenu(null);
        const p = l.product_id ? byId.get(l.product_id) : undefined;
        if (p) update(row, (x) => ({ ...x, text: p.name }));
        return;
      }
      if (e.key === "Tab" && open) {
        choose(row, open.hits[open.hl] as GridProduct); // Tab takes the highlighted match, then moves on
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        if (open) return go(pos, "enter", () => choose(row, open.hits[open.hl] as GridProduct));
        if (!l.text.trim() && !l.product_id) return onDone?.();
        const p = l.product_id && byId.get(l.product_id)?.name === l.text ? byId.get(l.product_id) : resolveProduct(products, l.text);
        if (p) return go(pos, "enter", () => choose(row, p));
        const hits = searchProducts(products, l.text);
        setMenu(hits.length ? { row, hits, hl: 0 } : null); // unclear: show the choices, stay
        return;
      }
    }
    if (e.key === "Tab") return setMenu(null); // native Tab order is the grid's cell order
    const t = e.currentTarget;
    const m = keyMove(e, t instanceof HTMLInputElement ? { kind: "text", caret: t.selectionStart, caretEnd: t.selectionEnd, length: t.value.length } : { kind: "select" });
    if (!m) return;
    e.preventDefault();
    go(pos, m, kind === "item" ? () => settle(row) : undefined);
  }

  function onPaste(e: ClipboardEvent<HTMLInputElement | HTMLSelectElement>, row: number, col: number) {
    const text = e.clipboardData.getData("text/plain");
    if (!isBlock(text)) return; // one value: the cell's own paste
    e.preventDefault();
    const block = parseClipboard(text);
    const res = applyPaste(lines, { row, col }, block, fields, products, maxRows);
    setLines(() => res.lines);
    setMenu(null);
    onPasted?.(block.length, res.unmatched.length);
  }

  const ref = (row: number, col: number) => (el: HTMLInputElement | HTMLSelectElement | null) => {
    const k = `${row}:${col}`;
    if (el) cells.current.set(k, el);
    else cells.current.delete(k);
  };
  const cellCls = "block h-9 w-full min-w-0 scroll-mt-14 scroll-mb-32 border-0 bg-transparent px-2.5 text-sm text-ink outline-none placeholder:text-ink-muted/70 focus:bg-card focus:shadow-[inset_0_0_0_2px_var(--color-brand-primary)]";

  return (
    <div className="overflow-x-auto rounded-[var(--radius-ctl)] border border-line xl:overflow-visible">
      <table className="w-full min-w-[52rem] table-fixed border-collapse text-sm" aria-label={label}>
        <colgroup>
          {columns.map((c) => (
            <col key={c.key} style={c.width ? { width: c.width } : undefined} />
          ))}
        </colgroup>
        <thead>
          <tr className="bg-subtle">
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                title={c.title}
                className={`sticky top-0 z-10 border-b border-line bg-subtle px-2.5 py-2 text-xs font-semibold text-ink ${c.align === "right" ? "text-right" : "text-left"} ${c.kind === "sn" ? "text-center" : ""}`}
              >
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody
          onFocus={(e: FocusEvent<HTMLElement>) => {
            const tr = e.target.closest<HTMLElement>("tr[data-row]");
            onActiveRow?.(tr ? Number(tr.dataset.row) : null);
          }}
          onBlur={(e: FocusEvent<HTMLElement>) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node | null)) onActiveRow?.(null);
          }}
        >
          {lines.map((l, row) => {
            const prob = problem(l, row);
            const probId = `lg-prob-${l.key}`;
            return (
              <tr key={l.key} className={`group border-b border-line last:border-b-0 focus-within:bg-apple-50/30 ${row % 2 ? "bg-subtle/60" : "bg-card"}`} data-testid={`grid-row-${row + 1}`} data-row={row}>
                {columns.map((c, col) => {
                  const name = `${c.label ?? c.key}, line ${row + 1}`;
                  const common = {
                    ref: ref(row, col),
                    onKeyDown: (e: KeyboardEvent<HTMLInputElement | HTMLSelectElement>) => onKey(e, row, col, c.kind),
                    onPaste: (e: ClipboardEvent<HTMLInputElement | HTMLSelectElement>) => onPaste(e, row, col),
                    "aria-label": name,
                  };
                  const td = `border-r border-line p-0 last:border-r-0 ${c.align === "right" ? "text-right" : ""}`;
                  switch (c.kind) {
                    case "sn":
                      return (
                        <td key={c.key} className={`${td} num relative px-1 text-center text-xs ${prob ? "font-semibold text-danger" : "text-ink-muted"}`} title={prob ?? undefined}>
                          <span className={isFilled(l) ? "group-hover:invisible" : ""}>
                            {row + 1}
                            {prob && <span aria-hidden="true">!</span>}
                          </span>
                          {isFilled(l) && (
                            <button
                              type="button"
                              tabIndex={-1}
                              aria-label={`Clear line ${row + 1}`}
                              title="Clear this line"
                              className="invisible absolute inset-0 m-auto size-7 cursor-pointer rounded-full text-base text-ink-muted hover:bg-line hover:text-ink group-hover:visible"
                              onClick={() => setLines((ls) => ensureRows(ls.filter((x) => x.key !== l.key), maxRows))}
                            >
                              ×
                            </button>
                          )}
                        </td>
                      );
                    case "item": {
                      const open = menu?.row === row && menu.hits.length > 0;
                      const listId = `lg-list-${l.key}`;
                      return (
                        <td key={c.key} className={`${td} relative`}>
                          <input
                            {...common}
                            role="combobox"
                            aria-autocomplete="list"
                            aria-expanded={open}
                            aria-controls={open ? listId : undefined}
                            aria-activedescendant={open ? `${listId}-${menu.hl}` : undefined}
                            aria-invalid={prob ? true : undefined}
                            aria-describedby={prob ? probId : undefined}
                            autoComplete="off"
                            spellCheck={false}
                            className={`${cellCls} ${l.prefilled && !l.qty ? "text-ink-muted" : ""}`}
                            value={l.text}
                            onChange={(e) => {
                              const text = e.target.value;
                              const hits = searchProducts(products, text);
                              setMenu(hits.length ? { row, hits, hl: 0 } : null);
                              update(row, (x) => ({ ...x, text, ...(x.product_id && byId.get(x.product_id)?.name !== text ? { product_id: "", lot_id: "" } : {}) }));
                            }}
                            onBlur={() => {
                              settle(row);
                              setMenu((m) => (m?.row === row ? null : m));
                            }}
                          />
                          {prob && (
                            <span id={probId} className="sr-only">
                              {prob}
                            </span>
                          )}
                          {open && (
                            <ul id={listId} role="listbox" aria-label={`Products matching ${l.text}`} className="absolute left-0 top-full z-30 mt-0.5 max-h-72 w-[min(28rem,90vw)] overflow-y-auto rounded-xl border border-line bg-card py-1 shadow-[0_8px_24px_rgb(24_24_27/0.12)]">
                              {menu.hits.map((p, k) => (
                                <li
                                  key={p.id}
                                  id={`${listId}-${k}`}
                                  role="option"
                                  aria-selected={k === menu.hl}
                                  className={`flex cursor-pointer items-baseline justify-between gap-3 px-3 py-1.5 ${k === menu.hl ? "bg-apple-50/50" : "hover:bg-subtle"}`}
                                  onMouseDown={(e) => {
                                    e.preventDefault(); // keep focus in the cell
                                    go({ row, col }, "enter", () => choose(row, p));
                                  }}
                                >
                                  <span className="truncate text-ink">{p.name}</span>
                                  <span className="num shrink-0 text-xs text-ink-muted">{p.sku}</span>
                                </li>
                              ))}
                            </ul>
                          )}
                        </td>
                      );
                    }
                    case "qty":
                      return (
                        <td key={c.key} className={td}>
                          <input
                            {...common}
                            inputMode="decimal"
                            autoComplete="off"
                            className={`${cellCls} num text-right`}
                            value={l.qty}
                            onChange={(e) => update(row, (x) => ({ ...x, qty: e.target.value, prefilled: x.prefilled && !e.target.value.trim() }))}
                          />
                        </td>
                      );
                    case "note":
                      return (
                        <td key={c.key} className={td}>
                          <input {...common} maxLength={500} autoComplete="off" className={cellCls} value={l.note} onChange={(e) => update(row, (x) => ({ ...x, note: e.target.value }))} />
                        </td>
                      );
                    case "unit": {
                      const units = l.product_id ? (byId.get(l.product_id)?.units ?? []) : [];
                      return (
                        <td key={c.key} className={`${td} relative`}>
                          <select {...common} className={`${cellCls} cursor-pointer appearance-none pr-6`} value={l.unit_code} onChange={(e) => update(row, (x) => ({ ...x, unit_code: e.target.value as UnitCode, lot_id: "" }))}>
                            {!units.length && <option value="" />}
                            {units.map((u) => (
                              <option key={u} value={u}>
                                {UNIT_LABEL[u]}
                              </option>
                            ))}
                          </select>
                          {CHEVRON}
                        </td>
                      );
                    }
                    case "lot": {
                      const opts = c.options?.(l, row) ?? [];
                      return (
                        <td key={c.key} className={`${td} relative ${c.warn?.(l) ? "bg-danger/5" : ""}`} title={opts.find((o) => o.value === l.lot_id)?.label || undefined}>
                          <select {...common} className={`${cellCls} cursor-pointer appearance-none truncate pr-6 ${c.warn?.(l) ? "text-danger" : l.lot_id ? "" : "text-ink-muted"}`} value={l.lot_id} onChange={(e) => update(row, (x) => ({ ...x, lot_id: e.target.value }))}>
                            {opts.map((o) => (
                              <option key={o.value} value={o.value}>
                                {o.label}
                              </option>
                            ))}
                          </select>
                          {CHEVRON}
                        </td>
                      );
                    }
                    case "display":
                      return (
                        <td key={c.key} className={`${td} num truncate px-2.5 text-sm`}>
                          {c.render?.(l, row)}
                        </td>
                      );
                  }
                })}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
