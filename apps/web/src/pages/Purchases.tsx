import { useCallback, useEffect, useId, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, ApiError, canInWarehouse, type CorrectionKind, type GrnLine, type PurchaseCorrection, type Shipment, type ShipmentDetail, type UnitCode, type Vendor } from "../api";
import { useAuth } from "../auth";
import { Modal } from "../components/gmail/PagePreview";
import { dec, isDecimal } from "../lib/decimal";
import { todayIso } from "../lib/format";
import { rate, useGridProducts } from "../lib/gridData";
import { qtyText } from "../lib/inventory";
import { billable, blankLine, ensureRows, gridProblem, resolveProduct, rowProblem, type GridLine, type GridProduct } from "../lib/lineGrid";
import { badges, CORRECTION, correctionText, costProblem, expiryIso, goodsText, gridTotal, lineAmount, matchingQuick, purchaseMessage, qtyWords, STAGE, stockNow, supplierName, toLineIn, VIEW_STAGE, type PurchaseView } from "../lib/purchases";
import { usePurchaseWarehouse } from "../lib/purchaseWarehouse";
import { moneyText } from "../lib/receiving";
import { ACCEPT, checkFile } from "../lib/scans";
import { DataGrid, type Column } from "../ui/DataGrid";
import { GridProblems } from "../ui/gridParts";
import { Icon } from "../ui/Icon";
import { LineGrid, type GridColumn, type LineGridHandle } from "../ui/LineGrid";
import { NumericInput } from "../ui/NumericInput";
import { ConfirmDialog, EmptyState, ErrorState, FilterBar, LoadingRows, PageHeader, PageSkeleton, StatusChip } from "../ui/primitives";
import { ReasonDialog } from "./Orders";
import { CostSection, DocumentsStrip, Notice } from "./Receiving";

// Purchases (owner, 2026-11-12): one flow for every supplier. Saving a purchase adds its lines
// to stock at once; airport shipments are verified on arrival; goods found on the shelf are
// added with a quick receive and matched to the supplier's invoice later.

const link = "font-medium text-brand-primary-hover underline-offset-2 hover:underline";
const day = (iso: string | null) => (iso ? new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "—");
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—");
const UNIT_LABEL: Record<UnitCode, string> = { BOX: "box", PIECE: "piece", LB: "lb", KG: "kg" };
const MAX_LINES = 200;

export function PurchaseChips({ s }: { s: Shipment }) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      <StatusChip tone={STAGE[s.stage].tone}>{STAGE[s.stage].label}</StatusChip>
      {badges(s).map((b) => (
        <StatusChip key={b.label} tone={b.tone}>{b.label}</StatusChip>
      ))}
    </span>
  );
}

/* ================================ Quick receive ================================ */

export type QuickPreset = { product_id: string; product_name: string; unit_code: UnitCode; qty: string; needed_for?: string };

function ProductField({ id, products, value, onPick }: { id: string; products: GridProduct[]; value: string; onPick: (p: GridProduct | null) => void }) {
  const [text, setText] = useState("");
  const listId = `${id}-list`;
  const chosen = products.find((p) => p.id === value);
  return (
    <>
      <input
        id={id}
        className="input"
        list={listId}
        autoComplete="off"
        placeholder="Type a product name or code"
        value={chosen && !text ? chosen.name : text}
        onChange={(e) => {
          setText(e.target.value);
          onPick(resolveProduct(products, e.target.value));
        }}
        onBlur={() => chosen && setText("")}
      />
      <datalist id={listId}>
        {products.map((p) => (
          <option key={p.id} value={p.name}>{p.sku}</option>
        ))}
      </datalist>
    </>
  );
}

/** Goods that are on the shelf but were never entered: add them to stock now, with a reason.
 * The record becomes a purchase marked "Details to complete". */
export function QuickReceiveDialog({ open, onClose, onDone, warehouseId, preset }: { open: boolean; onClose: () => void; onDone: (s: ShipmentDetail) => void; warehouseId: string; preset?: QuickPreset }) {
  const { products, details } = useGridProducts();
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [product, setProduct] = useState(preset?.product_id ?? "");
  const [unit, setUnit] = useState<UnitCode | "">(preset?.unit_code ?? "");
  const [qty, setQty] = useState(preset ? qtyText(preset.qty) : "");
  const [vendor, setVendor] = useState("");
  const [reason, setReason] = useState("");
  const [expiry, setExpiry] = useState("");
  const [askExpiry, setAskExpiry] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const formId = useId();

  useEffect(() => {
    if (!open) return;
    api.vendors({ active: true }).then(setVendors, () => setVendors([]));
    setProduct(preset?.product_id ?? "");
    setUnit(preset?.unit_code ?? "");
    setQty(preset ? qtyText(preset.qty) : "");
    setReason("");
    setError(null);
    setAskExpiry(false);
    setExpiry("");
  }, [open, preset]);

  const prod = products.find((p) => p.id === product);
  const units = prod?.units ?? (preset ? [preset.unit_code] : []);
  const whole = unit === "BOX" || unit === "PIECE";

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!product) return setError("Choose the product.");
    if (!unit) return setError("Choose the unit.");
    if (!qty.trim() || !isDecimal(qty) || dec(qty) <= 0n) return setError("Enter how many you found.");
    if (details.get(product)?.tracks_expiry && !expiry) return setError("This product needs its expiry date.");
    if (reason.trim().length < 3) return setError("Say why you are adding it (a few words).");
    setBusy(true);
    try {
      onDone(
        await api.quickReceive({
          warehouse_id: warehouseId,
          product_id: product,
          unit_code: unit,
          qty: qty.trim(),
          vendor_id: vendor || null,
          reason: reason.trim(),
          expires_at: expiryIso(expiry),
          needed_for: preset?.needed_for ?? null,
        }),
      );
    } catch (err) {
      if (err instanceof ApiError && err.code === "EXPIRY_REQUIRED") setAskExpiry(true);
      setError(err instanceof ApiError && err.code === "EXPIRY_REQUIRED" ? "This product needs its expiry date." : purchaseMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      title="Quick receive"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
          <button type="submit" form={formId} className="btn-primary" disabled={busy || !warehouseId}>{busy ? "Adding…" : "Add to stock"}</button>
        </>
      }
    >
      <form id={formId} className="space-y-4 p-5" onSubmit={submit} aria-label="Quick receive">
        <p className="text-sm text-ink-muted">
          For goods that are in the warehouse but were never entered. They are added to stock now, so the order can be packed and invoiced. The supplier's invoice and cost can follow: this stays on the “To complete” list until they do.
        </p>
        <div>
          <label className="label" htmlFor={`${formId}-p`}>Product</label>
          {preset ? <p id={`${formId}-p`} className="input bg-subtle">{preset.product_name}</p> : <ProductField id={`${formId}-p`} products={products} value={product} onPick={(p) => { setProduct(p?.id ?? ""); setUnit(p ? (p.units[0] ?? "") : ""); }} />}
        </div>
        <div className="grid grid-cols-[minmax(0,1fr)_8rem] gap-3">
          <NumericInput label="Quantity found" value={qty} onChange={setQty} places={whole ? 0 : 2} />
          <div>
            <label className="label" htmlFor={`${formId}-u`}>Unit</label>
            <select id={`${formId}-u`} className="input" value={unit} onChange={(e) => setUnit(e.target.value as UnitCode)}>
              {!unit && <option value="">Choose…</option>}
              {units.map((u) => <option key={u} value={u}>{UNIT_LABEL[u]}</option>)}
            </select>
          </div>
        </div>
        <div>
          <label className="label" htmlFor={`${formId}-v`}>Supplier, if you know it</label>
          <select id={`${formId}-v`} className="input" value={vendor} onChange={(e) => setVendor(e.target.value)}>
            <option value="">Supplier invoice to follow</option>
            {vendors.map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}
          </select>
        </div>
        {(askExpiry || details.get(product)?.tracks_expiry) && (
          <div>
            <label className="label" htmlFor={`${formId}-x`}>Expiry date</label>
            <input id={`${formId}-x`} type="date" className="input" value={expiry} onChange={(e) => setExpiry(e.target.value)} />
          </div>
        )}
        <div>
          <label className="label" htmlFor={`${formId}-r`}>Why are you adding it? (required)</label>
          <input id={`${formId}-r`} className="input" maxLength={500} value={reason} placeholder="e.g. Delivered yesterday, the purchase was not entered" onChange={(e) => setReason(e.target.value)} />
        </div>
        {error && <Notice tone="error">{error}</Notice>}
        {!warehouseId && <Notice tone="error">Your account can't add stock. Ask a manager or the warehouse.</Notice>}
      </form>
    </Modal>
  );
}

