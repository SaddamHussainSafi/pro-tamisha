import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import {
  api,
  canInWarehouse,
  has,
  hasCompanyWide,
  type AdjustReason,
  type Balance,
  type Lot,
  type LotStatus,
  type Movement,
  type Product,
  type ProductSummary,
  type Reconciliation,
  type StockCount,
  type UnitCode,
  type Warehouse,
} from "../api";
import { useAuth } from "../auth";
import { inventoryMessage, MOVEMENT_LABEL, qtyText, signed, stockUnits, wholeOnly } from "../lib/inventory";
import { DataGrid, type Column } from "../ui/DataGrid";
import { NumericInput } from "../ui/NumericInput";
import { ConfirmDialog, EmptyState, ErrorState, FilterBar, LoadingRows, PageHeader, StatusChip, type Tone } from "../ui/primitives";

const LOT: Record<LotStatus, { tone: Tone; label: string }> = {
  SALEABLE: { tone: "success", label: "Saleable" },
  QUARANTINE: { tone: "warning", label: "Quarantine" },
  DAMAGED: { tone: "danger", label: "Damaged" },
  EXPIRED: { tone: "danger", label: "Expired" },
};
function LotChip({ status, expired }: { status: LotStatus; expired: boolean }) {
  const s = expired && status === "SALEABLE" ? "EXPIRED" : status;
  return <StatusChip tone={LOT[s].tone}>{LOT[s].label}</StatusChip>;
}
const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString() : "—");
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");
const link = "font-medium text-brand-primary-hover underline-offset-2 hover:underline";

/** Re-runs `onChange` whenever committed stock changes (SSE; browser reconnects automatically). */
function useStockEvents(onChange: () => void): boolean {
  const [live, setLive] = useState(false);
  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    const es = new EventSource("/api/v1/inventory/events");
    es.addEventListener("ready", () => setLive(true));
    es.addEventListener("stock", () => onChange());
    es.onerror = () => setLive(false);
    return () => es.close();
  }, [onChange]);
  return live;
}

function LiveBadge({ live }: { live: boolean }) {
  return (
    <span className="text-xs text-ink-muted" role="status">
      {live ? "● Live — updates automatically" : "○ Reconnecting…"}
    </span>
  );
}

/* ================================ Main page ================================ */

type Tab = "stock" | "movements" | "counts" | "reconcile";

