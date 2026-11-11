import { useEffect, useMemo, useRef, useState } from "react";
import { api, type LinePreview, type LinePreviewLine, type Product } from "../api";
import { dec, qty, toFixed } from "./decimal";
import { previewRows, toGridProduct, type GridLine, type GridProduct } from "./lineGrid";
import { useDebounced } from "./useDebounced";
import type { GridColumn } from "../ui/LineGrid";

// Data both entry screens share around <LineGrid>: the product list, the live server preview
// of every row, and the Batch / Stock / LB / Rate / Gross columns.

/** Every active product (case sizes + aliases) for the Item cell, loaded once. */
export function useGridProducts(): { products: GridProduct[]; details: Map<string, Product>; ready: boolean } {
  const [list, setList] = useState<Product[] | null>(null);
  useEffect(() => {
    api.productLookup().then(setList, () => setList([]));
  }, []);
  return useMemo(() => ({ products: (list ?? []).map(toGridProduct), details: new Map((list ?? []).map((p) => [p.id, p])), ready: list !== null }), [list]);
}

/** Live prices, pounds, batches and stock for every row with a product, from the server
 * (POST /sales-orders/preview), a moment after typing stops. Provisional until checked. */
export function useLinePreview(customer: string, warehouse: string, lines: GridLine[]) {
  const req = previewRows(lines);
  const sig = warehouse && req.lines.length ? JSON.stringify({ customer, warehouse, lines: req.lines }) : "";
  const settled = useDebounced(sig, 300);
  const [res, setRes] = useState<{ sig: string; keys: number[]; data: LinePreview } | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const keysFor = useRef(new Map<string, number[]>());
  keysFor.current.set(sig, req.keys);
  useEffect(() => {
    if (!settled) return;
    let live = true;
    const body = JSON.parse(settled) as { customer: string; warehouse: string; lines: ReturnType<typeof previewRows>["lines"] };
    const keys = keysFor.current.get(settled) ?? [];
    api.previewLines({ customer_id: body.customer || null, warehouse_id: body.warehouse, lines: body.lines }).then(
      (data) => {
        if (!live) return;
        setRes({ sig: settled, keys, data });
        setFailed(null);
        keysFor.current = new Map([[settled, keys]]);
      },
      (err: unknown) => live && setFailed(err instanceof Error ? err.message : "Couldn't get prices"),
    );
    return () => {
      live = false;
    };
  }, [settled]);
  const byKey = new Map<number, LinePreviewLine>();
  if (res && sig) res.keys.forEach((k, i) => res.data.lines[i] && byKey.set(k, res.data.lines[i] as LinePreviewLine));
  /** The preview row for a grid row, while it still describes that row's product and unit. */
  const entry = (l: GridLine) => {
    const e = byKey.get(l.key);
    return e && e.product_id === l.product_id && e.unit_code === l.unit_code ? e : undefined;
  };
  return { data: sig ? (res?.data ?? null) : null, current: !!sig && res?.sig === sig, entry, failed: sig ? failed : null };
}

export const money2 = (s: string) => qty(dec(s), 2);
/** Rates keep up to 4 decimals (per lb x box), shown with at least 2. */
export const rate = (s: string) => toFixed(dec(s), 4).replace(/(\.\d\d\d?)0+$/, "$1").replace(/(\.\d\d)0$/, "$1");
const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "2-digit" });

const dash = <span className="text-ink-muted">—</span>;