/** Offered right where missing stock blocks work (packing sheet, invoice screens):
 * "Add 5 BOX found in the warehouse". Needs `grn.record`; others see who can do it. */
export function AddFoundStock({ warehouseId, productId, productName, unit, qty, neededFor, onAdded }: { warehouseId: string; productId: string; productName: string; unit: UnitCode; qty: string; neededFor?: string; onAdded: (s: ShipmentDetail) => void }) {
  const { session } = useAuth();
  const [open, setOpen] = useState(false);
  const preset = useMemo(() => ({ product_id: productId, product_name: productName, unit_code: unit, qty, needed_for: neededFor }), [productId, productName, unit, qty, neededFor]);
  if (!session) return null;
  if (!canInWarehouse(session, "grn.record", warehouseId)) return <span className="text-xs text-ink-muted">Found in the warehouse? Ask the warehouse or a manager to add it with Quick receive.</span>;
  return (
    <>
      <button type="button" className="btn-ghost gap-1.5 px-3.5 py-1.5 text-xs print:hidden" onClick={() => setOpen(true)}>
        <Icon name="plus" size={14} />
        Add {qtyText(qty)} {unit} found in the warehouse
      </button>
      <QuickReceiveDialog
        open={open}
        warehouseId={warehouseId}
        preset={preset}
        onClose={() => setOpen(false)}
        onDone={(s) => {
          setOpen(false);
          onAdded(s);
        }}
      />
    </>
  );
}

/* ================================ List (a tab of the Orders page) ================================ */

const VIEWS: { value: PurchaseView; label: string }[] = [
  { value: "all", label: "All" },
  { value: "complete", label: "To complete" },
  { value: "verify", label: "To verify" },
  { value: "stock", label: "In stock" },
  { value: "old", label: "Not in stock yet" },
  { value: "cancelled", label: "Cancelled" },
];
const isView = (v: string | null): v is PurchaseView => !!v && VIEWS.some((x) => x.value === v);

export function PurchasesTab({ search, onSearch }: { search: string; onSearch: (s: string) => void }) {
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const view: PurchaseView = isView(params.get("view")) ? (params.get("view") as PurchaseView) : "all";
  const [rows, setRows] = useState<Shipment[] | null>(null);
  const [total, setTotal] = useState(0);
  const [cursor, setCursor] = useState<string | null>(null);
  const [counts, setCounts] = useState<Partial<Record<PurchaseView, number>>>({});
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(
    async (more?: string) => {
      try {
        const page = await api.shipments({ stage: VIEW_STAGE[view], q: search.trim() || undefined, cursor: more, limit: 50 });
        setRows((prev) => (more && prev ? [...prev, ...page.items] : page.items));
        setTotal(page.total);
        setCursor(page.next_cursor);
        setError(null);
      } catch (err) {
        setError(err);
      }
    },
    [view, search],
  );
  useEffect(() => {
    const t = setTimeout(() => void load(), 200);
    return () => clearTimeout(t);
  }, [load]);
  useEffect(() => {
    // How much is waiting: shown on the filter chips.
    for (const v of ["complete", "verify", "old"] as const) {
      api.shipments({ stage: VIEW_STAGE[v], limit: 1 }).then((p) => setCounts((c) => ({ ...c, [v]: p.total })), () => undefined);
    }
  }, [rows]);

  const options = VIEWS.filter((v) => v.value !== "old" || (counts.old ?? 0) > 0 || view === "old").map((v) => ({ ...v, count: v.value === "complete" || v.value === "verify" || v.value === "old" ? counts[v.value] || undefined : undefined }));
  const setView = (v: PurchaseView) => {
    const next = new URLSearchParams(params);
    if (v === "all") next.delete("view");
    else next.set("view", v);
    setParams(next, { replace: true });
  };

  const columns: Column<Shipment>[] = [
    {
      key: "supplier",
      header: "Supplier",
      cell: (s) => (
        <>
          <Link className={link} to={`/purchases/${s.id}`}>
            {s.vendor_name || <span className="italic">Supplier invoice to follow</span>}
          </Link>
          {/* phones: the Status column is off-screen, so what needs doing shows here too */}
          <span className="mt-1.5 block sm:hidden"><PurchaseChips s={s} /></span>
        </>
      ),
      sort: (s) => supplierName(s),
    },
    { key: "inv", header: "Invoice no.", cell: (s) => <span>{s.vendor_invoice_no || "—"} <span className="block text-xs text-ink-muted">{s.shipment_no}</span></span>, sort: (s) => s.vendor_invoice_no ?? "" },
    { key: "date", header: "Date", cell: (s) => day(s.invoice_date ?? s.created_at), sort: (s) => s.invoice_date ?? s.created_at },
    { key: "goods", header: "Goods", cell: (s) => <span className="text-sm">{goodsText(s.items, s.line_count)}</span> },
    {
      key: "total",
      header: "Total",
      align: "right",
      cell: (s) =>
        s.needs_details || (s.line_count > 0 && s.unpriced_lines === s.line_count) ? (
          <span className="text-sm text-ink-muted">Cost to follow</span>
        ) : (
          <span className="num">
            {moneyText(s.total, s.currency)}
            {s.unpriced_lines > 0 && <span className="block text-xs font-normal text-ink-muted">+ {s.unpriced_lines} without a cost</span>}
          </span>
        ),
      sort: (s) => dec(s.total),
    },
    { key: "status", header: "Status", cell: (s) => <PurchaseChips s={s} /> },
  ];

  return (
    <>
      <FilterBar search={search} onSearch={onSearch} options={options} value={view} onChange={setView} placeholder="Search supplier, invoice or waybill" />
      {view === "complete" && (rows?.length ?? 0) > 0 && (
        <p className="alert-warning" role="note">
          These goods are in stock, but their supplier invoice is still missing. Open one and choose <strong>Complete details</strong>, or scan the invoice.
        </p>
      )}
      <section className="card overflow-clip">
        {error ? (
          <ErrorState title="Couldn't load purchases" error={error} onRetry={() => void load()} />
        ) : rows === null ? (
          <LoadingRows cols={6} />
        ) : (
          <>
            <DataGrid
              rows={rows}
              columns={columns}
              rowKey={(s) => s.id}
              caption={`Purchases (${total})`}
              onOpen={(s) => nav(`/purchases/${s.id}`)}
              empty={<EmptyState title="No purchases here" hint={view === "all" ? "Enter one with New › New purchase when goods are bought. Stock is added as soon as it is saved." : "Try another filter."} />}
            />
            {cursor && (
              <div className="flex justify-center border-t border-line p-4">
                <button className="btn-ghost" onClick={() => void load(cursor)}>Load more</button>
              </div>
            )}
          </>
        )}
      </section>
    </>
  );
}

/* ================================ New purchase / complete a quick receive ================================ */

const FORM = "purchase-form";

export function NewPurchasePage() {
  const { session } = useAuth();
  const wh = usePurchaseWarehouse(session);
  if (!session) return null;
  return (
    <div className="space-y-5">
      <PageHeader
        title="New purchase"
        subtitle="Enter the seller's invoice. Its goods are added to stock the moment you save."
        actions={<button type="submit" form={FORM} className="btn-primary">Save &amp; add to stock</button>}
      />
      <ScanInstead warehouseId={wh} />
      <PurchaseForm warehouseId={wh} />
    </div>
  );
}