export function InventoryPage() {
  const { session } = useAuth();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab | null) ?? "stock";
  const [warehouses, setWarehouses] = useState<Warehouse[]>([]);
  const [tick, setTick] = useState(0);
  const [panel, setPanel] = useState<"none" | "opening" | "count">("none");
  const bump = useCallback(() => setTick((t) => t + 1), []);
  const live = useStockEvents(bump);

  useEffect(() => {
    api.warehouses().then((w) => setWarehouses(w.filter((x) => x.active)), () => setWarehouses([]));
  }, []);

  if (!session) return null;
  const canAdjust = has(session, "inventory.adjust");
  const canCount = has(session, "inventory.count");
  const canReconcile = hasCompanyWide(session, "inventory.reconcile");
  const tabs: { id: Tab; label: string }[] = [
    { id: "stock", label: "Stock on hand" },
    { id: "movements", label: "Stock ledger" },
    { id: "counts", label: "Counts" },
    ...(canReconcile ? [{ id: "reconcile" as Tab, label: "Reconciliation" }] : []),
  ];
  const adjustable = warehouses.filter((w) => canInWarehouse(session, "inventory.adjust", w.id));
  const countable = warehouses.filter((w) => canInWarehouse(session, "inventory.count", w.id));

  return (
    <div className="space-y-5">
      <PageHeader
        title="Inventory"
        subtitle={<>Every change is a ledger entry — stock is never edited directly. <LiveBadge live={live} /></>}
        actions={
          <>
            {canCount && countable.length > 0 && (
              <button className="btn-ghost" onClick={() => setPanel("count")}>Record count</button>
            )}
            {canAdjust && adjustable.length > 0 && (
              <button className="btn-primary" onClick={() => setPanel("opening")}>Opening balance</button>
            )}
          </>
        }
      />
      {panel === "opening" && (
        <OpeningPanel warehouses={adjustable} onDone={() => { setPanel("none"); bump(); }} onCancel={() => setPanel("none")} />
      )}
      {panel === "count" && (
        <CountPanel warehouses={countable} onDone={() => { setPanel("none"); setParams({ tab: "counts" }); bump(); }} onCancel={() => setPanel("none")} />
      )}
      <div role="tablist" aria-label="Inventory views" className="flex gap-1 border-b border-line">
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${
              tab === t.id ? "border-brand-primary text-ink" : "border-transparent text-ink-muted hover:text-ink"
            }`}
            onClick={() => setParams({ tab: t.id })}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel">
        {tab === "stock" && <StockTab tick={tick} />}
        {tab === "movements" && <MovementsTab tick={tick} />}
        {tab === "counts" && <CountsTab tick={tick} onChanged={bump} />}
        {tab === "reconcile" && canReconcile && <ReconcileTab />}
      </div>
    </div>
  );
}

/* --------------------------------- Stock ---------------------------------- */

type StatusFilter = "all" | LotStatus;

function StockTab({ tick }: { tick: number }) {
  const nav = useNavigate();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [rows, setRows] = useState<Balance[] | null>(null);
  const [total, setTotal] = useState(0);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState(false);

  const load = useCallback(async (more?: string) => {
    try {
      const page = await api.stock({ q: search.trim(), lot_status: status === "all" ? undefined : status, cursor: more, limit: 50 });
      setRows((prev) => (more && prev ? [...prev, ...page.items] : page.items));
      setTotal(page.total);
      setCursor(page.next_cursor);
      setError(false);
    } catch {
      setError(true);
    }
  }, [search, status]);
  useEffect(() => {
    const t = setTimeout(() => void load(), 200);
    return () => clearTimeout(t);
  }, [load, tick]);

  const columns: Column<Balance>[] = [
    { key: "lot", header: "Lot", cell: (r) => <Link className={link} to={`/inventory/lots/${r.lot_id}`}>{r.lot_code}</Link>, sort: (r) => r.lot_code },
    { key: "prod", header: "Product", cell: (r) => <span><span className="font-medium">{r.sku}</span> <span className="text-ink-muted">{r.product_name}</span></span>, sort: (r) => r.sku },
    { key: "unit", header: "Unit", cell: (r) => r.unit_code },
    { key: "qty", header: "On hand", align: "right", cell: (r) => <span className="num">{qtyText(r.qty)}</span> },
    { key: "res", header: "Reserved", align: "right", cell: (r) => <span className="num text-ink-muted">{qtyText(r.reserved_qty)}</span> },
    { key: "av", header: "Available", align: "right", cell: (r) => <span className="num font-semibold">{qtyText(r.available_qty)}</span> },
    { key: "exp", header: "Expires", cell: (r) => day(r.expires_at), sort: (r) => r.expires_at ?? "~" },
    { key: "st", header: "Status", cell: (r) => <LotChip status={r.lot_status} expired={r.expired} /> },
  ];

  return (
    <div className="space-y-3">
      <FilterBar
        search={search}
        onSearch={setSearch}
        placeholder="Search SKU, product or lot"
        options={[
          { value: "all", label: "All" },
          { value: "SALEABLE", label: "Saleable" },
          { value: "QUARANTINE", label: "Quarantine" },
          { value: "DAMAGED", label: "Damaged" },
        ]}
        value={status}
        onChange={setStatus}
      />
      <div className="card overflow-hidden">
        {error ? (
          <ErrorState title="Couldn't load stock" onRetry={() => void load()} />
        ) : rows === null ? (
          <LoadingRows cols={8} />
        ) : (
          <DataGrid
            rows={rows}
            columns={columns}
            rowKey={(r) => `${r.lot_id}:${r.unit_code}`}
            caption="Stock on hand by lot and unit"
            onOpen={(r) => nav(`/inventory/lots/${r.lot_id}`)}
            empty={<EmptyState title="No stock found" hint="Post an opening balance, or clear the filters." />}
          />
        )}
      </div>
      {rows && (
        <div className="flex items-center justify-between text-sm text-ink-muted">
          <span>{total > 0 && `Showing ${rows.length} of ${total}`}</span>
          {cursor && <button className="btn-ghost" onClick={() => void load(cursor)}>Load more</button>}
        </div>
      )}
    </div>
  );
}

/* ------------------------------- Movements -------------------------------- */

function MovementGrid({ rows, onReverse }: { rows: Movement[]; onReverse?: (m: Movement) => void }) {
  const columns: Column<Movement>[] = [
    { key: "at", header: "When", cell: (r) => when(r.created_at) },
    { key: "type", header: "Type", cell: (r) => MOVEMENT_LABEL[r.movement_type] + (r.reason_code ? ` · ${r.reason_code.toLowerCase().replace("_", " ")}` : "") },
    { key: "lot", header: "Lot", cell: (r) => <Link className={link} to={`/inventory/lots/${r.lot_id}`}>{r.lot_code}</Link> },
    { key: "sku", header: "SKU", cell: (r) => r.sku },
    { key: "unit", header: "Unit", cell: (r) => r.unit_code },
    {
      key: "d",
      header: "Change",
      align: "right",
      cell: (r) => <span className={`num font-semibold ${r.delta_qty.startsWith("-") ? "text-danger" : "text-success"}`}>{signed(r.delta_qty)}</span>,
    },
    { key: "after", header: "Balance after", align: "right", cell: (r) => <span className="num">{qtyText(r.qty_after)}</span> },
    { key: "who", header: "By", cell: (r) => r.actor_name ?? "system" },
    {
      key: "note",
      header: "Note",
      cell: (r) => (
        <span className="text-ink-muted">
          {r.reversed_by_id ? <StatusChip tone="neutral">Reversed</StatusChip> : null} {r.note || ""}
        </span>
      ),
    },
    ...(onReverse
      ? [{
          key: "act",
          header: "Actions",
          cell: (r: Movement) =>
            !r.reversed_by_id && !r.reverses_movement_id && !["GRN_RECEIPT", "INVOICE_DISPATCH"].includes(r.movement_type) ? (
              <button className="btn-ghost px-2 py-1 text-xs" onClick={() => onReverse(r)}>Reverse</button>
            ) : null,
        }]
      : []),
  ];
  return (
    <DataGrid
      rows={rows}
      columns={columns}
      rowKey={(r) => r.id}
      caption="Stock movements"
      empty={<EmptyState title="No movements" hint="Stock changes appear here as they are posted." />}
    />
  );
}

function MovementsTab({ tick }: { tick: number }) {
  const [type, setType] = useState("");
  const [rows, setRows] = useState<Movement[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState(false);
  const load = useCallback(async (more?: string) => {
    try {
      const p = await api.movements({ movement_type: type, cursor: more, limit: 50 });
      setRows((prev) => (more && prev ? [...prev, ...p.items] : p.items));
      setCursor(p.next_cursor);
      setTotal(p.total);
      setError(false);
    } catch {
      setError(true);
    }
  }, [type]);
  useEffect(() => void load(), [load, tick]);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-3">
        <div>
          <label htmlFor="mv-type" className="label">Type</label>
          <select id="mv-type" className="input" value={type} onChange={(e) => setType(e.target.value)}>
            <option value="">All types</option>
            {Object.entries(MOVEMENT_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
      </div>
      <div className="card overflow-hidden">
        {error ? <ErrorState title="Couldn't load the ledger" onRetry={() => void load()} /> : rows === null ? <LoadingRows cols={8} /> : <MovementGrid rows={rows} />}
      </div>
      {rows && (
        <div className="flex items-center justify-between text-sm text-ink-muted">
          <span>{total > 0 && `Showing ${rows.length} of ${total}`}</span>
          {cursor && <button className="btn-ghost" onClick={() => void load(cursor)}>Load more</button>}
        </div>
      )}
    </div>
  );
}

/* --------------------------------- Counts --------------------------------- */

const COUNT_TONE: Record<StockCount["status"], Tone> = { SUBMITTED: "info", POSTED: "success", REJECTED: "neutral" };

function CountsTab({ tick, onChanged }: { tick: number; onChanged: () => void }) {
  const { session } = useAuth();
  const [rows, setRows] = useState<StockCount[] | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [rejecting, setRejecting] = useState<StockCount | null>(null);
  const [reason, setReason] = useState("");
  const load = useCallback(async () => {
    try {
      setRows(await api.counts());
      setError(false);
    } catch {
      setError(true);
    }
  }, []);
  useEffect(() => void load(), [load, tick]);
  if (!session) return null;

  async function act(fn: () => Promise<StockCount>) {
    setMsg(null);
    try {
      await fn();
      onChanged();
    } catch (e) {
      setMsg(inventoryMessage(e));
    }
    await load();
  }

  if (error) return <div className="card"><ErrorState title="Couldn't load counts" onRetry={() => void load()} /></div>;
  if (rows === null) return <div className="card"><LoadingRows cols={5} /></div>;
  if (rows.length === 0) return <div className="card"><EmptyState title="No counts yet" hint="Record a physical count to correct stock against the shelf." /></div>;
  return (
    <div className="space-y-3">
      {msg && <div role="alert" className="alert-danger">{msg}</div>}
      {rows.map((c) => {
        const canDecide = c.status === "SUBMITTED" && canInWarehouse(session, "inventory.adjust", c.warehouse_id);
        const stale = c.lines.some((l) => l.stale);
        return (
          <section key={c.id} className="card" aria-label={`Count ${c.count_no}`}>
            <div className="flex flex-wrap items-center justify-between gap-3 p-4">
              <div>
                <button className="font-semibold text-brand-primary-hover hover:underline" aria-expanded={open === c.id} onClick={() => setOpen(open === c.id ? null : c.id)}>
                  {c.count_no}
                </button>{" "}
                <StatusChip tone={COUNT_TONE[c.status]}>{c.status.toLowerCase()}</StatusChip>{" "}
                {stale && c.status === "SUBMITTED" && <StatusChip tone="warning">stock moved since count</StatusChip>}
                <p className="text-xs text-ink-muted">
                  {c.lines.length} line(s) · counted by {c.counted_by_name ?? "—"} {when(c.created_at)}
                  {c.decided_by_name ? ` · ${c.status.toLowerCase()} by ${c.decided_by_name}` : ""}
                </p>
              </div>
              {canDecide && (
                <div className="flex gap-2">
                  <button className="btn-ghost" onClick={() => { setReason(""); setRejecting(c); }}>Reject</button>
                  <button className="btn-primary" onClick={() => void act(() => api.postCount(c.id, c.version))}>Post corrections</button>
                </div>
              )}
            </div>
            {open === c.id && (
              <div className="overflow-x-auto border-t border-line">
                <table className="w-full text-sm">
                  <caption className="sr-only">Count lines for {c.count_no}</caption>
                  <thead className="text-left text-xs text-ink-muted">
                    <tr><th className="px-4 py-2">Lot</th><th>SKU</th><th>Unit</th><th className="text-right">Expected</th><th className="text-right">Counted</th><th className="px-4 text-right">Variance</th></tr>
                  </thead>
                  <tbody>
                    {c.lines.map((l) => (
                      <tr key={`${l.lot_id}:${l.unit_code}`} className="border-t border-line">
                        <td className="px-4 py-2">{l.lot_code}{l.stale && c.status === "SUBMITTED" ? " ⚠" : ""}</td>
                        <td>{l.sku}</td>
                        <td>{l.unit_code}</td>
                        <td className="num text-right">{qtyText(l.expected_qty)}</td>
                        <td className="num text-right">{qtyText(l.counted_qty)}</td>
                        <td className={`num px-4 text-right font-semibold ${l.variance.startsWith("-") ? "text-danger" : ""}`}>{l.variance === "0.0000" ? "0" : signed(l.variance)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        );
      })}
      <ConfirmDialog
        open={rejecting !== null}
        title={`Reject ${rejecting?.count_no ?? "count"}?`}
        confirmLabel="Reject count"
        danger
        onClose={() => setRejecting(null)}
        onConfirm={() => {
          const c = rejecting;
          setRejecting(null);
          if (c) void act(() => api.rejectCount(c.id, c.version, reason));
        }}
      >
        <p>No stock changes. The counter can submit a fresh count.</p>
        <label htmlFor="rej-reason" className="label mt-3">Reason (optional)</label>
        <input id="rej-reason" className="input w-full" value={reason} onChange={(e) => setReason(e.target.value)} />
      </ConfirmDialog>
    </div>
  );
}

function CountPanel({ warehouses, onDone, onCancel }: { warehouses: Warehouse[]; onDone: () => void; onCancel: () => void }) {
  // No warehouse field: counts go to the first warehouse this user may count in.
  const wh = warehouses[0]?.id ?? "";
  const [search, setSearch] = useState("");
  const [lines, setLines] = useState<Balance[] | null>(null);
  const [counted, setCounted] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!wh) return;
    setLines(null);
    api.stock({ warehouse_id: wh, q: search.trim(), limit: 200 }).then((p) => setLines(p.items), () => setLines([]));
  }, [wh, search]);
  const key = (b: Balance) => `${b.lot_id}:${b.unit_code}`;
  const filled = (lines ?? []).filter((b) => (counted[key(b)] ?? "") !== "");

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (filled.length === 0) return setError("Enter at least one counted quantity.");
    setBusy(true);
    setError(null);
    try {
      await api.submitCount({
        warehouse_id: wh,
        lines: filled.map((b) => ({ lot_id: b.lot_id, unit_code: b.unit_code, counted_qty: counted[key(b)] ?? "0" })),
      });
      onDone();
    } catch (err) {
      setError(inventoryMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card space-y-3 p-6" onSubmit={(e) => void submit(e)} aria-label="Record stock count">
      <h2 className="font-semibold">Record a physical count</h2>
      <p className="text-sm text-ink-muted">Enter what is on the shelf. A manager reviews the variance before stock changes.</p>
      <div className="flex flex-wrap gap-3">
        <div>
          <label htmlFor="cnt-q" className="label">Find lot or SKU</label>
          <input id="cnt-q" className="input" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
      </div>
      {lines === null ? <LoadingRows cols={4} rows={3} /> : lines.length === 0 ? <p className="text-sm text-ink-muted">No stock to count.</p> : (
        <div className="max-h-80 overflow-y-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">Lots to count</caption>
            <thead className="text-left text-xs text-ink-muted"><tr><th className="py-1">Lot</th><th>SKU</th><th>Unit</th><th className="w-40">Counted</th></tr></thead>
            <tbody>
              {lines.map((b) => (
                <tr key={key(b)} className="border-t border-line">
                  <td className="py-1">{b.lot_code}</td>
                  <td>{b.sku}</td>
                  <td>{b.unit_code}</td>
                  <td>
                    <NumericInput
                      label={`Counted ${b.lot_code} ${b.unit_code}`}
                      hideLabel
                      places={wholeOnly(b.unit_code) ? 0 : 4}
                      invalidHint={wholeOnly(b.unit_code) ? "Whole numbers only" : undefined}
                      unit={b.unit_code.toLowerCase()}
                      value={counted[key(b)] ?? ""}
                      onChange={(v) => setCounted((c) => ({ ...c, [key(b)]: v }))}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {error && <div role="alert" className="alert-danger">{error}</div>}
      <div className="flex justify-end gap-2">
        <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
        <button className="btn-primary" disabled={busy}>{busy ? "Submitting…" : `Submit ${filled.length} line(s)`}</button>
      </div>
    </form>
  );
}

/* ----------------------------- Opening balance ---------------------------- */

function OpeningPanel({ warehouses, onDone, onCancel }: { warehouses: Warehouse[]; onDone: () => void; onCancel: () => void }) {
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [productId, setProductId] = useState("");
  const [product, setProduct] = useState<Product | null>(null);
  // No warehouse field: opening stock goes to the first warehouse this user may adjust.
  const wh = warehouses[0]?.id ?? "";
  const [lotCode, setLotCode] = useState("");
  const [expires, setExpires] = useState("");
  const [qty, setQty] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.products({ active: true, limit: 200 }).then((r) => setProducts(r.items), () => setProducts([]));
  }, []);
  useEffect(() => {
    setProduct(null);
    setQty({});
    if (productId) api.product(productId).then(setProduct, () => setProduct(null));
  }, [productId]);
  const units: UnitCode[] = product ? stockUnits(product) : [];

  async function submit(e: FormEvent) {
    e.preventDefault();
    const lines = units.filter((u) => (qty[u] ?? "") !== "").map((u) => ({ unit_code: u, qty: qty[u] ?? "0" }));
    if (!product || lines.length === 0) return setError("Choose a product and enter at least one quantity.");
    setBusy(true);
    setError(null);
    try {
      await api.openingBalance({
        warehouse_id: wh,
        product_id: product.id,
        lot_code: lotCode.trim() || undefined,
        expires_at: expires ? new Date(`${expires}T23:59:59`).toISOString() : undefined,
        lines,
      });
      onDone();
    } catch (err) {
      setError(inventoryMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card space-y-3 p-6" onSubmit={(e) => void submit(e)} aria-label="Opening balance">
      <h2 className="font-semibold">Opening balance</h2>
      <p className="text-sm text-ink-muted">Boxes, pieces and weight are separate stock — they are never converted into each other.</p>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <label htmlFor="ob-prod" className="label">Product</label>
          <select id="ob-prod" className="input w-full" value={productId} onChange={(e) => setProductId(e.target.value)}>
            <option value="">Choose…</option>
            {products.map((p) => <option key={p.id} value={p.id}>{p.sku} — {p.name}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="ob-lot" className="label">Lot code (optional)</label>
          <input id="ob-lot" className="input w-full" value={lotCode} onChange={(e) => setLotCode(e.target.value)} placeholder="Auto-generated" />
        </div>
        <div>
          <label htmlFor="ob-exp" className="label">Expiry{product?.tracks_expiry ? " (required)" : ""}</label>
          <input id="ob-exp" type="date" className="input w-full" value={expires} required={product?.tracks_expiry} onChange={(e) => setExpires(e.target.value)} />
        </div>
      </div>
      {product && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {units.map((u) => (
            <NumericInput
              key={u}
              label={`Quantity (${u.toLowerCase()})`}
              unit={u.toLowerCase()}
              places={wholeOnly(u) ? 0 : 4}
              invalidHint={wholeOnly(u) ? "Whole numbers only" : undefined}
              value={qty[u] ?? ""}
              onChange={(v) => setQty((q) => ({ ...q, [u]: v }))}
            />
          ))}
        </div>
      )}
      {error && <div role="alert" className="alert-danger">{error}</div>}
      <div className="flex justify-end gap-2">
        <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
        <button className="btn-primary" disabled={busy}>{busy ? "Posting…" : "Post opening balance"}</button>
      </div>
    </form>
  );
}

/* ----------------------------- Reconciliation ----------------------------- */

function ReconcileTab() {
  const [r, setR] = useState<Reconciliation | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  async function check() {
    setBusy(true);
    setMsg(null);
    try {
      setR(await api.reconciliation());
    } catch (e) {
      setMsg(inventoryMessage(e));
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => void check(), []);
  return (
    <div className="card space-y-3 p-6">
      <p className="text-sm text-ink-muted">
        Compares cached balances with the sum of the stock ledger. The ledger is the source of truth.
      </p>
      {r && (
        <p role="status" className="text-sm">
          Checked <strong>{r.checked}</strong> balances against <strong>{r.ledger_rows}</strong> ledger rows —{" "}
          {r.discrepancies.length === 0 ? <StatusChip tone="success">all match</StatusChip> : <StatusChip tone="danger">{r.discrepancies.length} mismatch(es)</StatusChip>}
          {r.rebuilt && <> · rebuilt from ledger</>}
        </p>
      )}
      {r && r.discrepancies.length > 0 && (
        <table className="w-full text-sm">
          <caption className="sr-only">Balance mismatches</caption>
          <thead className="text-left text-xs text-ink-muted"><tr><th>Lot</th><th>Unit</th><th className="text-right">Cached</th><th className="text-right">Ledger</th><th className="text-right">Cached reserved</th><th className="text-right">Active reserved</th></tr></thead>
          <tbody>
            {r.discrepancies.map((d) => (
              <tr key={`${d.lot_id}:${d.unit_code}`} className="border-t border-line">
                <td>{d.lot_code}</td><td>{d.unit_code}</td>
                <td className="num text-right">{d.cached_qty === null ? "missing" : qtyText(d.cached_qty)}</td>
                <td className="num text-right font-semibold">{qtyText(d.ledger_qty)}</td>
                <td className="num text-right">{d.cached_reserved === null ? "missing" : qtyText(d.cached_reserved)}</td>
                <td className="num text-right">{qtyText(d.active_reserved)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {msg && <div role="alert" className="alert-danger">{msg}</div>}
      <div className="flex gap-2">
        <button className="btn-ghost" disabled={busy} onClick={() => void check()}>Check again</button>
        {r && r.discrepancies.length > 0 && <button className="btn-primary" onClick={() => setConfirm(true)}>Rebuild from ledger</button>}
      </div>
      <ConfirmDialog
        open={confirm}
        title="Rebuild cached balances?"
        confirmLabel="Rebuild"
        onClose={() => setConfirm(false)}
        onConfirm={async () => {
          setConfirm(false);
          try {
            setR(await api.rebuildBalances());
          } catch (e) {
            setMsg(inventoryMessage(e));
          }
        }}
      >
        <p>Cached balances are reset to the ledger totals. The ledger itself is not changed. This is audited.</p>
      </ConfirmDialog>
    </div>
  );
}

/* -------------------------------- Lot page -------------------------------- */

const REASONS: { value: AdjustReason; label: string }[] = [
  { value: "LOST", label: "Lost / missing" },
  { value: "FOUND", label: "Found" },
  { value: "SPOILAGE", label: "Spoilage" },
  { value: "DATA_ENTRY", label: "Data entry correction" },
  { value: "OTHER", label: "Other" },
];

export function LotPage() {
  const { id = "" } = useParams();
  const { session } = useAuth();
  const [lot, setLot] = useState<Lot | null>(null);
  const [moves, setMoves] = useState<Movement[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [mode, setMode] = useState<"none" | "adjust" | "damage" | "status">("none");
  const [reverse, setReverse] = useState<Movement | null>(null);
  const [reason, setReason] = useState("");
  const [loadFailed, setLoadFailed] = useState(false);

  const load = useCallback(async () => {
    try {
      const [l, m] = await Promise.all([api.lot(id), api.movements({ lot_id: id, limit: 200 })]);
      setLot(l);
      setMoves(m.items);
      setLoadFailed(false);
    } catch {
      setLoadFailed(true);
    }
  }, [id]);
  useEffect(() => void load(), [load]);
  const live = useStockEvents(load);

  if (!session) return null;
  if (loadFailed) return <div className="card"><ErrorState title="Couldn't load this lot" detail="It may be in a warehouse you can't access." onRetry={() => void load()} /></div>;
  if (!lot) return <div className="card"><LoadingRows cols={4} /></div>;
  const canAdjust = canInWarehouse(session, "inventory.adjust", lot.warehouse_id);
  const canReverse = canInWarehouse(session, "inventory.reverse", lot.warehouse_id);
  const units = lot.balances.map((b) => b.unit_code);

  async function done(p: Promise<unknown>, ok: string) {
    setError(null);
    setNotice(null);
    try {
      await p;
      setNotice(ok);
      setMode("none");
    } catch (e) {
      setError(inventoryMessage(e));
    }
    await load();
  }

  return (
    <div className="space-y-5">
      <nav aria-label="Breadcrumb" className="text-sm"><Link className={link} to="/inventory">Inventory</Link> / {lot.lot_code}</nav>
      <PageHeader
        title={`Lot ${lot.lot_code}`}
        subtitle={<>{lot.sku} — {lot.product_name} <LiveBadge live={live} /></>}
        actions={
          canAdjust && lot.status !== "DAMAGED" ? (
            <>
              <button className="btn-ghost" onClick={() => setMode("status")}>{lot.status === "SALEABLE" ? "Quarantine" : "Change status"}</button>
              <button className="btn-ghost" onClick={() => setMode("damage")}>Record damage</button>
              <button className="btn-primary" onClick={() => setMode("adjust")}>Adjust stock</button>
            </>
          ) : undefined
        }
      />
      {notice && <div role="status" className="alert-success">{notice}</div>}
      {error && <div role="alert" className="alert-danger">{error}</div>}
      <div className="grid gap-4 lg:grid-cols-3">
        <dl className="card grid grid-cols-2 gap-x-4 gap-y-2 p-6 text-sm">
          <dt className="text-ink-muted">Status</dt><dd><LotChip status={lot.status} expired={lot.expired} /></dd>
          <dt className="text-ink-muted">Received</dt><dd>{day(lot.received_at)}</dd>
          <dt className="text-ink-muted">Expires</dt><dd>{day(lot.expires_at)}</dd>
          <dt className="text-ink-muted">Batch</dt><dd>{lot.batch_ref ?? "—"}</dd>
          {lot.parent_lot_id && (<><dt className="text-ink-muted">Damaged from</dt><dd><Link className={link} to={`/inventory/lots/${lot.parent_lot_id}`}>original lot</Link></dd></>)}
          {lot.damaged_lot_id && (<><dt className="text-ink-muted">Damaged stock</dt><dd><Link className={link} to={`/inventory/lots/${lot.damaged_lot_id}`}>{lot.lot_code}-DMG</Link></dd></>)}
        </dl>
        <div className="card overflow-x-auto lg:col-span-2">
          <table className="w-full text-sm">
            <caption className="sr-only">Balances for {lot.lot_code}</caption>
            <thead className="text-left text-xs text-ink-muted"><tr><th className="px-4 py-2">Unit</th><th className="text-right">On hand</th><th className="text-right">Reserved</th><th className="px-4 text-right">Available</th></tr></thead>
            <tbody>
              {lot.balances.map((b) => (
                <tr key={b.unit_code} className="border-t border-line" data-testid={`bal-${b.unit_code}`}>
                  <td className="px-4 py-2 font-medium">{b.unit_code}</td>
                  <td className="num text-right">{qtyText(b.qty)}</td>
                  <td className="num text-right">{qtyText(b.reserved_qty)}</td>
                  <td className="num px-4 text-right font-semibold">{qtyText(b.available_qty)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {mode === "adjust" && <AdjustForm lot={lot} units={units} onCancel={() => setMode("none")} onSubmit={(b) => void done(api.adjustStock(b), "Adjustment posted to the ledger.")} />}
      {mode === "damage" && <DamageForm lot={lot} units={units} onCancel={() => setMode("none")} onSubmit={(b) => void done(api.damageStock(b), "Damaged stock moved to the damage lot.")} />}
      {mode === "status" && (
        <StatusForm lot={lot} onCancel={() => setMode("none")} onSubmit={(status, r) => void done(api.setLotStatus(lot.id, { status, version: lot.version, reason: r }), `Lot is now ${status.toLowerCase()}.`)} />
      )}
      <section aria-labelledby="ledger-h" className="space-y-2">
        <h2 id="ledger-h" className="font-semibold">Stock ledger</h2>
        <div className="card overflow-hidden">
          <MovementGrid rows={moves} onReverse={canReverse ? (m) => { setReason(""); setReverse(m); } : undefined} />
        </div>
      </section>
      <ConfirmDialog
        open={reverse !== null}
        title="Reverse this movement?"
        confirmLabel="Post reversal"
        danger
        onClose={() => setReverse(null)}
        onConfirm={() => {
          const m = reverse;
          if (!m || reason.trim().length < 3) return;
          setReverse(null);
          void done(api.reverseMovement(m.id, reason.trim()), "Reversal posted. The original entry is kept for audit.");
        }}
      >
        <p>A compensating entry is added; the original stays in the ledger. Related legs (e.g. a damage transfer) are reversed together.</p>
        <label htmlFor="rev-reason" className="label mt-3">Reason (required)</label>
        <input id="rev-reason" className="input w-full" value={reason} minLength={3} onChange={(e) => setReason(e.target.value)} />
      </ConfirmDialog>
    </div>
  );
}

function UnitSelect({ id, units, value, onChange }: { id: string; units: UnitCode[]; value: UnitCode; onChange: (u: UnitCode) => void }) {
  return (
    <select id={id} className="input w-full" value={value} onChange={(e) => onChange(e.target.value as UnitCode)}>
      {units.map((u) => <option key={u} value={u}>{u}</option>)}
    </select>
  );
}

function AdjustForm({ lot, units, onSubmit, onCancel }: {
  lot: Lot;
  units: UnitCode[];
  onSubmit: (b: { lot_id: string; unit_code: UnitCode; delta_qty: string; reason_code: AdjustReason; note: string }) => void;
  onCancel: () => void;
}) {
  const [unit, setUnit] = useState<UnitCode>(units[0] ?? "LB");
  const [dir, setDir] = useState<"remove" | "add">("remove");
  const [qty, setQty] = useState("");
  const [reason, setReason] = useState<AdjustReason>("LOST");
  const [note, setNote] = useState("");
  const valid = qty !== "" && note.trim().length >= 3;
  return (
    <form
      className="card space-y-3 p-6"
      aria-label="Adjust stock"
      onSubmit={(e) => {
        e.preventDefault();
        if (valid) onSubmit({ lot_id: lot.id, unit_code: unit, delta_qty: dir === "remove" ? `-${qty}` : qty, reason_code: reason, note: note.trim() });
      }}
    >
      <h2 className="font-semibold">Adjust stock</h2>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <div><label htmlFor="adj-dir" className="label">Direction</label>
          <select id="adj-dir" className="input w-full" value={dir} onChange={(e) => setDir(e.target.value as "remove" | "add")}>
            <option value="remove">Remove stock</option><option value="add">Add stock</option>
          </select>
        </div>
        <div><label htmlFor="adj-unit" className="label">Unit</label><UnitSelect id="adj-unit" units={units} value={unit} onChange={setUnit} /></div>
        <NumericInput label="Quantity" unit={unit.toLowerCase()} places={wholeOnly(unit) ? 0 : 4} invalidHint={wholeOnly(unit) ? "Whole numbers only" : undefined} value={qty} onChange={setQty} />
        <div><label htmlFor="adj-reason" className="label">Reason</label>
          <select id="adj-reason" className="input w-full" value={reason} onChange={(e) => setReason(e.target.value as AdjustReason)}>
            {REASONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
        </div>
        <div><label htmlFor="adj-note" className="label">Note (required)</label><input id="adj-note" className="input w-full" value={note} onChange={(e) => setNote(e.target.value)} /></div>
      </div>
      <div className="flex justify-end gap-2">
        <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
        <button className="btn-primary" disabled={!valid}>Post adjustment</button>
      </div>
    </form>
  );
}

function DamageForm({ lot, units, onSubmit, onCancel }: {
  lot: Lot;
  units: UnitCode[];
  onSubmit: (b: { lot_id: string; unit_code: UnitCode; qty: string; note: string }) => void;
  onCancel: () => void;
}) {
  const [unit, setUnit] = useState<UnitCode>(units[0] ?? "LB");
  const [qty, setQty] = useState("");
  const [note, setNote] = useState("");
  const valid = qty !== "" && note.trim().length >= 3;
  return (
    <form className="card space-y-3 p-6" aria-label="Record damage" onSubmit={(e) => { e.preventDefault(); if (valid) onSubmit({ lot_id: lot.id, unit_code: unit, qty, note: note.trim() }); }}>
      <h2 className="font-semibold">Record damage</h2>
      <p className="text-sm text-ink-muted">Damaged stock moves to a separate damage lot and can't be sold.</p>
      <div className="grid gap-3 sm:grid-cols-3">
        <div><label htmlFor="dmg-unit" className="label">Unit</label><UnitSelect id="dmg-unit" units={units} value={unit} onChange={setUnit} /></div>
        <NumericInput label="Damaged quantity" unit={unit.toLowerCase()} places={wholeOnly(unit) ? 0 : 4} invalidHint={wholeOnly(unit) ? "Whole numbers only" : undefined} value={qty} onChange={setQty} />
        <div><label htmlFor="dmg-note" className="label">What happened (required)</label><input id="dmg-note" className="input w-full" value={note} onChange={(e) => setNote(e.target.value)} /></div>
      </div>
      <div className="flex justify-end gap-2">
        <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
        <button className="btn-primary" disabled={!valid}>Move to damage</button>
      </div>
    </form>
  );
}

function StatusForm({ lot, onSubmit, onCancel }: {
  lot: Lot;
  onSubmit: (s: "SALEABLE" | "QUARANTINE" | "EXPIRED", reason: string) => void;
  onCancel: () => void;
}) {
  const options = (["SALEABLE", "QUARANTINE", "EXPIRED"] as const).filter((s) => s !== lot.status);
  const [status, setStatus] = useState<(typeof options)[number]>(options[0] ?? "QUARANTINE");
  const [reason, setReason] = useState("");
  return (
    <form className="card space-y-3 p-6" aria-label="Change lot status" onSubmit={(e) => { e.preventDefault(); if (reason.trim().length >= 3) onSubmit(status, reason.trim()); }}>
      <h2 className="font-semibold">Change lot status</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <div><label htmlFor="st-status" className="label">New status</label>
          <select id="st-status" className="input w-full" value={status} onChange={(e) => setStatus(e.target.value as (typeof options)[number])}>
            {options.map((s) => <option key={s} value={s}>{LOT[s].label}</option>)}
          </select>
        </div>
        <div><label htmlFor="st-reason" className="label">Reason (required)</label><input id="st-reason" className="input w-full" value={reason} onChange={(e) => setReason(e.target.value)} /></div>
      </div>
      <div className="flex justify-end gap-2">
        <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
        <button className="btn-primary" disabled={reason.trim().length < 3}>Save status</button>
      </div>
    </form>
  );
}
