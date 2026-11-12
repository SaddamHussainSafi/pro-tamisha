import { useState } from "react";
import { addrText, mail, mailError, OUTGOING, whenFull, type Outgoing } from "../../lib/mail";
import { Icon } from "../../ui/Icon";
import { StatusChip } from "../../ui/primitives";
import { Modal } from "../gmail/PagePreview";

const ACTIVE = new Set(["QUEUED", "SENDING"]);
const PROBLEM = new Set(["FAILED", "UNCERTAIN"]);

/** One line above the list while something is on its way or needs a decision. */
export function OutboxStrip({ items, onOpen }: { items: Outgoing[]; onOpen: () => void }) {
  const active = items.filter((o) => ACTIVE.has(o.status)).length;
  const problems = items.filter((o) => PROBLEM.has(o.status));
  if (!active && !problems.length) return null;
  const tone = problems.length ? "border-warning/30 bg-warning/5" : "border-info/20 bg-info/5";
  const parts = [active ? `${active} sending` : "", problems.filter((o) => o.status === "FAILED").length ? `${problems.filter((o) => o.status === "FAILED").length} failed` : "", problems.filter((o) => o.status === "UNCERTAIN").length ? `${problems.filter((o) => o.status === "UNCERTAIN").length} may have been sent` : ""].filter(Boolean);
  return (
    <button type="button" onClick={onOpen} className={`flex w-full cursor-pointer items-center gap-2 border-b px-4 py-2 text-left text-sm ${tone}`} aria-label={`Outbox: ${parts.join(", ")}. Open the outbox`}>
      <Icon name={problems.length ? "alert" : "clock"} size={16} className={problems.length ? "text-warning" : "text-info"} />
      <span className="min-w-0 flex-1 truncate">Outbox: {parts.join(" · ")}</span>
      <span className="text-xs font-medium text-brand-primary-hover">View</span>
    </button>
  );
}

/** Everything I sent from this mailbox recently, with its real delivery state. A failed or
 * uncertain message is only sent again after the person says so (uncertain: twice). */
export function OutboxDialog({ open, items, onClose, onChanged }: { open: boolean; items: Outgoing[]; onClose: () => void; onChanged: () => void }) {
  const [confirm, setConfirm] = useState<Outgoing | null>(null);
  const [checked, setChecked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function retry(o: Outgoing) {
    setBusy(true);
    setError(null);
    try {
      await mail.retry(o.id, o.status === "UNCERTAIN" && checked);
      setConfirm(null);
      setChecked(false);
      onChanged();
    } catch (err) {
      setError(mailError(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal open={open} title={confirm ? "Send again?" : "Outbox"} onClose={() => (confirm ? setConfirm(null) : onClose())}>
      <div className="space-y-3 p-5 text-sm">
        {confirm ? (
          <>
            <p className="font-medium">{confirm.subject || "(no subject)"}</p>
            {confirm.status === "UNCERTAIN" ? (
              <>
                <p className="alert-warning">{OUTGOING.UNCERTAIN?.detail} Open the Sent folder (or ask the recipient) first.</p>
                <label className="flex items-start gap-2.5">
                  <input type="checkbox" className="mt-0.5 size-4 accent-brand-primary" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
                  <span>I checked Sent and it is not there. Send it again, knowing it may arrive twice.</span>
                </label>
              </>
            ) : (
              <p className="text-ink-muted">Lark did not accept this message{confirm.last_error ? ` (${confirm.last_error})` : ""}. It goes back in the queue and is tried again.</p>
            )}
            {error && (
              <p role="alert" className="alert-danger">
                {error}
              </p>
            )}
            <div className="flex justify-end gap-2.5">
              <button type="button" className="btn-ghost" onClick={() => setConfirm(null)}>
                Cancel
              </button>
              <button type="button" className="btn-primary" disabled={busy || (confirm.status === "UNCERTAIN" && !checked)} onClick={() => void retry(confirm)}>
                Send again
              </button>
            </div>
          </>
        ) : items.length === 0 ? (
          <p className="text-ink-muted">Nothing sent from this mailbox yet.</p>
        ) : (
          <ul className="divide-y divide-line" aria-label="Sent from Pro Trading">
            {items.map((o) => {
              const s = OUTGOING[o.status] ?? { tone: "neutral" as const, label: o.status, detail: "" };
              return (
                <li key={o.id} className="space-y-1 py-3">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                    <span className="min-w-0 flex-1 truncate font-medium">{o.subject || "(no subject)"}</span>
                    <StatusChip tone={s.tone}>{s.label}</StatusChip>
                  </div>
                  <p className="truncate text-xs text-ink-muted">
                    To {o.to.map(addrText).join(", ") || "(Bcc only)"} · {whenFull(o.created_at)}
                    {o.attempts > 0 ? ` · ${o.attempts} ${o.attempts === 1 ? "attempt" : "attempts"}` : ""}
                  </p>
                  <p className="text-xs text-ink-muted">
                    {s.detail}
                    {o.last_error && o.status !== "SENT" ? ` Last problem: ${o.last_error}` : ""}
                    {o.status === "SENT" && o.seen_in_sent ? " Seen in the Sent folder." : ""}
                  </p>
                  {PROBLEM.has(o.status) && (
                    <button type="button" className="btn-ghost mt-1 px-4 py-1.5 text-xs" onClick={() => {
                        setConfirm(o);
                        setChecked(false);
                        setError(null);
                      }}>
                      Send again…
                    </button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </Modal>
  );
}