/** "Scan or upload the seller's invoice": the file is read, a person checks every line, then
 * "Save & add to stock" on the review screen. The original is kept unaltered. */
function ScanInstead({ warehouseId }: { warehouseId: string }) {
  const nav = useNavigate();
  const { session } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!session || !warehouseId || !canInWarehouse(session, "scan.create", warehouseId)) return null;
  async function onFile(file: File | undefined, input: HTMLInputElement) {
    setError(null);
    if (!file) return;
    const bad = checkFile(file);
    input.value = "";
    if (bad) return setError(bad);
    setBusy(true);
    try {
      const job = await api.uploadScan(file, { warehouse_id: warehouseId, job_type: "VENDOR_DOCUMENT", note: "Purchase invoice" });
      nav(`/scan/${job.id}`);
    } catch (err) {
      setError(purchaseMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="card flex flex-wrap items-center gap-x-4 gap-y-3 p-4 sm:px-6" aria-label="Scan or upload the invoice">
      <span aria-hidden className="grid size-10 shrink-0 place-items-center rounded-full bg-subtle text-ink-muted"><Icon name="scan" size={18} /></span>
      <p className="min-w-0 flex-1 text-sm">
        <span className="font-medium">Have the seller's invoice?</span> <span className="text-ink-muted">Upload it and the lines are read for you. You check them, then save. The original file is kept as it is.</span>
      </p>
      <div className="flex flex-wrap gap-2">
        <label className={`btn-ghost cursor-pointer gap-2 ${busy ? "pointer-events-none opacity-60" : ""}`}>
          <Icon name="upload" size={16} />
          {busy ? "Uploading…" : "Upload invoice"}
          <input data-testid="invoice-upload" type="file" accept={ACCEPT} className="sr-only" onChange={(e) => void onFile(e.target.files?.[0], e.target)} />
        </label>
        <Link className="btn-ghost gap-2" to="/scan"><Icon name="scan" size={16} />Scan Center</Link>
      </div>
      {error && <div className="w-full"><Notice tone="error">{error}</Notice></div>}
    </section>
  );
}

function PurchaseForm({ warehouseId, complete, onCompleted }: { warehouseId: string; complete?: ShipmentDetail; /** Completing a quick receive: the page it is on shows the result. */ onCompleted?: (s: ShipmentDetail) => void }) {
  const nav = useNavigate();
  const { products, details } = useGridProducts();
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [vendor, setVendor] = useState(complete?.vendor_id ?? "");
  const [invoiceNo, setInvoiceNo] = useState(complete?.vendor_invoice_no ?? "");
  const [invoiceDate, setInvoiceDate] = useState(complete?.invoice_date ?? todayIso());
  const [airport, setAirport] = useState(false);
  const [awb, setAwb] = useState("");
  const [expected, setExpected] = useState(todayIso());
  const [pieces, setPieces] = useState("");
  const [note, setNote] = useState("");
  const [lines, setLines] = useState<GridLine[]>(() =>
    ensureRows(
      (complete?.counted ?? []).map((c) => ({ ...blankLine(), product_id: c.product_id, unit_code: c.unit_code, qty: qtyText(c.accepted_qty), text: c.product_name })),
      MAX_LINES,
      8,
      3,
    ),
  );
  const [openQuick, setOpenQuick] = useState<Shipment[]>([]);
  const [same, setSame] = useState<Record<string, boolean | undefined>>({});
  const [reason, setReason] = useState("");
  const [needReason, setNeedReason] = useState(false);
  const [dup, setDup] = useState<{ id: string; no: string } | null>(null);
  const [activeRow, setActiveRow] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const grid = useRef<LineGridHandle>(null);
  const saveBtn = useRef<HTMLButtonElement>(null);
  const sending = useRef(false);

  useEffect(() => {
    api.vendors({ active: true }).then(setVendors, () => setVendors([]));
    api.shipments({ stage: "TO_COMPLETE", limit: 200 }).then((p) => setOpenQuick(p.items), () => setOpenQuick([]));
  }, []);

  const used = billable(lines);
  const quick = matchingQuick(openQuick.filter((q) => !warehouseId || q.warehouse_id === warehouseId), used, complete?.id);
  const undecided = quick.filter((q) => same[q.id] === undefined);
  const totals = gridTotal(lines);
  const currency = vendors.find((v) => v.id === vendor)?.currency ?? complete?.currency ?? "CAD";
  // A product that tracks expiry needs its date (new stock only: goods already on the shelf have theirs).
  const onShelf = new Set((complete?.counted ?? []).map((c) => `${c.product_id}:${c.unit_code}`));
  const needsExpiry = (l: GridLine) => !!details.get(l.product_id)?.tracks_expiry && !onShelf.has(`${l.product_id}:${l.unit_code}`);
  const expiryProblem = (l: GridLine) => (needsExpiry(l) && !l.expires ? "This product needs its expiry date." : null);
  const problemFor = (l: GridLine, i: number) => (i === activeRow && !l.product_id ? null : (rowProblem(l, i, lines) ?? (l.product_id ? (costProblem(l.cost ?? "") ?? expiryProblem(l)) : null)));
  const problems = lines.flatMap((l, row) => {
    const text = problemFor(l, row);
    return text ? [{ row, text }] : [];
  });
  const toGrid = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key !== "Enter" || e.shiftKey) return;
    e.preventDefault();
    grid.current?.focusFirstEmpty();
  };

  async function submit(confirmDuplicate = false) {
    if (sending.current) return;
    setError(null);
    const costBad = used.map((l) => costProblem(l.cost ?? "") ?? expiryProblem(l)).find(Boolean);
    const problem = !warehouseId
      ? "Your account can't enter purchases."
      : !vendor
        ? "Choose the supplier."
        : (gridProblem(lines) ?? costBad ?? (undecided.length ? "Say whether the goods already added by quick receive are on this invoice." : null) ?? (airport && pieces.trim() && !/^\d+$/.test(pieces.trim()) ? "Pieces on the airway bill must be a whole number." : null));
    if (problem) return setError(problem);
    const header = {
      vendor_id: vendor,
      vendor_invoice_no: invoiceNo.trim() || null,
      invoice_date: invoiceDate || null,
      absorb_quick_ids: quick.filter((q) => same[q.id]).map((q) => q.id),
      reason: reason.trim() || null,
      confirm_duplicate: confirmDuplicate,
      lines: used.map(toLineIn),
    };
    setBusy(true);
    sending.current = true;
    try {
      const saved = complete
        ? await api.completePurchase(complete.id, { ...header, version: complete.version })
        : await api.createPurchase({
            ...header,
            warehouse_id: warehouseId,
            note: note.trim() || null,
            is_airport: airport,
            awb_no: airport ? awb.trim() || null : null,
            expected_on: airport ? expected || null : null,
            expected_pieces: airport && pieces.trim() ? Number(pieces.trim()) : null,
          });
      if (complete && onCompleted) onCompleted(saved);
      else nav(`/purchases/${saved.id}`, { state: { saved: complete ? "completed" : "created" } });
    } catch (err) {
      if (err instanceof ApiError && err.code === "INVOICE_ALREADY_ENTERED") {
        setDup({ id: String(err.extra.shipment_id ?? ""), no: String(err.extra.shipment_no ?? "") });
      } else {
        if (err instanceof ApiError && err.code === "MATCH_REASON_REQUIRED") setNeedReason(true);
        setError(purchaseMessage(err));
      }
    } finally {
      sending.current = false;
      setBusy(false);
    }
  }

  const columns: GridColumn[] = [
    { key: "sn", header: "S/N", kind: "sn", width: "3rem" },
    { key: "item", header: "Product", kind: "item", enter: true, label: "Product" },
    { key: "qty", header: "Qty", kind: "qty", enter: true, align: "right", width: "5.5rem", label: "Quantity" },
    { key: "unit", header: "Unit", kind: "unit", width: "5.5rem", label: "Unit" },
    { key: "cost", header: "Cost per unit", kind: "cost", enter: true, align: "right", width: "8rem", label: "Cost per unit", title: "What one unit costs on the invoice. Leave empty if the cost is not known yet.", placeholder: () => "0.00" },
    ...(used.some(needsExpiry) ? [{ key: "expiry", header: "Expiry", kind: "date", width: "9.5rem", label: "Expiry date", title: "For products that track expiry." } as GridColumn] : []),
    { key: "amount", header: "Amount", kind: "display", align: "right", width: "8rem", render: (l) => lineAmount(l.qty, l.cost ?? "") ?? "" },
  ];

  return (
    <form
      id={FORM}
      className="card space-y-5 p-5 sm:p-6"
      aria-label={complete ? "Complete purchase details" : "New purchase"}
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        void submit();
      }}
    >
      <div className="grid gap-x-5 gap-y-4 md:grid-cols-12">
        <div className="md:col-span-6">
          <label className="label" htmlFor="pu-v">Supplier</label>
          <select id="pu-v" className="input" value={vendor} onChange={(e) => setVendor(e.target.value)} onKeyDown={toGrid}>
            <option value="">Choose…</option>
            {vendors.map((v) => <option key={v.id} value={v.id}>{v.name} ({v.code})</option>)}
          </select>
          <p className="mt-1 text-xs text-ink-muted">Any seller: importers, local suppliers, other wholesalers. <Link className={link} to="/suppliers" target="_blank">Add a new supplier</Link></p>
        </div>
        <div className="md:col-span-3">
          <label className="label" htmlFor="pu-inv">Invoice no.</label>
          <input id="pu-inv" className="input" maxLength={64} value={invoiceNo} onChange={(e) => setInvoiceNo(e.target.value)} onKeyDown={toGrid} />
        </div>
        <div className="md:col-span-3">
          <label className="label" htmlFor="pu-date">Invoice date</label>
          <input id="pu-date" type="date" className="input" value={invoiceDate} onChange={(e) => setInvoiceDate(e.target.value)} onKeyDown={toGrid} />
        </div>
        {!complete && (
          <div className="md:col-span-12">
            <label className="flex items-start gap-2.5 text-sm">
              <input type="checkbox" className="mt-0.5 size-4" checked={airport} onChange={(e) => setAirport(e.target.checked)} />
              <span>
                <span className="font-medium">Airport shipment, verify pieces on arrival</span>
                <span className="block text-ink-muted">The goods are in stock as soon as you save, even before pickup, so invoices can be printed when they arrive. The pieces are checked later.</span>
              </span>
            </label>
          </div>
        )}
        {airport && !complete && (
          <>
            <div className="md:col-span-4">
              <label className="label" htmlFor="pu-awb">Airway bill no.</label>
              <input id="pu-awb" className="input" maxLength={64} value={awb} onChange={(e) => setAwb(e.target.value)} onKeyDown={toGrid} />
            </div>
            <div className="md:col-span-4">
              <label className="label" htmlFor="pu-exp">Expected arrival</label>
              <input id="pu-exp" type="date" className="input" value={expected} onChange={(e) => setExpected(e.target.value)} onKeyDown={toGrid} />
            </div>
            <div className="md:col-span-4">
              <label className="label" htmlFor="pu-pcs">Pieces on the airway bill</label>
              <input id="pu-pcs" className="input num" inputMode="numeric" maxLength={7} placeholder="Optional" value={pieces} onChange={(e) => setPieces(e.target.value.replace(/\D/g, ""))} onKeyDown={toGrid} />
            </div>
            <p className="text-xs text-ink-muted md:col-span-12">CFIA documents, the airway bill and freight or duty costs are added on the next screen.</p>
          </>
        )}
        {!complete && (
          <div className="md:col-span-12">
            <label className="label" htmlFor="pu-note">Note (optional)</label>
            <input id="pu-note" className="input" maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} onKeyDown={toGrid} />
          </div>
        )}
      </div>

      <div className="space-y-2">
        <LineGrid label="Purchase lines" columns={columns} lines={lines} setLines={setLines} products={products} maxRows={MAX_LINES} problem={problemFor} handle={grid} onActiveRow={setActiveRow} onDone={() => saveBtn.current?.focus()} thumbs addRows={10} />
        <p className="flex items-start gap-2 text-xs text-ink-muted">
          <Icon name="info" size={14} className="mt-0.5 shrink-0" />
          <span>Enter moves Product → Qty → Cost → next line, Tab moves across; paste rows from Excel straight into the grid. A kg line for a product kept in lb is saved in lb.</span>
        </p>
        <GridProblems items={problems} />
      </div>

      {quick.length > 0 && (
        <fieldset className="space-y-3 rounded-ctl border border-warning/40 bg-warning/5 p-4">
          <legend className="px-1 text-sm font-semibold">Already added by quick receive</legend>
          <p className="text-sm text-ink-muted">These goods were put into stock earlier and are waiting for their invoice. If this is that invoice, they are not added a second time.</p>
          {quick.map((q) => (
            <div key={q.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm" role="group" aria-label={`Quick receive ${q.shipment_no}`}>
              <span className="min-w-0 flex-1">
                <span className="font-medium">{goodsText(q.items, q.line_count)}</span>
                <span className="block text-xs text-ink-muted">{q.shipment_no} · added {when(q.created_at)} by {q.created_by_name}{q.quick_reason ? ` · “${q.quick_reason}”` : ""}</span>
              </span>
              <label className="inline-flex items-center gap-1.5">
                <input type="radio" name={`same-${q.id}`} checked={same[q.id] === true} onChange={() => setSame((s) => ({ ...s, [q.id]: true }))} />
                Same goods, on this invoice
              </label>
              <label className="inline-flex items-center gap-1.5">
                <input type="radio" name={`same-${q.id}`} checked={same[q.id] === false} onChange={() => setSame((s) => ({ ...s, [q.id]: false }))} />
                Different goods
              </label>
            </div>
          ))}
        </fieldset>
      )}
      {(needReason || reason) && (
        <div>
          <label className="label" htmlFor="pu-reason">Why does the invoice differ from what was added to stock? (required)</label>
          <input id="pu-reason" className="input" maxLength={500} value={reason} autoFocus onChange={(e) => setReason(e.target.value)} />
          <p className="mt-1 text-xs text-ink-muted">The stock already added stays as it is. Only the difference is corrected, with this reason, after a manager's OK.</p>
        </div>
      )}
      {error && <p className="alert-danger" role="alert">{error}</p>}

      <div className="sticky bottom-0 -mx-5 -mb-5 flex flex-wrap items-center gap-x-6 gap-y-3 rounded-b-[var(--radius-card)] border-t border-line bg-card px-5 py-4 sm:-mx-6 sm:-mb-6 sm:px-6">
        <p className="mr-auto text-sm text-ink-muted" role="status">
          {used.length === 0 ? "Type or paste the products on the invoice." : (
            <>
              {used.length} line{used.length === 1 ? "" : "s"} · Invoice total <span className="num font-semibold text-ink">{moneyText(totals.total, currency)}</span>
              {totals.unpriced > 0 && <> · {totals.unpriced} without a cost</>}
            </>
          )}
        </p>
        <Link className="btn-ghost" to={complete ? `/purchases/${complete.id}` : "/orders?tab=purchases"}>Cancel</Link>
        <button ref={saveBtn} className="btn-primary" disabled={busy}>{busy ? "Saving…" : complete ? "Save details" : "Save & add to stock"}</button>
      </div>

      <ConfirmDialog open={dup !== null} title="This invoice was already entered" confirmLabel="Enter it again" danger onClose={() => setDup(null)} onConfirm={() => { setDup(null); void submit(true); }}>
        <p>
          Invoice {invoiceNo} from this supplier is already saved as <Link className={link} to={`/purchases/${dup?.id}`}>{dup?.no}</Link>. Entering it again adds the goods to stock a second time.
        </p>
      </ConfirmDialog>
    </form>
  );
}

