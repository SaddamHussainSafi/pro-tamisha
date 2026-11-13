import { useEffect, useState, type FormEvent } from "react";
import { errorText } from "../lib/errors";
import { Link } from "react-router-dom";
import { api, ApiError, has, type OcrExtractionDetail as OcrExtraction, type OcrField, type Shipment, type Vendor } from "../api";
import { useAuth } from "../auth";
import { todayIso } from "../lib/format";
import { warningText } from "../lib/ocr";
import { useGridProducts } from "../lib/gridData";
import { expiryIso, goodsText, matchingQuick, purchaseMessage } from "../lib/purchases";
import { Icon } from "../ui/Icon";

type Box = { page: number; x0: string; y0: string; x1: string; y1: string } | null;
/** An area the reviewer must check (unclear quantity or unknown product), always outlined. */
export type Flag = { page: number; x0: string; y0: string; x1: string; y1: string; seq: number; tone: "warning" | "danger"; label: string };

/**
 * Original document viewer: every page as an image, page by page, with zoom and a view-only
 * rotation. The original file is never changed. `highlight` outlines the area a line was read
 * from (positions come from the OCR engine).
 */
export function DocViewer({
  documentId,
  title,
  highlight,
  flags = [],
  initialPage = 1,
  onFlag,
}: {
  documentId: string;
  title: string;
  highlight: Box;
  flags?: Flag[];
  initialPage?: number;
  onFlag?: (seq: number) => void;
}) {
  const [page, setPage] = useState(initialPage);
  const [pages, setPages] = useState<number | null>(null);
  const [zoom, setZoom] = useState(1);
  const [turn, setTurn] = useState(0);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    fetch(api.documentPageUrl(documentId, 1), { credentials: "same-origin" }).then(
      (r) => live && (r.ok ? setPages(Number(r.headers.get("X-Page-Count") ?? "1")) : setFailed(true)),
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, [documentId]);
  useEffect(() => {
    if (highlight && highlight.page !== page) setPage(highlight.page);
  }, [highlight]); // eslint-disable-line react-hooks/exhaustive-deps
  const box = highlight && highlight.page === page ? highlight : null;
  const here = flags.filter((f) => f.page === page);
  const btn = "grid size-9 place-items-center rounded-ctl border border-line bg-card hover:bg-subtle disabled:opacity-40";
  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line p-2" role="toolbar" aria-label="Document view">
        <button type="button" className={btn} aria-label="Previous page" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
          <Icon name="chevronLeft" size={16} />
        </button>
        <span className="num text-sm" aria-live="polite">
          Page {page} of {pages ?? "…"}
        </span>
        <button type="button" className={btn} aria-label="Next page" disabled={!pages || page >= pages} onClick={() => setPage((p) => p + 1)}>
          <Icon name="chevronLeft" size={16} className="rotate-180" />
        </button>
        <span className="mx-1 h-5 w-px bg-line" aria-hidden />
        <button type="button" className={btn} aria-label="Zoom out" disabled={zoom <= 0.5} onClick={() => setZoom((z) => Math.max(0.5, z - 0.25))}>
          −
        </button>
        <span className="num w-12 text-center text-sm">{Math.round(zoom * 100)}%</span>
        <button type="button" className={btn} aria-label="Zoom in" disabled={zoom >= 3} onClick={() => setZoom((z) => Math.min(3, z + 0.25))}>
          +
        </button>
        <button type="button" className="rounded-ctl border border-line px-3 py-1.5 text-sm hover:bg-subtle" onClick={() => setZoom(1)}>
          Fit
        </button>
        <button type="button" className={btn} aria-label="Rotate view" onClick={() => setTurn((t) => (t + 90) % 360)}>
          <Icon name="refresh" size={16} />
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto bg-subtle p-3" tabIndex={0} role="region" aria-label={`${title}, page ${page}`}>
        {failed ? (
          <p className="p-6 text-sm text-ink-muted">This file can't be shown here. Download it from the scan page.</p>
        ) : (
          <div className="relative mx-auto origin-top transition-transform duration-300" style={{ width: `${zoom * 100}%`, transform: `rotate(${turn}deg)` }}>
            <img alt={`${title}, page ${page}`} src={api.documentPageUrl(documentId, page)} className="block w-full rounded-ctl border border-line bg-card" />
            {here.map((f) => (
              <button
                key={f.seq}
                type="button"
                className={`absolute rounded-md border-2 border-dashed ${f.tone === "danger" ? "border-danger bg-danger/10" : "border-warning bg-warning/12"} hover:bg-transparent focus-visible:outline-2`}
                style={{
                  left: `${Number(f.x0) * 100}%`,
                  top: `${Number(f.y0) * 100}%`,
                  width: `${Math.max(Number(f.x1) - Number(f.x0), 0.02) * 100}%`,
                  height: `${Math.max(Number(f.y1) - Number(f.y0), 0.012) * 100}%`,
                }}
                aria-label={`Line ${f.seq}: ${f.label}. Show the line`}
                title={`Line ${f.seq}: ${f.label}`}
                onClick={() => onFlag?.(f.seq)}
                data-testid="doc-flag"
              />
            ))}
            {box && (
              <span
                aria-hidden
                className="pointer-events-none absolute rounded-md border-2 border-brand-accent bg-brand-accent/15 transition-all duration-300"
                style={{
                  left: `${Number(box.x0) * 100}%`,
                  top: `${Number(box.y0) * 100}%`,
                  width: `${(Number(box.x1) - Number(box.x0)) * 100}%`,
                  height: `${(Number(box.y1) - Number(box.y0)) * 100}%`,
                }}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

const LABEL: Record<string, string> = {
  supplier: "Supplier as written",
  document_no: "Document number",
  document_date: "Date",
  due_date: "Due date",
  currency: "Currency",
  subtotal: "Subtotal",
  tax: "Tax",
  total: "Total",
  awb_no: "Airway bill",
  carrier: "Carrier",
  arrival_date: "Delivered",
};
/** Shown only for airport packets or when the document has them. */
const AIR_FIELDS = new Set(["awb_no", "carrier", "arrival_date"]);
const WARN: Record<string, string> = {
  SUPPLIER_NOT_FOUND: "The supplier on the document isn't one of yours yet. Choose it above, or add it as a new supplier.",
  LINES_NOT_EQUAL_SUBTOTAL: "The lines don't add up to the subtotal.",
  TOTALS_DONT_ADD_UP: "Subtotal plus tax doesn't equal the total.",
  NO_LINES: "No product lines were found. Add them by hand.",
  NO_TEXT: "No text could be read. Check the scan quality.",
  PACKET_TOTAL_MISMATCH: "The total printed on the invoice or CFIA form doesn't match the lines. Check the amounts.",
  PACKET_INVOICE_MISMATCH: "The vendor invoice and the CFIA Confirmation of Sale don't agree. Check the lines.",
};
function warnText(w: string): string {
  if (w.startsWith("LINE_MATH:")) return `Line ${w.split(":")[1]}: quantity × price doesn't match the amount.`;
  if (w.startsWith("POSSIBLE_DUPLICATE:")) return `Possible duplicate: the same supplier and number are already on ${w.slice(19)}.`;
  return WARN[w] ?? warningText(w);
}

/** Header values as read, with engine confidence; low-confidence values are marked. */
export function SupplierHeader({ ex, threshold, editable, onFixed, vendor, onVendor }: { ex: OcrExtraction; threshold: number; editable: boolean; onFixed: (e: OcrExtraction) => void; vendor: string; onVendor: (id: string) => void }) {
  const [editing, setEditing] = useState<string | null>(null);
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const h = ex.header ?? {};
  async function save(field: string) {
    setError(null);
    try {
      onFixed(await api.fixOcrHeader(ex.id, { version: ex.version, field, value: value.trim() || null, reason: "" }));
      setEditing(null);
    } catch {
      setError("Couldn't save. Reload and try again.");
    }
  }
  return (
    <section aria-labelledby="hdr-h" className="card space-y-3 p-6">
      <h2 id="hdr-h" className="text-lg font-semibold">Document details</h2>
      {!ex.shipment_id && <SupplierPicker ex={ex} vendor={vendor} onVendor={onVendor} editable={editable} />}
      {h.kind?.value === "AIR_PACKET" && (
        <p className="text-sm text-ink-muted">Read as an airport delivery packet (customs declaration, vendor invoice and CFIA Confirmation of Sale). Product lines come from the Confirmation of Sale, checked against the invoice.</p>
      )}
      {(ex.warnings ?? []).some((w) => !(w === "SUPPLIER_NOT_FOUND" && vendor)) && (
        <ul className="space-y-1">
          {ex.warnings.filter((w) => !(w === "SUPPLIER_NOT_FOUND" && vendor)).map((w) => (
            <li key={w} className="flex items-start gap-2 rounded-ctl bg-warning/10 px-3 py-2 text-sm">
              <Icon name="alert" size={16} className="mt-0.5 shrink-0 text-warning" />
              {warnText(w)}
            </li>
          ))}
        </ul>
      )}
      {error && (
        <p role="alert" className="alert-danger">
          {error}
        </p>
      )}
      <dl className="divide-y divide-line rounded-ctl border border-line">
        {Object.keys(LABEL).filter((k) => !AIR_FIELDS.has(k) || h.kind?.value === "AIR_PACKET" || h[k]?.value != null || h[k]?.corrected != null).map((k) => {
          const c = h[k];
          const shown = c?.corrected !== undefined ? c.corrected : c?.value;
          const low = c?.confidence != null && Number(c.confidence) < threshold;
          return (
            <div key={k} className="flex flex-wrap items-center gap-3 px-4 py-2.5 text-sm">
              <dt className="w-36 text-ink-muted">{LABEL[k]}</dt>
              <dd className="flex min-w-0 flex-1 flex-wrap items-center gap-3">
                <span className="min-w-0 flex-1">
                {editing === k ? (
                  <span className="flex gap-2">
                    <label className="sr-only" htmlFor={`hdr-${k}`}>{LABEL[k]}</label>
                    <input id={`hdr-${k}`} className="input py-1.5" value={value} onChange={(e) => setValue(e.target.value)} />
                    <button type="button" className="btn-primary px-3 py-1.5 text-xs" onClick={() => void save(k)}>
                      Save
                    </button>
                  </span>
                ) : (
                  <span className={shown ? "font-medium" : "text-ink-muted"}>
                    {shown ?? (c?.supplier_default ? `Not on the page (supplier uses ${c.supplier_default})` : "Not found")}
                  </span>
                )}
                {c?.corrected !== undefined && <span className="ml-2 text-xs text-ink-muted">(read: {c.value ?? "nothing"})</span>}
                </span>
              {low && c?.corrected === undefined && <span className="rounded-full bg-warning/15 px-2 py-0.5 text-xs font-medium">Check · {Math.round(Number(c.confidence) * 100)}%</span>}
              {c?.ambiguous && <span className="rounded-full bg-warning/15 px-2 py-0.5 text-xs font-medium">Day/month unclear</span>}
              {editable && editing !== k && (
                <button
                  type="button"
                  className="text-xs font-medium text-brand-primary hover:underline"
                  aria-label={`Correct ${LABEL[k]}`}
                  onClick={() => {
                    setEditing(k);
                    setValue(shown ?? "");
                  }}
                >
                  Correct
                </button>
              )}
              </dd>
            </div>
          );
        })}
      </dl>
    </section>
  );
}

const NEW = "__new";
const short = (t: string) => (t.length > 40 ? `${t.slice(0, 40).trimEnd()}…` : t);
const codeFrom = (name: string) => name.toUpperCase().replace(/[^A-Z0-9]+/g, "").slice(0, 8);

/** The supplier, at the top of the review: the one that was matched, any other one, or a new
 * supplier made from the name on the document. Sending to Receiving uses this choice. */
function SupplierPicker({ ex, vendor, onVendor, editable }: { ex: OcrExtraction; vendor: string; onVendor: (id: string) => void; editable: boolean }) {
  const { session } = useAuth();
  const canAdd = !!session && has(session, "catalog.manage");
  const [vendors, setVendors] = useState<Vendor[] | null>(null);
  const [adding, setAdding] = useState(false);
  const h = ex.header ?? {};
  const read = (h.supplier?.corrected ?? h.supplier?.value ?? "").trim();
  const [name, setName] = useState(read);
  const [code, setCode] = useState(codeFrom(read));
  const [currency, setCurrency] = useState(((h.currency?.corrected ?? h.currency?.value) || "CAD").toUpperCase());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.vendors({ active: true }).then(setVendors, () => setVendors([]));
  }, []);
  async function add(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const v = await api.createVendor({ code: code.trim().toUpperCase(), name: name.trim(), currency: currency.trim().toUpperCase() || "CAD" });
      setVendors((list) => [...(list ?? []), v].sort((a, b) => a.name.localeCompare(b.name)));
      onVendor(v.id);
      setAdding(false);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  const matched = !!ex.vendor_id && vendor === ex.vendor_id;
  return (
    <div className="space-y-2 rounded-ctl border border-line bg-subtle/60 p-4">
      <label className="label" htmlFor="ocr-vendor">
        Supplier
      </label>
      <select
        id="ocr-vendor"
        className="input max-w-md"
        value={adding ? NEW : vendor}
        disabled={!editable || vendors === null}
        onChange={(e) => {
          if (e.target.value === NEW) setAdding(true);
          else {
            setAdding(false);
            onVendor(e.target.value);
          }
        }}
      >
        <option value="">{vendors === null ? "Loading suppliers…" : "Choose the supplier…"}</option>
        {(vendors ?? []).map((v) => (
          <option key={v.id} value={v.id}>
            {v.name}
          </option>
        ))}
        {canAdd && <option value={NEW}>{read ? `+ Add “${short(read)}” as a new supplier` : "+ Add a new supplier"}</option>}
      </select>
      <p className="text-xs text-ink-muted">
        {read ? `The document says “${read}”. ` : "No supplier name was found on the document. "}
        {matched ? "Matched to your supplier list." : vendor ? "Chosen by you." : "Not one of your suppliers yet."}
      </p>
      {adding && (
        <form className="grid gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_6rem_auto] sm:items-end" onSubmit={(e) => void add(e)} aria-label="New supplier">
          <div>
            <label className="label" htmlFor="nv-name">Name</label>
            <input id="nv-name" className="input" required maxLength={200} value={name} onChange={(e) => { setName(e.target.value); setCode(codeFrom(e.target.value)); }} />
          </div>
          <div>
            <label className="label" htmlFor="nv-code">Code</label>
            <input id="nv-code" className="input uppercase" required maxLength={32} value={code} onChange={(e) => setCode(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="nv-cur">Currency</label>
            <input id="nv-cur" className="input uppercase" required maxLength={3} value={currency} onChange={(e) => setCurrency(e.target.value)} />
          </div>
          <div className="flex gap-2">
            <button type="submit" className="btn-primary" disabled={busy || !name.trim() || !code.trim()}>
              {busy ? "Adding…" : "Add supplier"}
            </button>
            <button type="button" className="btn-ghost" onClick={() => setAdding(false)}>
              Cancel
            </button>
          </div>
          {error && (
            <p role="alert" className="alert-danger sm:col-span-4">
              {error}
            </p>
          )}
        </form>
      )}
    </div>
  );
}

/** Line prices as read (supplier documents). */
export function PriceList({ fields }: { fields: OcrField[] }) {
  const rows = fields.filter((f) => f.read_unit_price || f.read_amount);
  if (!rows.length) return null;
  return (
    <table className="w-full text-sm">
      <caption className="sr-only">Prices as read</caption>
      <thead>
        <tr className="text-left text-ink-muted">
          <th className="py-1.5 font-medium">#</th>
          <th className="py-1.5 font-medium">Line</th>
          <th className="py-1.5 text-right font-medium">Unit price</th>
          <th className="py-1.5 text-right font-medium">Amount</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((f) => (
          <tr key={f.id} className="border-t border-line">
            <td className="py-1.5">{f.seq}</td>
            <td className="py-1.5">{f.label}</td>
            <td className="num py-1.5 text-right">{f.read_unit_price ?? "—"}</td>
            <td className="num py-1.5 text-right">{f.read_amount ?? "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Reviewed supplier document → a purchase, in stock at once (owner, 2026-11-12). Every line
 * was checked by a person first; the original file stays on the purchase unaltered. */
export function ToPurchase({ ex, ready, canReceive, onDone, vendor }: { ex: OcrExtraction; ready: boolean; canReceive: boolean; onDone: (e: OcrExtraction) => void; vendor: string }) {
  const [checked, setChecked] = useState(false);
  const [airport, setAirport] = useState(false);
  const [pieces, setPieces] = useState("");
  const [expected, setExpected] = useState(todayIso());
  const [openQuick, setOpenQuick] = useState<Shipment[]>([]);
  const [same, setSame] = useState<Record<string, boolean | undefined>>({});
  const [reason, setReason] = useState("");
  const [needReason, setNeedReason] = useState(false);
  const [dup, setDup] = useState<string | null>(null);
  const [expiries, setExpiries] = useState<Record<string, string>>({});
  const { details } = useGridProducts();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const h = ex.header ?? {};
  const pick = (k: string) => (h[k]?.corrected !== undefined ? h[k]?.corrected : h[k]?.value) ?? null;
  const isPacket = h.kind?.value === "AIR_PACKET" || !!pick("awb_no");
  useEffect(() => {
    if (ex.shipment_id || !canReceive) return;
    api.shipments({ stage: "TO_COMPLETE", warehouse_id: ex.warehouse_id, limit: 200 }).then((p) => setOpenQuick(p.items), () => setOpenQuick([]));
  }, [ex.shipment_id, ex.warehouse_id, canReceive]);
  useEffect(() => {
    if (isPacket) setAirport(true);
  }, [isPacket]);
  const kept = ex.fields.filter((f) => (f.resolution === "CONFIRMED" || f.resolution === "CORRECTED") && f.final_product_id && f.final_unit_code);
  const quick = matchingQuick(openQuick, kept.map((f) => ({ product_id: f.final_product_id as string, unit_code: f.final_unit_code as string })));
  const undecided = quick.filter((q) => same[q.id] === undefined);
  const date = pick("document_date");
  // Products that track expiry need their date before they can go to stock.
  // (Goods a quick receive already put on the shelf have theirs.)
  const shelved = new Set(quick.filter((q) => same[q.id]).flatMap((q) => q.items.map((i) => `${i.product_id}:${i.unit_code}`)));
  const dated = kept.filter((f) => details.get(f.final_product_id as string)?.tracks_expiry && !shelved.has(`${f.final_product_id}:${f.final_unit_code}`));
  const missingExpiry = dated.some((f) => !expiries[f.id]);

  async function go(confirmDuplicate = false) {
    setError(null);
    setBusy(true);
    try {
      onDone(
        await api.ocrToPurchase(ex.id, {
          version: ex.version,
          vendor_id: vendor,
          vendor_invoice_no: pick("document_no"),
          invoice_date: date && /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : null,
          currency: pick("currency"),
          reviewed_all: true,
          is_airport: airport,
          awb_no: airport ? pick("awb_no") : null,
          expected_on: airport ? expected || null : null,
          expected_pieces: airport && pieces ? Number(pieces) : null,
          absorb_quick_ids: quick.filter((q) => same[q.id]).map((q) => q.id),
          reason: reason.trim() || null,
          confirm_duplicate: confirmDuplicate,
          expiries: Object.fromEntries(dated.flatMap((f) => (expiries[f.id] ? [[f.id, expiryIso(expiries[f.id]) as string]] : []))),
        }),
      );
    } catch (e) {
      if (e instanceof ApiError && e.code === "INVOICE_ALREADY_ENTERED") setDup(String(e.extra.shipment_no ?? ""));
      else {
        if (e instanceof ApiError && e.code === "MATCH_REASON_REQUIRED") setNeedReason(true);
        setError(purchaseMessage(e));
      }
    } finally {
      setBusy(false);
    }
  }
  if (ex.shipment_id)
    return (
      <p role="status" className="alert-success">
        Saved as a purchase. Its goods are in stock now. <Link className="font-medium underline" to={`/purchases/${ex.shipment_id}`}>Open the purchase</Link>
      </p>
    );
  return (
    <section aria-labelledby="rcv-h" className="card space-y-4 p-6">
      <div>
        <h2 id="rcv-h" className="font-semibold">Save &amp; add to stock</h2>
        <p className="mt-1 text-sm text-ink-muted">The kept lines become a purchase from this supplier and go into stock at once. The scanned file stays on the purchase exactly as received. Lines can still be added by hand above, or on the purchase afterwards.</p>
      </div>
      <label className="flex items-start gap-2.5 text-sm">
        <input type="checkbox" className="mt-0.5 size-4" checked={airport} onChange={(e) => setAirport(e.target.checked)} />
        <span>
          <span className="font-medium">Airport shipment, verify pieces on arrival</span>
          <span className="block text-ink-muted">In stock as soon as it is saved, even before pickup. The pieces are checked later.</span>
        </span>
      </label>
      {airport && (
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="tp-exp">Expected arrival</label>
            <input id="tp-exp" type="date" className="input" value={expected} onChange={(e) => setExpected(e.target.value)} />
          </div>
          <div>
            <label className="label" htmlFor="tp-pcs">Pieces on the airway bill</label>
            <input id="tp-pcs" className="input num" inputMode="numeric" maxLength={7} placeholder="Optional" value={pieces} onChange={(e) => setPieces(e.target.value.replace(/\D/g, ""))} />
          </div>
        </div>
      )}
      {dated.length > 0 && (
        <fieldset className="space-y-2 rounded-ctl border border-line p-4">
          <legend className="px-1 text-sm font-semibold">Expiry dates</legend>
          <p className="text-sm text-ink-muted">These products track expiry. Enter the date printed on the goods or the documents.</p>
          {dated.map((f) => (
            <div key={f.id} className="flex flex-wrap items-center gap-3 text-sm">
              <label className="min-w-0 flex-1 font-medium" htmlFor={`tp-x-${f.id}`}>{f.final_product_name ?? f.label}</label>
              <input id={`tp-x-${f.id}`} type="date" className="input w-44" value={expiries[f.id] ?? ""} onChange={(e) => setExpiries((x) => ({ ...x, [f.id]: e.target.value }))} />
            </div>
          ))}
        </fieldset>
      )}
      {quick.length > 0 && (
        <fieldset className="space-y-3 rounded-ctl border border-warning/40 bg-warning/5 p-4">
          <legend className="px-1 text-sm font-semibold">Already added by quick receive</legend>
          <p className="text-sm text-ink-muted">These goods were put into stock earlier and are waiting for their invoice. If this is that invoice, they are not added a second time.</p>
          {quick.map((q) => (
            <div key={q.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm" role="group" aria-label={`Quick receive ${q.shipment_no}`}>
              <span className="min-w-0 flex-1">
                <span className="font-medium">{goodsText(q.items, q.line_count)}</span>
                <span className="block text-xs text-ink-muted">{q.shipment_no} · added by {q.created_by_name}{q.quick_reason ? ` · “${q.quick_reason}”` : ""}</span>
              </span>
              <label className="inline-flex items-center gap-1.5">
                <input type="radio" name={`tp-same-${q.id}`} checked={same[q.id] === true} onChange={() => setSame((s) => ({ ...s, [q.id]: true }))} />
                Same goods, on this invoice
              </label>
              <label className="inline-flex items-center gap-1.5">
                <input type="radio" name={`tp-same-${q.id}`} checked={same[q.id] === false} onChange={() => setSame((s) => ({ ...s, [q.id]: false }))} />
                Different goods
              </label>
            </div>
          ))}
        </fieldset>
      )}
      {(needReason || reason) && (
        <div>
          <label className="label" htmlFor="tp-reason">Why does the invoice differ from what was added to stock? (required)</label>
          <input id="tp-reason" className="input" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
        </div>
      )}
      {error && (
        <p role="alert" className="alert-danger">
          {error}
        </p>
      )}
      {dup !== null && (
        <p role="alert" className="alert-warning">
          This invoice from this supplier was already entered as {dup}. Saving it again adds the goods to stock a second time.{" "}
          <button type="button" className="font-medium underline" disabled={busy} onClick={() => { setDup(null); void go(true); }}>Save it again anyway</button>
        </p>
      )}
      {!vendor && <p className="text-sm text-warning">Choose the supplier at the top of the page first.</p>}
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" className="mt-0.5" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
        I checked every line and the document details against the scan.
      </label>
      <div className="flex justify-end">
        {canReceive ? (
          <button type="button" className="btn-primary" disabled={busy || !ready || !checked || !vendor || undecided.length > 0 || missingExpiry} onClick={() => void go()}>
            {busy ? "Saving…" : "Save & add to stock"}
          </button>
        ) : (
          <span className="text-sm text-ink-muted">Someone who enters purchases must save it.</span>
        )}
      </div>
    </section>
  );
}
