import { useState } from "react";
import { add, cad, dec, isDecimal, mul, qty } from "../../lib/decimal";
import { SAMPLE_RECEIPT, type ReceiptLine } from "../../mock/sample";
import { ConfirmDialog, PageHeader, SampleBanner, StatusChip } from "../../ui/primitives";
import { NumericInput } from "../../ui/NumericInput";
import { Icon } from "../../ui/Icon";

const n = (s: string) => (isDecimal(s) ? dec(s) : 0n);

/** SplitDocumentReview: original documents left, editable recognized values right. */
export function ReceivingPreview() {
  const r = SAMPLE_RECEIPT;
  const [doc, setDoc] = useState(0);
  const [lines, setLines] = useState<ReceiptLine[]>(r.lines);
  const [confirm, setConfirm] = useState(false);
  const [approved, setApproved] = useState(false);

  const set = (id: string, patch: Partial<ReceiptLine>) => setLines((ls) => ls.map((l) => (l.id === id ? { ...l, ...patch, confidence: 100 } : l)));
  const short = lines.filter((l) => n(l.received) + n(l.damaged) !== n(l.declared));
  const lowConf = lines.filter((l) => l.confidence < 80);
  const goodBoxes = add(...lines.map((l) => n(l.received) - n(l.damaged)));
  const value = add(...lines.map((l) => mul(n(l.received) - n(l.damaged), n(l.cost))));

  return (
    <div className="space-y-4">
      <SampleBanner>Receiving review layout. Stock is posted only after signed approval (Module 08).</SampleBanner>
      <PageHeader
        title={`Receive ${r.shipment}`}
        subtitle={<>{r.vendor} · Bill {r.billNo} · Waybill {r.waybill} · {r.date} · {r.currency}</>}
        actions={
          approved ? (
            <StatusChip tone="success">Approved (preview)</StatusChip>
          ) : (
            <button className="btn-primary" onClick={() => setConfirm(true)} disabled={lowConf.length > 0} title={lowConf.length ? "Review low-confidence lines first" : undefined}>
              Approve &amp; post stock
            </button>
          )
        }
      />

      <div className="grid gap-5 2xl:grid-cols-[minmax(0,1fr)_minmax(0,2.4fr)]">
        <section className="card min-w-0 overflow-hidden 2xl:order-2" aria-labelledby="rl-h">
          <div className="flex flex-wrap items-center gap-4 p-5 sm:p-6">
            <span className="grid size-14 shrink-0 place-items-center rounded-2xl bg-brand-primary text-white"><Icon name="scan" size={26} /></span>
            <div className="min-w-0 flex-1">
              <h2 id="rl-h" className="text-[22px] font-semibold tracking-tight">Recognized lines</h2>
              <p className="text-sm text-ink-muted">Review OCR results, confirm quantities and resolve discrepancies.</p>
            </div>
            <div className="flex flex-wrap gap-2">
              {lowConf.length > 0 && <span className="inline-flex items-center gap-2 rounded-full bg-warning/12 px-4 py-2 text-sm font-semibold text-ink [&_svg]:text-warning"><Icon name="clock" size={16} />{lowConf.length} low-confidence</span>}
              {short.length > 0 ? (
                <span className="inline-flex items-center gap-2 rounded-full bg-danger/10 px-4 py-2 text-sm font-semibold text-danger"><Icon name="alert" size={16} />{short.length} discrepancy</span>
              ) : (
                <StatusChip tone="success">Counts reconcile</StatusChip>
              )}
            </div>
          </div>
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[52rem] border-separate border-spacing-0">
              <caption className="sr-only">Declared vs received vs damaged</caption>
              <thead>
                <tr className="[&>th]:border-y [&>th]:border-line [&>th]:bg-brand-accent/8 [&>th]:px-5 [&>th]:py-3.5 [&>th]:text-sm [&>th]:font-semibold [&>th]:text-ink">
                  <th className="text-left">Product / batch</th>
                  <th className="w-28 text-right"><span className="inline-flex items-center gap-1.5"><Icon name="inventory" size={18} className="text-brand-primary" />Declared</span></th>
                  <th className="w-44 text-right"><span className="inline-flex items-center gap-1.5"><Icon name="download" size={18} className="text-brand-primary" />Received</span></th>
                  <th className="w-36 text-right"><span className="inline-flex items-center gap-1.5"><Icon name="shield" size={18} className="text-brand-primary" />Damaged</span></th>
                  <th className="w-44 text-right"><span className="inline-flex items-center gap-1.5"><Icon name="pricing" size={18} className="text-brand-primary" />Unit cost</span></th>
                  <th className="w-36 text-left">Status</th>
                </tr>
              </thead>
              <tbody>
                {lines.map((l, i) => {
                  const mismatch = n(l.received) + n(l.damaged) !== n(l.declared);
                  return (
                    <tr key={l.id} className={`align-middle ${mismatch ? "bg-danger/[0.04]" : ""} [&>td]:border-b [&>td]:border-line [&>td]:px-5 [&>td]:py-4`}>
                      <td>
                        <div className="flex items-center gap-4">
                          <span className="grid size-14 shrink-0 place-items-center rounded-2xl border border-line bg-subtle text-ink-muted"><Icon name="inventory" size={24} /></span>
                          <div className="min-w-0">
                            <div className="text-base font-semibold">{l.product}</div>
                            <div className="text-sm text-ink-muted">Batch {l.batch} · {l.unit}</div>
                            {mismatch && <div className="mt-0.5 text-sm text-danger">Received + damaged ≠ declared</div>}
                          </div>
                        </div>
                      </td>
                      <td className="num text-right text-lg font-semibold">{l.declared}</td>
                      <td><NumericInput hideLabel label={`Line ${i + 1} received`} value={l.received} onChange={(v) => set(l.id, { received: v })} places={0} confidence={l.confidence} /></td>
                      <td><NumericInput hideLabel label={`Line ${i + 1} damaged`} value={l.damaged} onChange={(v) => set(l.id, { damaged: v })} places={0} /></td>
                      <td><NumericInput hideLabel label={`Line ${i + 1} unit cost`} value={l.cost} onChange={(v) => set(l.id, { cost: v })} unit="CAD" places={4} /></td>
                      <td>{mismatch ? <StatusChip tone="danger">Discrepancy</StatusChip> : <StatusChip tone="success">Matched</StatusChip>}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="m-4 grid gap-4 rounded-[20px] bg-brand-accent/8 p-5 sm:grid-cols-3">
            {([
              ["inventory", "Good boxes", qty(goodBoxes, 0)],
              ["coins", "Landed value (goods only)", cad(value)],
              ["receiving", "Freight / duty", "Allocation pending D-04"],
            ] as const).map(([icon, k, v], i) => (
              <div key={k} className={`flex items-center gap-4 ${i > 0 ? "sm:border-l sm:border-line sm:pl-4" : ""}`}>
                <span className="grid size-12 shrink-0 place-items-center rounded-2xl bg-card text-brand-primary"><Icon name={icon} size={22} /></span>
                <span className="min-w-0">
                  <span className="block text-sm text-ink-muted">{k}</span>
                  <span className={`num block truncate font-semibold leading-tight ${i === 2 ? "text-base" : "text-[24px]"}`}>{v}</span>
                </span>
              </div>
            ))}
          </div>
        </section>

        <section className="card flex min-w-0 flex-col overflow-hidden 2xl:order-1" aria-label="Original documents">
          <div className="flex gap-1 overflow-x-auto border-b border-line p-2" role="tablist">
            {r.documents.map((d, i) => (
              <button
                key={d}
                role="tab"
                aria-selected={doc === i}
                onClick={() => setDoc(i)}
                className={`whitespace-nowrap rounded-[var(--radius-ctl)] px-2.5 py-1 text-xs ${doc === i ? "bg-brand-secondary text-white" : "text-ink-muted hover:bg-canvas"}`}
              >
                {d}
              </button>
            ))}
          </div>
          <div className="grid min-h-80 flex-1 place-items-center bg-canvas p-6" role="tabpanel">
            <div className="grid aspect-[8.5/11] w-full max-w-sm place-items-center rounded border border-dashed border-brand-gray bg-card text-center text-sm text-ink-muted">
              <div>
                <div className="font-medium text-ink">{r.documents[doc]}</div>
                Original scan (immutable)
                <br />
                <span className="text-xs">No real document in preview</span>
              </div>
            </div>
          </div>
        </section>
      </div>

      <ConfirmDialog open={confirm} title="Approve and post stock?" confirmLabel="Approve (preview)" onClose={() => setConfirm(false)} onConfirm={() => { setApproved(true); setConfirm(false); }}>
        {qty(goodBoxes, 0)} good boxes worth {cad(value)} will be added to stock. {short.length > 0 && `${short.length} line(s) have discrepancies and will be recorded against the vendor.`} In production this requires your signature and server-side permission.
      </ConfirmDialog>
    </div>
  );
}