/* ================================ Detail ================================ */

type Dialog = { kind: "withdrawCost"; id: string } | { kind: "cancel" } | { kind: "reject"; id: string } | { kind: "reviewed"; id: string } | { kind: "stock" } | { kind: "verify" } | { kind: "correct"; line: GrnLine } | null;

export function PurchasePage() {
  const { id = "" } = useParams();
  const { session } = useAuth();
  const location = useLocation();
  const [s, setS] = useState<ShipmentDetail | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(() => {
    const st = (location.state as { saved?: string } | null)?.saved;
    return st === "created" ? "Saved. Every line is in stock now." : st === "completed" ? "Details saved. The stock was not added again." : st === "quick" ? "Added to stock. Complete the details when the supplier's invoice arrives." : null;
  });
  const [busy, setBusy] = useState(false);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [reason, setReason] = useState("");
  const [completing, setCompleting] = useState(false);

  const load = useCallback(async () => {
    try {
      setS(await api.shipment(id));
      setLoadError(null);
    } catch (err) {
      setLoadError(err);
    }
  }, [id]);
  useEffect(() => {
    setS(null);
    setCompleting(false);
    void load();
  }, [load]);

  const run = useCallback(
    async (fn: () => Promise<ShipmentDetail>, done?: string) => {
      setError(null);
      setOk(null);
      setBusy(true);
      try {
        setS(await fn());
        if (done) setOk(done);
        return true;
      } catch (err) {
        setError(purchaseMessage(err));
        if (err instanceof ApiError && (err.code === "VERSION_CONFLICT" || err.code === "INVALID_STATE")) await load();
        return false;
      } finally {
        setBusy(false);
      }
    },
    [load],
  );

  if (!session) return null;
  if (loadError) return <ErrorState title="Couldn't load this purchase" error={loadError} onRetry={() => void load()} />;
  if (!s) return <PageSkeleton />;

  const canRecord = canInWarehouse(session, "grn.record", s.warehouse_id);
  const canApprove = canInWarehouse(session, "grn.approve", s.warehouse_id);
  const inStock = s.status === "POSTED";
  const old = s.stage === "NOT_IN_STOCK";
  const docName = (docId: string | null) => {
    const d = s.documents.find((x) => x.id === docId);
    return d ? `${d.kind === "CFIA" ? "CFIA document" : d.kind.toLowerCase().replace("_", " ")}${d.doc_ref ? ` ${d.doc_ref}` : ""}` : "No document";
  };

  async function confirmReason() {
    if (!s || !dialog) return;
    const r = reason.trim();
    let done = false;
    if (dialog.kind === "withdrawCost") done = await run(() => api.withdrawLandedCost(s.id, dialog.id, s.version, r), "Cost withdrawn. It stays on record.");
    else if (dialog.kind === "cancel") done = await run(() => api.cancelShipment(s.id, s.version, r), "Cancelled. No stock was changed.");
    else if (dialog.kind === "reject") done = await run(() => api.decideCorrection(s.id, dialog.id, "reject", s.version, r), "Refused. Stock was not changed.");
    else if (dialog.kind === "reviewed") done = await run(() => api.decideCorrection(s.id, dialog.id, "reviewed", s.version, r), "Marked as reviewed.");
    if (done) {
      setDialog(null);
      setReason("");
    }
  }

  if (completing && s.needs_details) {
    return (
      <div className="space-y-5">
        <PageHeader
          title={`Complete ${s.shipment_no}`}
          subtitle="Add the supplier, the invoice and the costs. The goods are already in stock and are not added again."
          actions={<button type="submit" form={FORM} className="btn-primary">Save details</button>}
        />
        <Notice tone="ok">
          Quick receive of {goodsText(s.items, s.line_count)} on {when(s.created_at)} by {s.created_by_name}. If the invoice shows a different quantity, change it below: only the difference is corrected.
        </Notice>
        <PurchaseForm
          warehouseId={s.warehouse_id}
          complete={s}
          onCompleted={(done) => {
            setS(done);
            setCompleting(false);
            setError(null);
            setOk("Details saved. The stock was not added again.");
          }}
        />
      </div>
    );
  }

  const now = stockNow(s);
  const invoiceLines = s.declared.filter((d) => !d.removed_at);

  return (
    <div className="space-y-4">
      <PageHeader
        title={s.entry === "QUICK" && s.needs_details ? `Quick receive ${s.shipment_no}` : `Purchase ${s.shipment_no}`}
        subtitle={
          <>
            {supplierName(s)}
            {s.vendor_invoice_no && <> · Invoice {s.vendor_invoice_no}</>}
            {s.invoice_date && <> · {day(s.invoice_date)}</>}
            {s.awb_no && <> · Waybill {s.awb_no}</>}
          </>
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <PurchaseChips s={s} />
            {old && canApprove && <button className="btn-ghost" onClick={() => setDialog({ kind: "cancel" })}>Cancel shipment</button>}
            {old && canRecord && <button className="btn-primary" disabled={busy} onClick={() => setDialog({ kind: "stock" })}>Add to stock now</button>}
            {s.needs_details && canRecord && <button className="btn-primary" onClick={() => setCompleting(true)}>Complete details</button>}
            {s.stage === "TO_VERIFY" && canRecord && <button className="btn-primary" onClick={() => setDialog({ kind: "verify" })}>Verify pieces</button>}
          </div>
        }
      />
      {error && <Notice tone="error">{error}</Notice>}
      {ok && <Notice tone="ok">{ok}</Notice>}

      {s.needs_details && (
        <p className="alert-warning" role="note">
          <strong>Details to complete.</strong> {goodsText(s.items, s.line_count)} {s.line_count === 1 ? "was" : "were"} added to stock on {when(s.created_at)} by {s.created_by_name}
          {s.quick_reason && <>: “{s.quick_reason}”</>}
          {s.note && <> ({s.note})</>}. The stock can be sold now. Still missing: the supplier's invoice and the cost.{" "}
          {canRecord && <>Choose <strong>Complete details</strong> to type them in, or <Link className={link} to="/purchases/new">scan the invoice</Link> and tick this quick receive there.</>}
        </p>
      )}
      {s.matched_shipment_id && (
        <Notice tone="ok">
          Matched to the supplier's invoice: <Link className={link} to={`/purchases/${s.matched_shipment_id}`}>purchase {s.matched_shipment_no}</Link>. The cost and the bill are there.
        </Notice>
      )}
      {s.stage === "TO_VERIFY" && (
        <p className="alert-warning" role="note">
          <strong>Airport shipment · to verify.</strong> In stock since {when(s.posted_at)}, so it can be invoiced as soon as it arrives{s.expected_on ? ` (expected ${day(s.expected_on)})` : ""}. When the goods are in, count the pieces and choose <strong>Verify pieces</strong>.
        </p>
      )}
      {s.stage === "TO_APPROVE" && (
        <p className="alert-warning" role="note">
          <strong>A difference needs a manager's OK.</strong> {s.verified_by_name} counted {s.verified_pieces} piece{s.verified_pieces === 1 ? "" : "s"} on {when(s.verified_at)}. Stock is unchanged until the difference below is approved or refused.
        </p>
      )}
      {s.stage === "VERIFIED" && <Notice tone="ok">Verified: {s.verified_pieces} piece{s.verified_pieces === 1 ? "" : "s"} counted by {s.verified_by_name} on {when(s.verified_at)}.</Notice>}
      {old && (
        <p className="alert-warning" role="note">
          <strong>Not in stock yet.</strong> This shipment was started before purchases went straight to stock. {canRecord ? <>Choose <strong>Add to stock now</strong> to add what {s.counted.length > 0 ? "was counted" : "its documents list"}; there is no approval step any more.</> : "Someone who enters purchases can add it to stock."}
        </p>
      )}
      {s.status === "CANCELLED" && <Notice tone="error">Cancelled: {s.cancel_reason}. No stock was changed.</Notice>}

      <Corrections s={s} canApprove={canApprove} busy={busy} onApprove={(c) => void run(() => api.decideCorrection(s.id, c.id, "approve", s.version), "Approved. Stock was corrected.")} onReject={(c) => setDialog({ kind: "reject", id: c.id })} onReviewed={(c) => setDialog({ kind: "reviewed", id: c.id })} />

      {invoiceLines.length > 0 && (
        <section className="card min-w-0 overflow-hidden" aria-labelledby="pl-h">
          <div className="flex flex-wrap items-baseline gap-x-3 px-4 py-3 sm:px-5">
            <h2 id="pl-h" className="font-semibold">Invoice lines</h2>
            <span className="text-sm text-ink-muted">What the supplier bills. Saved lines are never changed.</span>
          </div>
          <div className="overflow-x-auto border-t border-line" tabIndex={0} role="group" aria-label="Invoice lines table">
            <table className="w-full min-w-[34rem] text-sm">
              <thead>
                <tr>
                  <th className="th">Product</th>
                  <th className="th text-right">Qty</th>
                  <th className="th text-right">Cost per unit</th>
                  <th className="th text-right">Amount</th>
                </tr>
              </thead>
              <tbody>
                {invoiceLines.map((d) => (
                  <tr key={d.id} data-testid={`line-${d.sku}`}>
                    <td className="td"><span className="font-medium">{d.product_name}</span> <span className="text-xs text-ink-muted">{d.sku}</span></td>
                    <td className="td num text-right">{qtyWords(d.declared_qty, d.unit_code)}</td>
                    <td className="td num text-right">{d.unit_cost === null ? <span className="text-ink-muted">Cost to follow</span> : rate(d.unit_cost)}</td>
                    <td className="td num text-right">{d.unit_cost === null ? "—" : moneyText(lineAmount(d.declared_qty, d.unit_cost) ?? "0", s.currency)}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr>
                  <td className="px-5 py-3 text-sm font-medium" colSpan={3}>Supplier bill{s.unpriced_lines > 0 ? ` (${s.unpriced_lines} line${s.unpriced_lines === 1 ? "" : "s"} without a cost not included)` : ""}</td>
                  <td className="num px-5 py-3 text-right font-semibold">{moneyText(s.total, s.currency)}</td>
                </tr>
              </tfoot>
            </table>
          </div>
        </section>
      )}

      {s.counted.length > 0 && (
        <section className="card min-w-0 overflow-hidden" aria-labelledby="st-h">
          <div className="flex flex-wrap items-baseline gap-x-3 px-4 py-3 sm:px-5">
            <h2 id="st-h" className="font-semibold">{inStock ? "Added to stock" : "Counted, not in stock yet"}</h2>
            {inStock && <span className="text-sm text-ink-muted">{s.grn_no} · {when(s.posted_at)} by {s.posted_by_name}</span>}
          </div>
          <div className="overflow-x-auto border-t border-line" tabIndex={0} role="group" aria-label="Stock lines table">
            <table className="w-full min-w-[34rem] text-sm">
              <thead>
                <tr>
                  <th className="th">Product</th>
                  <th className="th text-right">Added</th>
                  <th className="th text-right">After corrections</th>
                  <th className="th">Batch</th>
                  <th className="th"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {s.counted.map((c) => (
                  <tr key={c.id} data-testid={`stock-${c.sku}`}>
                    <td className="td"><span className="font-medium">{c.product_name}</span> <span className="text-xs text-ink-muted">{c.sku}</span>{c.note && <span className="block text-xs text-ink-muted">{c.note}</span>}</td>
                    <td className="td num text-right">{qtyWords(c.accepted_qty, c.unit_code)}{dec(c.damaged_qty) > 0n && <span className="block text-xs text-ink-muted">+ {qtyWords(c.damaged_qty, c.unit_code)} damaged</span>}</td>
                    <td className="td num text-right">{now.get(c.id) === undefined || now.get(c.id) === c.accepted_qty ? <span className="text-ink-muted">—</span> : qtyWords(now.get(c.id) as string, c.unit_code)}</td>
                    <td className="td">{c.lot_id ? <Link className={link} to={`/inventory/lots/${c.lot_id}`}>{c.batch_ref || c.lot_code}</Link> : (c.batch_ref ?? "—")}</td>
                    <td className="td text-right">
                      {inStock && canRecord && c.lot_id && s.entry !== "LEGACY" && (
                        <button type="button" className="btn-ghost px-3.5 py-1.5 text-xs" onClick={() => setDialog({ kind: "correct", line: c })} aria-label={`Correct ${c.product_name}`}>
                          Correct…
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {inStock && s.entry !== "LEGACY" && <p className="border-t border-line px-4 py-2.5 text-xs text-ink-muted sm:px-5">Short, damaged or returned later? Use <strong>Correct…</strong>: it adds a correction with your reason. What was saved is never edited.</p>}
        </section>
      )}

      {s.absorbed.length > 0 && (
        <section className="card space-y-2 p-4 sm:px-5" aria-labelledby="ab-h">
          <h2 id="ab-h" className="font-semibold">Already on the shelf</h2>
          <p className="text-sm text-ink-muted">These goods on the invoice were added earlier by quick receive, so they were not added again.</p>
          <ul className="divide-y divide-line text-sm">
            {s.absorbed.map((q) => (
              <li key={q.id} className="flex flex-wrap items-baseline gap-x-3 py-2">
                <Link className={link} to={`/purchases/${q.id}`}>{q.shipment_no}</Link>
                <span>{goodsText(q.items, q.items.length)}</span>
                <span className="text-xs text-ink-muted">added {when(q.created_at)} by {q.created_by_name}{q.quick_reason ? ` · “${q.quick_reason}”` : ""}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {inStock && canRecord && s.entry !== "LEGACY" && !s.needs_details && !s.matched_shipment_id && <AddLine s={s} busy={busy} run={run} />}

      {s.vendor_id ? (
        <DocumentsStrip s={s} editable={canRecord && s.status !== "CANCELLED"} busy={busy} run={run} />
      ) : (
        <section className="card px-4 py-3 text-sm text-ink-muted sm:px-5"><span className="font-semibold text-ink">Supplier documents</span> · Complete the details first: documents are filed under the supplier.</section>
      )}
      {(s.is_airport || s.costs.length > 0 || s.entry === "LEGACY") && s.vendor_id && (
        <CostSection s={s} editable={s.status !== "CANCELLED" && (canRecord || canApprove)} busy={busy} run={run} docName={docName} onWithdraw={(cid) => setDialog({ kind: "withdrawCost", id: cid })} />
      )}

      <ReasonDialog
        open={dialog?.kind === "withdrawCost" || dialog?.kind === "cancel" || dialog?.kind === "reject" || dialog?.kind === "reviewed"}
        title={dialog?.kind === "withdrawCost" ? "Withdraw this cost?" : dialog?.kind === "cancel" ? "Cancel this shipment?" : dialog?.kind === "reject" ? "Refuse this correction?" : "Mark as reviewed?"}
        body={
          dialog?.kind === "cancel"
            ? "Nothing is added to stock. The shipment and its documents stay on record."
            : dialog?.kind === "reject"
              ? "Nothing is changed in stock. An airport shipment goes back to be counted again."
              : dialog?.kind === "reviewed"
                ? "Say what was done about it (for example: customer credited, shelf counted)."
                : "It is kept on record as withdrawn, with your reason."
        }
        confirmLabel={dialog?.kind === "cancel" ? "Cancel shipment" : dialog?.kind === "reject" ? "Refuse" : dialog?.kind === "reviewed" ? "Mark reviewed" : "Withdraw"}
        reason={reason}
        setReason={setReason}
        onClose={() => {
          setDialog(null);
          setReason("");
        }}
        onConfirm={() => void confirmReason()}
      />
      <ConfirmDialog open={dialog?.kind === "stock"} title="Add this shipment to stock?" confirmLabel={busy ? "Adding…" : "Add to stock"} onClose={() => setDialog(null)} onConfirm={() => void run(() => api.stockOldShipment(s.id, s.version), "Added to stock.").then((done) => done && setDialog(null))}>
        <p>{s.counted.length > 0 ? "What was counted is added: good quantities become stock, damaged goods are put aside." : "Every line on its documents is added to stock as arrived."} This cannot be undone; later differences are corrections.</p>
      </ConfirmDialog>
      {dialog?.kind === "verify" && <VerifyDialog s={s} canApprove={canApprove} onClose={() => setDialog(null)} onSubmit={async (b) => (await run(() => api.verifyPurchase(s.id, { version: s.version, ...b }), b.lines.length ? (canApprove ? "Verified. The difference was corrected in stock." : "Count saved. A manager must approve the difference.") : "Verified. Every piece arrived.")) && setDialog(null)} error={error} busy={busy} />}
      {dialog?.kind === "correct" && <CorrectDialog s={s} line={dialog.line} now={now.get(dialog.line.id) ?? dialog.line.accepted_qty} canApprove={canApprove} busy={busy} error={error} onClose={() => setDialog(null)} onSubmit={async (b) => (await run(() => api.addCorrection(s.id, { version: s.version, line_id: dialog.line.id, ...b }), canApprove ? "Correction posted to stock." : "Correction saved. A manager must approve it before stock changes.")) && setDialog(null)} />}
    </div>
  );
}

function Corrections({ s, canApprove, busy, onApprove, onReject, onReviewed }: { s: ShipmentDetail; canApprove: boolean; busy: boolean; onApprove: (c: PurchaseCorrection) => void; onReject: (c: PurchaseCorrection) => void; onReviewed: (c: PurchaseCorrection) => void }) {
  if (s.corrections.length === 0) return null;
  return (
    <section className="card min-w-0 overflow-hidden" aria-labelledby="co-h">
      <div className="flex flex-wrap items-baseline gap-x-3 px-4 py-3 sm:px-5">
        <h2 id="co-h" className="font-semibold">Corrections</h2>
        <span className="text-sm text-ink-muted">Each one is a new stock movement with its reason.</span>
      </div>
      <ul className="divide-y divide-line border-t border-line" aria-label="Corrections">
        {s.corrections.map((c) => (
          <li key={c.id} className="space-y-2 px-4 py-3 text-sm sm:px-5" data-testid={`correction-${c.kind}`}>
            <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
              <StatusChip tone={c.status === "PENDING" ? "warning" : c.status === "REJECTED" ? "neutral" : c.needs_review ? "danger" : "success"}>
                {c.status === "PENDING" ? "Needs manager's OK" : c.status === "REJECTED" ? "Refused" : c.needs_review ? "To review" : CORRECTION[c.kind].label}
              </StatusChip>
              <p className="min-w-0 flex-1">
                {correctionText(c)}
                <span className="block text-xs text-ink-muted">
                  Reason: “{c.reason}” · {c.created_by_name}, {when(c.created_at)}
                  {c.decided_by_name && <> · {c.status === "REJECTED" ? "refused" : "approved"} by {c.decided_by_name}{c.decision_note ? `: “${c.decision_note}”` : ""}</>}
                </span>
              </p>
              {c.status === "PENDING" && canApprove && (
                <span className="flex gap-2">
                  <button type="button" className="btn-ghost px-3.5 py-1.5 text-xs" disabled={busy} onClick={() => onReject(c)}>Refuse</button>
                  <button type="button" className="btn-primary px-3.5 py-1.5 text-xs" disabled={busy} onClick={() => onApprove(c)}>Approve</button>
                </span>
              )}
            </div>
            {c.needs_review && (
              <div className="alert-danger flex flex-wrap items-center gap-x-3 gap-y-2" role="alert">
                <span className="min-w-0 flex-1">
                  <strong>Already invoiced.</strong> {qtyWords(c.uncovered_qty, c.unit_code)} of this batch had left on {c.invoices.length ? `invoice ${c.invoices.join(", ")}` : "an invoice or an order being packed"}, so stock was not pushed below zero. Check that invoice and the shelf.
                </span>
                {canApprove && <button type="button" className="btn-ghost px-3.5 py-1.5 text-xs" onClick={() => onReviewed(c)}>Mark reviewed</button>}
              </div>
            )}
            {c.review_note && <p className="text-xs text-ink-muted">Reviewed by {c.reviewed_by_name}, {when(c.reviewed_at)}: “{c.review_note}”</p>}
          </li>
        ))}
      </ul>
    </section>
  );
}

type VerifyBody = { pieces_counted: number; lines: { line_id: string; arrived_qty: string; damaged_qty: string }[]; reason: string | null };

function VerifyDialog({ s, canApprove, onClose, onSubmit, error, busy }: { s: ShipmentDetail; canApprove: boolean; onClose: () => void; onSubmit: (b: VerifyBody) => Promise<unknown>; error: string | null; busy: boolean }) {
  const expected = s.expected_pieces ?? s.line_pieces;
  const [pieces, setPieces] = useState("");
  const [differs, setDiffers] = useState(false);
  const [rows, setRows] = useState<Record<string, { arrived: string; damaged: string }>>({});
  const [reason, setReason] = useState("");
  const [local, setLocal] = useState<string | null>(null);
  const formId = useId();
  const now = stockNow(s);
  const mismatch = expected !== null && pieces !== "" && Number(pieces) !== expected;
  const show = differs || mismatch;
  const changed = s.counted.flatMap((c) => {
    const r = rows[c.id];
    const want = qtyText(now.get(c.id) ?? c.accepted_qty);
    const arrived = (r?.arrived ?? want).trim();
    const damaged = (r?.damaged ?? "").trim() || "0";
    return arrived !== want || damaged !== "0" ? [{ line_id: c.id, arrived_qty: arrived || "0", damaged_qty: damaged }] : [];
  });

  async function submit(e: FormEvent) {
    e.preventDefault();
    setLocal(null);
    if (pieces === "") return setLocal("Enter how many pieces you counted.");
    if (show && changed.some((c) => !isDecimal(c.arrived_qty) || !isDecimal(c.damaged_qty))) return setLocal("Enter numbers for what arrived.");
    if (mismatch && changed.length === 0) return setLocal(`You counted ${pieces}, ${expected} were expected. Enter what arrived for the lines that differ.`);
    if (changed.length > 0 && show && reason.trim().length < 3) return setLocal("Say why the count differs.");
    await onSubmit({ pieces_counted: Number(pieces), lines: show ? changed : [], reason: show && changed.length ? reason.trim() : null });
  }

  return (
    <Modal
      open
      title="Verify pieces"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
          <button type="submit" form={formId} className="btn-primary" disabled={busy}>{busy ? "Saving…" : show && changed.length ? "Record the difference" : "Mark verified"}</button>
        </>
      }
    >
      <form id={formId} className="space-y-4 p-5" onSubmit={submit} aria-label="Verify pieces">
        <p className="text-sm text-ink-muted">Count the pieces that arrived. If they all came, that is all. If something is short or damaged, say which line: the difference is corrected in stock with your reason{canApprove ? "" : ", after a manager's OK"}.</p>
        <div className="grid grid-cols-[minmax(0,12rem)_1fr] items-end gap-4">
          <div>
            <label className="label" htmlFor={`${formId}-p`}>Pieces counted</label>
            <input id={`${formId}-p`} className="input num" inputMode="numeric" autoFocus value={pieces} onChange={(e) => setPieces(e.target.value.replace(/\D/g, ""))} />
          </div>
          <p className="pb-2.5 text-sm text-ink-muted">{expected === null ? "No piece count on file for this shipment." : `${expected} expected${s.expected_pieces !== null ? " (airway bill)" : " (from the lines)"}.`}</p>
        </div>
        {!mismatch && (
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={differs} onChange={(e) => setDiffers(e.target.checked)} />
            Something is short, damaged or extra
          </label>
        )}
        {show && (
          <div className="space-y-3 rounded-ctl border border-line p-3">
            <p className="text-sm font-medium">What arrived, line by line</p>
            {s.counted.map((c) => {
              const want = qtyText(now.get(c.id) ?? c.accepted_qty);
              const r = rows[c.id] ?? { arrived: want, damaged: "" };
              const places = c.unit_code === "BOX" || c.unit_code === "PIECE" ? 0 : 2;
              return (
                <div key={c.id} className="grid grid-cols-2 items-end gap-3 sm:grid-cols-[minmax(0,1fr)_8rem_8rem]" role="group" aria-label={c.product_name}>
                  <p className="col-span-2 text-sm sm:col-span-1">
                    <span className="font-medium">{c.product_name}</span>
                    <span className="block text-xs text-ink-muted">Entered: {qtyWords(now.get(c.id) ?? c.accepted_qty, c.unit_code)}</span>
                  </p>
                  <NumericInput label={`Arrived ${c.sku}`} value={r.arrived} onChange={(v) => setRows((x) => ({ ...x, [c.id]: { ...r, arrived: v } }))} places={places} unit={UNIT_LABEL[c.unit_code]} />
                  <NumericInput label={`Damaged ${c.sku}`} value={r.damaged} onChange={(v) => setRows((x) => ({ ...x, [c.id]: { ...r, damaged: v } }))} places={places} unit={UNIT_LABEL[c.unit_code]} />
                </div>
              );
            })}
            <div>
              <label className="label" htmlFor={`${formId}-r`}>What happened? (required)</label>
              <input id={`${formId}-r`} className="input" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Two boxes missing at the airport" />
            </div>
          </div>
        )}
        {(local || error) && <Notice tone="error">{local ?? error}</Notice>}
      </form>
    </Modal>
  );
}

const KINDS: { value: CorrectionKind; label: string; hint: string }[] = [
  { value: "SHORT", label: "Short", hint: "Fewer arrived than were entered." },
  { value: "DAMAGED", label: "Damaged", hint: "They arrived, but can't be sold. They are put aside." },
  { value: "RETURNED", label: "Returned to the supplier", hint: "Sent back." },
  { value: "OVER", label: "More than invoiced", hint: "Extra goods arrived." },
];

function CorrectDialog({ s, line, now, canApprove, busy, error, onClose, onSubmit }: { s: ShipmentDetail; line: GrnLine; now: string; canApprove: boolean; busy: boolean; error: string | null; onClose: () => void; onSubmit: (b: { kind: CorrectionKind; qty: string; reason: string }) => Promise<unknown> }) {
  const [kind, setKind] = useState<CorrectionKind>("SHORT");
  const [qty, setQty] = useState("");
  const [reason, setReason] = useState("");
  const [local, setLocal] = useState<string | null>(null);
  const formId = useId();
  const places = line.unit_code === "BOX" || line.unit_code === "PIECE" ? 0 : 2;
  async function submit(e: FormEvent) {
    e.preventDefault();
    setLocal(null);
    if (!qty.trim() || !isDecimal(qty) || dec(qty) <= 0n) return setLocal("Enter the quantity.");
    if (reason.trim().length < 3) return setLocal("Give a reason (a few words).");
    await onSubmit({ kind, qty: qty.trim(), reason: reason.trim() });
  }
  return (
    <Modal
      open
      title={`Correct ${line.product_name}`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn-ghost" onClick={onClose}>Cancel</button>
          <button type="submit" form={formId} className="btn-primary" disabled={busy}>{busy ? "Saving…" : canApprove ? "Post correction" : "Send for approval"}</button>
        </>
      }
    >
      <form id={formId} className="space-y-4 p-5" onSubmit={submit} aria-label="Correction">
        <p className="text-sm text-ink-muted">
          {qtyWords(now, line.unit_code)} on purchase {s.shipment_no}. The saved line stays as it is: this adds a correction to the stock ledger{canApprove ? "" : " once a manager approves it"}.
        </p>
        <fieldset className="space-y-2">
          <legend className="label">What happened?</legend>
          {KINDS.map((k) => (
            <label key={k.value} className="flex items-start gap-2 text-sm">
              <input type="radio" name={`${formId}-k`} className="mt-1" checked={kind === k.value} onChange={() => setKind(k.value)} />
              <span><span className="font-medium">{k.label}</span> <span className="text-ink-muted">{k.hint}</span></span>
            </label>
          ))}
        </fieldset>
        <NumericInput label="How many" value={qty} onChange={setQty} places={places} unit={UNIT_LABEL[line.unit_code]} className="max-w-48" />
        <div>
          <label className="label" htmlFor={`${formId}-r`}>Reason (required)</label>
          <input id={`${formId}-r`} className="input" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
        {(local || error) && <Notice tone="error">{local ?? error}</Notice>}
      </form>
    </Modal>
  );
}

/** A line missed on a saved purchase (or typed after a scan): added to stock at once. */
function AddLine({ s, busy, run }: { s: ShipmentDetail; busy: boolean; run: (fn: () => Promise<ShipmentDetail>, done?: string) => Promise<boolean> }) {
  const { products, details } = useGridProducts();
  const [expiry, setExpiry] = useState("");
  const [product, setProduct] = useState("");
  const [unit, setUnit] = useState<UnitCode | "">("");
  const [qty, setQty] = useState("");
  const [cost, setCost] = useState("");
  const [seq, setSeq] = useState(0);
  const prod = products.find((p) => p.id === product);
  const id = useId();
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!product || !unit || !qty.trim()) return;
    const done = await run(() => api.addPurchaseLines(s.id, s.version, [{ product_id: product, unit_code: unit, qty: qty.trim(), unit_cost: cost.trim() || null, expires_at: expiryIso(expiry) }]), `${prod?.name ?? "Line"} added to stock.`);
    if (done) {
      setProduct("");
      setUnit("");
      setQty("");
      setCost("");
      setSeq((n) => n + 1);
    }
  }
  return (
    <details className="card group/add min-w-0 overflow-hidden">
      <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3 sm:px-5 [&::-webkit-details-marker]:hidden">
        <Icon name="chevron" size={16} className="transition-transform group-open/add:rotate-90" />
        <h2 className="font-semibold">Add a line missed on this invoice</h2>
        <span className="text-sm text-ink-muted">It goes to stock at once.</span>
      </summary>
      <form className="grid gap-3 border-t border-line bg-canvas p-4 sm:px-5 md:grid-cols-[minmax(0,2fr)_7rem_7rem_8rem_auto] md:items-end" aria-label="Add a line" onSubmit={submit}>
        <div>
          <label className="label" htmlFor={`${id}-p`}>Product</label>
          <ProductField key={seq} id={`${id}-p`} products={products} value={product} onPick={(p) => { setProduct(p?.id ?? ""); setUnit(p ? (p.units[0] ?? "") : ""); }} />
        </div>
        <NumericInput label="Quantity" value={qty} onChange={setQty} places={unit === "BOX" || unit === "PIECE" ? 0 : 2} />
        <div>
          <label className="label" htmlFor={`${id}-u`}>Unit</label>
          <select id={`${id}-u`} className="input" value={unit} onChange={(e) => setUnit(e.target.value as UnitCode)}>
            {!unit && <option value="">—</option>}
            {(prod?.units ?? []).map((u) => <option key={u} value={u}>{UNIT_LABEL[u]}</option>)}
          </select>
        </div>
        <NumericInput label="Cost per unit" value={cost} onChange={setCost} places={4} />
        {details.get(product)?.tracks_expiry && (
          <div className="md:col-span-2">
            <label className="label" htmlFor={`${id}-x`}>Expiry date</label>
            <input id={`${id}-x`} type="date" className="input" value={expiry} onChange={(e) => setExpiry(e.target.value)} />
          </div>
        )}
        <button className="btn-primary" disabled={busy || !product || !unit || !qty.trim()}>Add to stock</button>
      </form>
    </details>
  );
}

/** "Added" feedback after a quick receive from a short line (packing sheet, invoice screens). */
export function AddedNotice({ added }: { added: ShipmentDetail | null }) {
  if (!added) return null;
  return (
    <p className="alert-success print:hidden" role="status">
      Added to stock: {goodsText(added.items, added.line_count)}. It stays on the <Link className={link} to={`/purchases/${added.id}`}>“To complete” list</Link> until the supplier's invoice is entered.
    </p>
  );
}
