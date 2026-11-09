import { QRCodeSVG } from "qrcode.react";
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, canInWarehouse, type Disposition, type Pick, type PickStatus, type PickSummary, type ShortageReason } from "../api";
import { useAuth } from "../auth";
import { qtyText, wholeOnly } from "../lib/inventory";
import { ordersMessage, PICK_STATUS, pickOutcome, SHORTAGE_LABEL } from "../lib/orders";
import { DataGrid, type Column } from "../ui/DataGrid";
import { NumericInput } from "../ui/NumericInput";
import { ConfirmDialog, EmptyState, ErrorState, FilterBar, LoadingRows, PageHeader, PageSkeleton, StatusChip } from "../ui/primitives";
import { ReasonDialog } from "./Orders";

const link = "font-medium text-brand-primary-hover underline-offset-2 hover:underline";
const PickChip = ({ status }: { status: PickStatus }) => <StatusChip tone={PICK_STATUS[status].tone}>{PICK_STATUS[status].label}</StatusChip>;

type Queue = "todo" | "check" | "done" | "all";
const QUEUES: { value: Queue; label: string }[] = [
  { value: "todo", label: "To pick" },
  { value: "check", label: "Awaiting check" },
  { value: "done", label: "Done" },
  { value: "all", label: "All" },
];
const QUEUE_STATUS: Record<Queue, string | undefined> = {
  todo: "CREATED,IN_PROGRESS,NEEDS_CORRECTION",
  check: "SUBMITTED",
  done: "VERIFIED,INVOICED,ABORTED",
  all: undefined,
};

export function PickingPage() {
  const nav = useNavigate();
  const [queue, setQueue] = useState<Queue>("todo");
  const [code, setCode] = useState("");
  const [rows, setRows] = useState<PickSummary[] | null>(null);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    try {
      setRows(await api.picks({ status: code.trim() ? undefined : QUEUE_STATUS[queue], code: code.trim() || undefined }));
      setError(false);
    } catch {
      setError(true);
    }
  }, [queue, code]);
  useEffect(() => {
    const t = setTimeout(() => void load(), 200);
    return () => clearTimeout(t);
  }, [load]);

  const columns: Column<PickSummary>[] = [
    { key: "c", header: "Sheet", cell: (r) => <Link className={link} to={`/picking/${r.id}`}>{r.pick_code}</Link>, sort: (r) => r.pick_code },
    { key: "o", header: "Order", cell: (r) => r.order_no },
    { key: "cu", header: "Customer", cell: (r) => r.customer_name },
    { key: "l", header: "Lines", align: "right", cell: (r) => <span className="num">{r.line_count}</span> },
    { key: "s", header: "Status", cell: (r) => <PickChip status={r.status} /> },
    { key: "t", header: "Created", cell: (r) => new Date(r.created_at).toLocaleString(), sort: (r) => r.created_at },
  ];
  return (
    <div className="space-y-5">
      <PageHeader title="Picking" subtitle="Scan or type a sheet code to open it. Record what was actually packed." />
      <FilterBar search={code} onSearch={setCode} options={QUEUES} value={queue} onChange={setQueue} placeholder="Sheet code (scan QR)" />
      <section className="card overflow-clip">
        {error ? (
          <ErrorState title="Couldn't load picking sheets" onRetry={() => void load()} />
        ) : rows === null ? (
          <LoadingRows cols={7} />
        ) : (
          <DataGrid
            rows={rows}
            columns={columns}
            rowKey={(r) => r.id}
            caption={`Picking sheets (${rows.length})`}
            onOpen={(r) => nav(`/picking/${r.id}`)}
            empty={<EmptyState title="Nothing in this queue" hint="Sheets appear when an order's picking sheet is printed." />}
          />
        )}
      </section>
    </div>
  );
}

type Entry = { qty: string; reason: ShortageReason | "" };

