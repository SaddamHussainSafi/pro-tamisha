import { QRCodeSVG } from "qrcode.react";
import { createPortal } from "react-dom";
import type { Pick } from "../../api";
import { qtyText } from "../../lib/inventory";
import { PACKING_COPIES, packingTotal, sheetDate } from "../../lib/packing";
import "./packing.css";

/** The packing sheet as the office's paper PACKING LIST (owner, 2026-11-11). Same layout as the
 *  printed PDF; CHK and MISSING GOODS stay blank for the pen. */
export function PackingPaper({ p, label = true }: { p: Pick; label?: boolean }) {
  const total = packingTotal(p.lines);
  return (
    <article className="pk-paper" aria-label={label ? `Packing sheet ${p.pick_code}, paper layout` : undefined} data-testid="packing-paper">
      <div className="pk-top">
        <div className="pk-brand">
          <p className="pk-company">TAMISHA TRADING INC.</p>
          <p className="pk-title">PACKING LIST</p>
        </div>
        <table className="pk-so">
          <thead>
            <tr>
              <th scope="col">Sales Order Number</th>
              <th scope="col">Date</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>{p.order_no}</td>
              <td>{sheetDate(p.order_date)}</td>
            </tr>
          </tbody>
        </table>
        <figure className="pk-qr">
          <QRCodeSVG value={p.pick_code} level="M" marginSize={2} role="img" aria-label={`QR code ${p.pick_code}`} />
          <figcaption>{p.pick_code}</figcaption>
        </figure>
      </div>
      <dl className="pk-who">
        <div>
          <dt>CUSTOMER NAME :</dt>
          <dd>{p.customer_name}</dd>
        </div>
        <div>
          <dt>EMPLOYEE NAME :</dt>
          <dd>{p.picked_by_name ?? ""}</dd>
        </div>
        <div className="pk-missing">
          <dt>MISSING GOODS :</dt>
          <dd />
        </div>
      </dl>
      <table className="pk-lines">
        <caption className="sr-only">Packing list lines</caption>
        <colgroup>
          <col className="pk-col-chk" />
          <col className="pk-col-qty" />
          <col className="pk-col-units" />
          <col />
          <col className="pk-col-batch" />
          <col className="pk-col-remarks" />
        </colgroup>
        <thead>
          <tr>
            <th scope="col" className="pk-c">CHK</th>
            <th scope="col" className="pk-c">QTY</th>
            <th scope="col" className="pk-c">UNITS</th>
            <th scope="col">PRODUCT</th>
            <th scope="col">Batch Number</th>
            <th scope="col">REMARKS</th>
          </tr>
        </thead>
        <tbody>
          {p.lines.map((l) => (
            <tr key={l.id} data-testid={`paper-${l.sku}`}>
              <td aria-label="Check" />
              <td className="pk-q">{qtyText(l.requested_qty)}</td>
              <td className="pk-c">{l.units}</td>
              <td className="pk-p" title={l.product_name}>{l.product_name}</td>
              <td className="pk-s">{l.lot_code ?? ""}</td>
              <td className="pk-s" title={l.remarks || undefined}>{l.remarks}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td />
            <td className="pk-q" data-testid="paper-total">{total.qty}</td>
            <td />
            <td className="pk-p">Total</td>
            <td />
            <td className="pk-s">{total.byWeight}</td>
          </tr>
        </tfoot>
      </table>
    </article>
  );
}

/** What Ctrl/Cmd+P prints on the packing sheet page: the paper sheet, PACKING_COPIES times,
 *  without the app around it (packing.css). The Print button prints the PDF instead. */
export function PackingPrintCopies({ p }: { p: Pick }) {
  return createPortal(
    <div className="pk-print" aria-hidden="true" data-testid="packing-print">
      {Array.from({ length: PACKING_COPIES }, (_, i) => (
        <PackingPaper key={i} p={p} label={false} />
      ))}
    </div>,
    document.body,
  );
}
