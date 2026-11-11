import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type OcrExtractionDetail as OcrExtraction, type OcrField, type Vendor } from "../api";
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
  supplier: "Supplier",
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
  SUPPLIER_NOT_FOUND: "The supplier isn't one of yours yet. Choose the supplier below.",
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
  return WARN[w] ?? w;
}

/** Header values as read, with engine confidence; low-confidence values are marked. */
export function SupplierHeader({ ex, threshold, editable, onFixed }: { ex: OcrExtraction; threshold: number; editable: boolean; onFixed: (e: OcrExtraction) => void }) {
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
      {h.kind?.value === "AIR_PACKET" && (
        <p className="text-sm text-ink-muted">Read as an airport delivery packet (customs declaration, vendor invoice and CFIA Confirmation of Sale). Product lines come from the Confirmation of Sale, checked against the invoice.</p>
      )}
      {(ex.warnings ?? []).length > 0 && (
        <ul className="space-y-1">
          {ex.warnings.map((w) => (
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
              <dd className="min-w-0 flex-1">
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
              </dd>
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
            </div>
          );
        })}
      </dl>
    </section>
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

/** Reviewed supplier document → declared lines on a shipment. Never stock or costs. */
export function ToReceiving({ ex, ready, canReceive, onDone }: { ex: OcrExtraction; ready: boolean; canReceive: boolean; onDone: (e: OcrExtraction) => void }) {
  const [vendors, setVendors] = useState<Vendor[]>([]);
  const [vendor, setVendor] = useState(ex.vendor_id ?? "");
  const [checked, setChecked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.vendors({ active: true }).then(setVendors, () => setVendors([]));
  }, []);
  const h = ex.header ?? {};
  const pick = (k: string) => (h[k]?.corrected !== undefined ? h[k]?.corrected : h[k]?.value) ?? null;
  async function go() {
    setError(null);
    setBusy(true);
    try {
      onDone(await api.ocrToReceiving(ex.id, { version: ex.version, vendor_id: vendor, document_no: pick("document_no"), currency: pick("currency"), reviewed_all: true }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't send to Receiving.");
    } finally {
      setBusy(false);
    }
  }
  if (ex.shipment_id)
    return (
      <p role="status" className="alert-success">
        Filed to Receiving. <Link className="font-medium underline" to={`/receiving/${ex.shipment_id}`}>Open the shipment</Link> to count and post it.
      </p>
    );
  return (
    <section aria-labelledby="rcv-h" className="card space-y-3 p-6">
      <h2 id="rcv-h" className="font-semibold">Send to Receiving</h2>
      <p className="text-sm text-ink-muted">The document is attached to a new shipment and the kept lines become what the supplier declared. Nothing is added to stock and nothing is owed until the goods are counted and posted.</p>
      {error && (
        <p role="alert" className="alert-danger">
          {error}
        </p>
      )}
      <div className="max-w-md">
        <label className="label" htmlFor="rcv-vendor">Supplier</label>
        <select id="rcv-vendor" className="input" value={vendor} onChange={(e) => setVendor(e.target.value)}>
          <option value="">Choose…</option>
          {vendors.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
            </option>
          ))}
        </select>
      </div>
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" className="mt-0.5" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
        I checked every line and the document details against the scan.
      </label>
      <div className="flex justify-end">
        {canReceive ? (
          <button type="button" className="btn-primary" disabled={busy || !ready || !checked || !vendor} onClick={() => void go()}>
            Send to Receiving
          </button>
        ) : (
          <span className="text-sm text-ink-muted">Someone who records receiving must send it.</span>
        )}
      </div>
    </section>
  );
}