export function PickSheetPage() {
  const { id = "" } = useParams();
  const { session } = useAuth();
  const [p, setP] = useState<Pick | null>(null);
  const [loadErr, setLoadErr] = useState(false);
  const [entries, setEntries] = useState<Record<string, Entry>>({});
  const [disp, setDisp] = useState<Record<string, Disposition>>({});
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [dialog, setDialog] = useState<"none" | "submit" | "verify" | "return" | "abort">("none");
  const [reason, setReason] = useState("");

  const apply = (x: Pick) => {
    setP(x);
    setEntries(Object.fromEntries(x.lines.map((l) => [l.id, { qty: l.picked_qty === null ? "" : qtyText(l.picked_qty), reason: l.shortage_reason ?? "" }])));
    setDisp(Object.fromEntries(x.lines.map((l) => [l.id, l.disposition ?? "BACKORDER"])));
  };
  const load = useCallback(async () => {
    try {
      apply(await api.pick(id));
      setLoadErr(false);
    } catch {
      setLoadErr(true);
    }
  }, [id]);
  useEffect(() => {
    // Initial load only; ignore a stale response so it can't wipe quantities typed meanwhile.
    let live = true;
    api.pick(id).then(
      (x) => {
        if (!live) return;
        apply(x);
        setLoadErr(false);
      },
      () => live && setLoadErr(true),
    );
    return () => {
      live = false;
    };
  }, [id]);

  const run = async (fn: () => Promise<Pick>, msg: string) => {
    setError(null);
    setSaved(null);
    try {
      apply(await fn());
      setSaved(msg);
      setDialog("none");
      setReason("");
    } catch (err) {
      setError(ordersMessage(err));
      setDialog("none");
      // Keep what the picker typed on validation errors; only refresh when the sheet changed underneath.
      if (err instanceof ApiError && err.status === 409) await load();
    }
  };

  if (!session) return null;
  if (loadErr) return <ErrorState title="Couldn't load this picking sheet" onRetry={() => void load()} />;
  if (!p) return <PageSkeleton />;
  const canRecord = canInWarehouse(session, "pick.record", p.warehouse_id) && ["CREATED", "IN_PROGRESS", "NEEDS_CORRECTION"].includes(p.status);
  const canVerify = canInWarehouse(session, "pick.verify", p.warehouse_id) && p.status === "SUBMITTED";
  const canClose = canInWarehouse(session, "sales_order.close", p.warehouse_id);
  const canAbort = canInWarehouse(session, "sales_order.manage", p.warehouse_id) && ["CREATED", "IN_PROGRESS", "SUBMITTED", "NEEDS_CORRECTION"].includes(p.status);
  const complete = p.lines.every((l) => l.picked_qty !== null);
  const shortLines = p.lines.filter((l) => l.outcome && l.outcome !== "PICKED");
  const cancelling = shortLines.some((l) => disp[l.id] === "CANCEL");
  // API keys dispositions by order line; only short lines are sent.
  const dispositions = Object.fromEntries(shortLines.map((l) => [l.order_line_id, disp[l.id] ?? "BACKORDER"]));

  const set = (lineId: string, v: Partial<Entry>) => setEntries((e) => ({ ...e, [lineId]: { qty: "", reason: "", ...e[lineId], ...v } }));
  const record = () => {
    const lines = p.lines
      .filter((l) => (entries[l.id]?.qty ?? "") !== "")
      .map((l) => {
        const e = entries[l.id] ?? { qty: "", reason: "" };
        const short = pickOutcome(l.requested_qty, e.qty) !== "PICKED";
        return { line_id: l.id, picked_qty: e.qty, shortage_reason: short && e.reason ? e.reason : null };
      });
    if (lines.length === 0) return setError("Enter at least one packed quantity.");
    void run(() => api.recordPick(p.id, p.version, lines), "Quantities saved.");
  };

  return (
    <div className="space-y-5">
      <PageHeader
        title={`Picking sheet ${p.pick_code}`}
        subtitle={<>Order <Link className={link} to={`/orders/${p.order_id}`}>{p.order_no}</Link> · {p.customer_name} · <PickChip status={p.status} /></>}
        actions={
          <>
            <button className="btn-ghost print:hidden" onClick={() => window.print()}>Print</button>
            {canAbort && <button className="btn-ghost print:hidden" onClick={() => setDialog("abort")}>Void sheet</button>}
            {canVerify && <button className="btn-ghost print:hidden" onClick={() => setDialog("return")}>Send back</button>}
            {canVerify && <button className="btn-primary print:hidden" onClick={() => setDialog("verify")}>Verify pick</button>}
            {canRecord && complete && <button className="btn-primary print:hidden" onClick={() => setDialog("submit")}>Submit for check</button>}
            {p.status === "VERIFIED" && (canInWarehouse(session, "invoice.prepare", p.warehouse_id) || canInWarehouse(session, "invoice.issue", p.warehouse_id)) && (
              <Link className="btn-primary print:hidden" to={`/picking/${p.id}/invoice`}>Prepare invoice</Link>
            )}
            {p.status === "INVOICED" && <InvoiceLink pickId={p.id} />}
          </>
        }
      />
      {p.status === "NEEDS_CORRECTION" && p.status_note && <p className="alert-warning" role="status">Sent back: {p.status_note}</p>}
      {error && <p className="alert-danger" role="alert">{error}</p>}
      {saved && <p className="alert-success" role="status">{saved}</p>}
      <div className="grid gap-5 lg:grid-cols-[1fr_auto]">
        <section className="card overflow-x-auto p-0" aria-label="Pick lines">
          <table className="w-full">
            <caption className="sr-only">Pick lines</caption>
            <thead>
              <tr>
                <th className="th">#</th>
                <th className="th">Product</th>
                <th className="th text-right">Requested</th>
                <th className="th">Packed</th>
                <th className="th">Result</th>
                {(canVerify || shortLines.some((l) => l.disposition)) && <th className="th">Shortfall</th>}
              </tr>
            </thead>
            <tbody>
              {p.lines.map((l) => {
                const e = entries[l.id] ?? { qty: "", reason: "" };
                const live = e.qty !== "" ? pickOutcome(l.requested_qty, e.qty) : null;
                const outcome = canRecord ? live : l.outcome;
                return (
                  <tr key={l.id} data-testid={`pick-${l.sku}`}>
                    <td className="td num">{l.line_no}</td>
                    <td className="td"><span className="font-medium">{l.sku}</span> <span className="text-ink-muted">{l.product_name}</span></td>
                    <td className="td num text-right">{qtyText(l.requested_qty)} {l.unit_code}</td>
                    <td className="td">
                      {canRecord ? (
                        <div className="flex flex-wrap items-end gap-2">
                          <div className="w-32">
                            <NumericInput label={`Packed ${l.sku}`} value={e.qty} onChange={(v) => set(l.id, { qty: v })} places={wholeOnly(l.unit_code) ? 0 : 4} unit={l.unit_code} />
                          </div>
                          <button type="button" className="btn-ghost py-1 text-xs" onClick={() => set(l.id, { qty: "0" })} aria-label={`None packed for ${l.sku}`}>NO</button>
                          {live && live !== "PICKED" && (
                            <div>
                              <label className="label" htmlFor={`sr-${l.id}`}>Why short?</label>
                              <select id={`sr-${l.id}`} className="input" value={e.reason} onChange={(ev) => set(l.id, { reason: ev.target.value as ShortageReason })}>
                                <option value="">Choose…</option>
                                {Object.entries(SHORTAGE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                              </select>
                            </div>
                          )}
                        </div>
                      ) : (
                        <span className="num">{l.picked_qty === null ? "—" : `${qtyText(l.picked_qty)} ${l.unit_code}`}</span>
                      )}
                    </td>
                    <td className="td">
                      {outcome === "PICKED" && <StatusChip tone="success">Picked</StatusChip>}
                      {outcome === "SHORT" && <StatusChip tone="warning">Short</StatusChip>}
                      {outcome === "NO" && <StatusChip tone="danger">NO</StatusChip>}
                      {!canRecord && l.shortage_reason && <span className="ml-2 text-xs text-ink-muted">{SHORTAGE_LABEL[l.shortage_reason]}</span>}
                    </td>
                    {(canVerify || shortLines.some((x) => x.disposition)) && (
                      <td className="td">
                        {l.outcome && l.outcome !== "PICKED" ? (
                          canVerify ? (
                            <select aria-label={`Shortfall for ${l.sku}`} className="input" value={disp[l.id] ?? "BACKORDER"} onChange={(ev) => setDisp((d) => ({ ...d, [l.id]: ev.target.value as Disposition }))}>
                              <option value="BACKORDER">Keep pending (backorder)</option>
                              {canClose && <option value="CANCEL">Cancel shortfall</option>}
                            </select>
                          ) : (
                            <span className="text-sm">{l.disposition === "CANCEL" ? "Cancelled" : "Backorder"}</span>
                          )
                        ) : null}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
          {canRecord && (
            <div className="flex justify-end border-t border-line p-3 print:hidden">
              <button className="btn-primary" onClick={record}>Save quantities</button>
            </div>
          )}
        </section>
        <aside className="card flex flex-col items-center gap-2 p-6" aria-label="Sheet code">
          <QRCodeSVG value={p.pick_code} size={128} role="img" aria-label={`QR code ${p.pick_code}`} />
          <span className="num text-sm font-semibold">{p.pick_code}</span>
          <ul className="w-full space-y-1 text-xs text-ink-muted">
            <li>Printed by {p.created_by_name ?? "—"}</li>
            {p.picked_by_name && <li>Picked by {p.picked_by_name}</li>}
            {p.verified_by_name && <li>Verified by {p.verified_by_name}</li>}
          </ul>
        </aside>
      </div>

      <ConfirmDialog open={dialog === "submit"} title="Submit for checking?" confirmLabel="Submit" onClose={() => setDialog("none")} onConfirm={() => void run(() => api.submitPick(p.id, p.version), "Submitted for checking.")}>
        A supervisor checks the packed quantities before an invoice can be made.
      </ConfirmDialog>
      {cancelling ? (
        <ReasonDialog
          open={dialog === "verify"}
          title="Verify and cancel shortfall?"
          body="Cancelled shortfall will not be supplied later. Other shortfall stays pending."
          confirmLabel="Verify pick"
          reason={reason}
          setReason={setReason}
          onClose={() => setDialog("none")}
          onConfirm={() => void run(() => api.verifyPick(p.id, { version: p.version, dispositions, reason }), "Pick verified.")}
        />
      ) : (
        <ConfirmDialog open={dialog === "verify"} title="Verify this pick?" confirmLabel="Verify pick" onClose={() => setDialog("none")} onConfirm={() => void run(() => api.verifyPick(p.id, { version: p.version, dispositions, reason: "" }), "Pick verified.")}>
          Packed quantities become ready to invoice. Any shortfall stays pending for this customer.
        </ConfirmDialog>
      )}
      <ReasonDialog open={dialog === "return"} title="Send back for correction?" body="The picker will see your note and can fix the quantities." confirmLabel="Send back" reason={reason} setReason={setReason} onClose={() => setDialog("none")} onConfirm={() => void run(() => api.returnPick(p.id, p.version, reason), "Sent back to picker.")} />
      <ReasonDialog open={dialog === "abort"} title="Void this sheet?" body="Nothing on it will be invoiced. You can print a new sheet from the order." confirmLabel="Void sheet" reason={reason} setReason={setReason} onClose={() => setDialog("none")} onConfirm={() => void run(() => api.abortPick(p.id, p.version, reason), "Sheet voided.")} />
    </div>
  );
}

function InvoiceLink({ pickId }: { pickId: string }) {
  const [id, setId] = useState<string | null>(null);
  useEffect(() => {
    api.invoices({ picking_attempt_id: pickId }).then((r) => setId(r.items[0]?.id ?? null), () => setId(null));
  }, [pickId]);
  return id ? <Link className="btn-primary print:hidden" to={`/invoices/${id}`}>View invoice</Link> : null;
}
