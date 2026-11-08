import { useState } from "react";
import { add, cad, dec, isDecimal, mul, qty } from "../../lib/decimal";
import { SAMPLE_INVOICE_LINES, type InvoiceLine } from "../../mock/sample";
import { Icon } from "../../ui/Icon";
import { ConfirmDialog, PageHeader, SampleBanner, StatusChip } from "../../ui/primitives";
import { NumericInput } from "../../ui/NumericInput";

/** Weight-priced lines bill weight × rate; unit-priced lines bill count × rate. Preview only — server computes real totals. */
function lineAmount(l: InvoiceLine): bigint | null {
  const basis = l.rateBasis === "per unit" ? l.boxes : l.weight;
  if (!isDecimal(basis) || !isDecimal(l.rate)) return null;
  return mul(dec(basis), dec(l.rate));
}

export function InvoicePreview() {
  const [lines, setLines] = useState(SAMPLE_INVOICE_LINES);
  const [remove, setRemove] = useState<InvoiceLine | null>(null);

  const set = (id: string, patch: Partial<InvoiceLine>) => setLines((ls) => ls.map((l) => (l.id === id ? { ...l, ...patch } : l)));
  const amounts = lines.map(lineAmount);
  const invalid = amounts.some((a) => a === null);
  const subtotal = add(...amounts.map((a) => a ?? 0n));
  const boxes = add(...lines.map((l) => (isDecimal(l.boxes) ? dec(l.boxes) : 0n)));

  return (
    <div className="space-y-4">
      <SampleBanner>Invoice <em>editor</em> layout only. The printed PDF will follow the customer-supplied template (D-12), not this screen.</SampleBanner>
      <PageHeader
        title="Invoice INV-DEMO-DRAFT"
        subtitle={<>Draft · Demo Market Co. · from SO-DEMO-1036</>}
        actions={
          <>
            <button className="btn-ghost">Save draft</button>
            <button className="btn-primary" disabled title="Blocked: issuance trigger (D-01) and tax rules (D-03) are open decisions">Finalize &amp; issue</button>
          </>
        }
      />

      <div className="grid gap-4 xl:grid-cols-[1fr_20rem]">
        <section className="card min-w-0 overflow-hidden" aria-labelledby="lines-h">
          <div className="flex items-center justify-between border-b border-line px-4 py-3">
            <h2 id="lines-h" className="font-semibold">Lines</h2>
            <StatusChip tone="warning">Weights estimated — not scale-verified</StatusChip>
          </div>
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[56rem] border-separate border-spacing-0">
              <caption className="sr-only">Invoice lines</caption>
              <thead>
                <tr>
                  <th className="th">Product</th>
                  <th className="th w-24 text-right">Qty</th>
                  <th className="th w-36 text-right">Weight</th>
                  <th className="th w-40 text-right">Rate (CAD)</th>
                  <th className="th w-20">Tax</th>
                  <th className="th w-36 text-right">Amount</th>
                  <th className="th w-10"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {lines.map((l, i) => (
                  <tr key={l.id} className="align-top">
                    <td className="td">
                      <div className="font-medium">{l.product}</div>
                      <div className="text-xs text-ink-muted">Line {i + 1} · {l.unit}</div>
                    </td>
                    <td className="td"><NumericInput hideLabel label={`Line ${i + 1} quantity`} value={l.boxes} onChange={(v) => set(l.id, { boxes: v })} unit={l.unit} places={0} /></td>
                    <td className="td">
                      {l.rateBasis === "per unit" ? (
                        <span className="block pt-1.5 text-right text-xs text-ink-muted">n/a (unit-priced)</span>
                      ) : (
                        <NumericInput hideLabel label={`Line ${i + 1} weight`} value={l.weight} onChange={(v) => set(l.id, { weight: v })} unit={l.weightUnit} />
                      )}
                    </td>
                    <td className="td">
                      <NumericInput hideLabel label={`Line ${i + 1} rate`} value={l.rate} onChange={(v) => set(l.id, { rate: v })} unit={l.rateBasis} places={4} />
                    </td>
                    <td className="td pt-4 text-xs"><span className="chip border-line bg-canvas text-ink-muted">{l.taxCode}</span></td>
                    <td className="td num pt-4 text-right font-medium">{(() => { const a = amounts[i]; return a == null ? <span className="text-danger">—</span> : cad(a); })()}</td>
                    <td className="td pt-3">
                      <button className="rounded p-1 text-ink-muted hover:bg-danger/10 hover:text-danger" aria-label={`Remove line ${i + 1}`} onClick={() => setRemove(l)}>
                        <Icon name="x" size={16} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <aside className="card h-fit p-4" aria-labelledby="totals-h">
          <h2 id="totals-h" className="font-semibold">Totals</h2>
          <dl className="mt-3 space-y-2 text-sm">
            <div className="flex justify-between"><dt className="text-ink-muted">Boxes / cases</dt><dd className="num">{qty(boxes, 0)}</dd></div>
            <div className="flex justify-between"><dt className="text-ink-muted">Subtotal</dt><dd className="num">{cad(subtotal)}</dd></div>
            <div className="flex justify-between"><dt className="text-ink-muted">Tax</dt><dd className="text-right text-xs font-medium text-ink">Pending tax rules (D-03)</dd></div>
            <div className="flex justify-between border-t border-line pt-2 text-base font-semibold"><dt>Total</dt><dd className="num">{invalid ? "—" : cad(subtotal)}<span className="block text-right text-xs font-normal text-ink-muted">before tax</span></dd></div>
          </dl>
          {invalid && <p className="alert-danger mt-3">Fix highlighted lines before saving.</p>}
          <p className="mt-3 text-xs text-ink-muted">Amounts are exact decimals rounded half-up per line. The server recalculates on save; finalized invoices are immutable.</p>
        </aside>
      </div>

      <ConfirmDialog
        open={remove !== null}
        title="Remove line?"
        confirmLabel="Remove line"
        danger
        onClose={() => setRemove(null)}
        onConfirm={() => {
          if (remove) setLines((ls) => ls.filter((l) => l.id !== remove.id));
          setRemove(null);
        }}
      >
        {remove && <>{remove.product} — {remove.boxes} {remove.unit}{remove.weight && `, ${remove.weight} ${remove.weightUnit}`}. This only affects the draft.</>}
      </ConfirmDialog>
    </div>
  );
}