/** Stock, LB, Rate and Gross columns. `checked` overrides with the issue preview's figures. */
export function figureColumns(
  entry: (l: GridLine) => LinePreviewLine | undefined,
  checked?: (l: GridLine) => { rate: string; amount: string; weight: string | null; ok: boolean; available: string } | undefined,
  opts: { stock?: boolean; rateTitle?: string; weighable?: boolean } = {},
): GridColumn[] {
  const cols: GridColumn[] = [];
  if (opts.stock !== false)
    cols.push({
      key: "stock",
      header: "Stock",
      kind: "display",
      width: "6.5rem",
      title: "Saleable stock in this warehouse (the chosen batch only, if one is chosen)",
      render: (l) => {
        if (!l.product_id) return null;
        const c = checked?.(l);
        const e = entry(l);
        const ok = c ? c.ok : e?.stock_ok;
        const avail = c ? c.available : e?.available_qty;
        if (avail === undefined || ok === undefined) return dash;
        const short = l.qty.trim() !== "" && !ok;
        return short ? <span className="font-medium text-danger">{`Only ${qty(dec(avail), 2).replace(/\.00$/, "")}`}</span> : <span className={c ? "font-medium text-success" : "text-ink-muted"}>{c ? "In stock" : qty(dec(avail), 2).replace(/\.00$/, "")}</span>;
      },
    });
  cols.push(
    opts.weighable
      ? {
          // Boxes are sold per lb: type what the line weighs (the sheet's WEIGHT); empty = the
          // case size's weight, shown greyed when the product has one.
          key: "lb",
          header: "LB",
          kind: "weight",
          align: "right",
          width: "5.5rem",
          label: "Weight in pounds",
          title: "Pounds the line weighs. Boxes are priced per lb × this weight.",
          placeholder: (l) => {
            if (!l.product_id || (l.weight ?? "").trim()) return undefined;
            const w = entry(l)?.weight_lb;
            return w && l.qty.trim() ? money2(w) : undefined;
          },
        }
      : {
          key: "lb",
          header: "LB",
          kind: "display",
          align: "right",
          width: "5.5rem",
          title: "Pounds: quantity × the case size (or the box weight the price used)",
          render: (l) => {
            if (!l.product_id) return null;
            const w = checked?.(l)?.weight ?? entry(l)?.weight_lb;
            return w && l.qty.trim() ? money2(w) : dash;
          },
        },
    {
      key: "rate",
      header: "Rate",
      kind: "display",
      align: "right",
      width: "6rem",
      title: opts.rateTitle ?? "Price per unit from the price book (CAD)",
      render: (l) => {
        if (!l.product_id) return null;
        const c = checked?.(l);
        const e = entry(l);
        if (!c && e?.lb_rate) return <span title="Per lb × the line's weight">{rate(e.lb_rate)}/lb</span>;
        const r = c?.rate ?? e?.rate;
        return r ? <span title={e?.basis === "PER_LB_X_BOX" ? "Per lb × box weight" : (e?.price_version_code ?? undefined)}>{rate(r)}</span> : dash;
      },
    },
    {
      key: "gross",
      header: "Gross",
      kind: "display",
      align: "right",
      width: "6.75rem",
      render: (l) => {
        if (!l.product_id) return null;
        const g = checked?.(l)?.amount ?? (l.qty.trim() ? entry(l)?.line_amount : null);
        return g ? <span className="font-medium">{money2(g)}</span> : dash;
      },
    },
  );
  return cols;
}

/** Batch No column: "Oldest first" (the default, first-expiry-first-out) or one batch. */
const known = new Map<string, string>(); // batch labels seen, so a chosen batch keeps its name

export function batchColumn(entry: (l: GridLine) => LinePreviewLine | undefined, plan?: (l: GridLine) => string[] | undefined): GridColumn {
  const free = (s: string) => qty(dec(s), 2).replace(/\.00$/, "");
  return {
    key: "batch",
    header: "Batch No",
    kind: "lot",
    label: "Batch",
    width: "10.5rem",
    title: "Oldest first: stock leaves the batch that expires first. Each batch shows what is free in it.",
    warn: (l) => {
      const e = entry(l);
      return !!e && l.qty.trim() !== "" && !e.stock_ok;
    },
    options: (l) => {
      if (!l.product_id) return [{ value: "", label: "" }];
      const e = entry(l);
      const lots = e?.lots ?? [];
      for (const x of lots) known.set(x.lot_id, `${x.lot_code} · ${free(x.available_qty)} free${x.expires_at ? ` · exp ${day(x.expires_at)}` : ""}`);
      // Oldest first: the batch(es) it takes — after a check, exactly those; before, the first.
      const codes = plan?.(l) ?? (lots[0] ? [lots[0].lot_code] : []);
      const auto = codes.length ? `${codes[0]}${codes.length > 1 ? ` +${codes.length - 1}` : ""}` : e && !lots.length ? "No stock" : "Oldest first";
      const opts = [{ value: "", label: auto }, ...lots.map((x) => ({ value: x.lot_id, label: known.get(x.lot_id) as string }))];
      if (l.lot_id && !lots.some((x) => x.lot_id === l.lot_id)) opts.push({ value: l.lot_id, label: known.get(l.lot_id) ?? "Chosen batch" });
      return opts;
    },
  };
}

