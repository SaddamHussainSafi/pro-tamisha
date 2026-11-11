import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { useAuth } from "../auth";
import {
  deleteMode,
  insteadLabel,
  KIND_LABEL,
  notifyTrashChanged,
  recordLink,
  referenceLine,
  referenceLines,
  trashApi,
  trashErrorText,
  twoStepReady,
  type Reference,
  type TrashItem,
  type TrashKind,
  type TrashPreview,
} from "../lib/trash";
import { Icon } from "./Icon";

/* ------------------------------------------------------------------ toast (Undo) */

type Toast =
  | { kind: "trashed"; item: TrashItem }
  | { kind: "restored"; label: string; to: string }
  | { kind: "done"; text: string }
  | { kind: "error"; text: string };
let showToast: (t: Toast) => void = () => {};

/** One per app (in the Shell): "“X” moved to Trash · Undo". Polite live region, no role. */
export function TrashToastHost() {
  const [toast, setToast] = useState<Toast | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    showToast = setToast;
    return () => {
      showToast = () => {};
    };
  }, []);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), toast.kind === "trashed" ? 10000 : 6000);
    return () => clearTimeout(t);
  }, [toast]);

  async function undo(item: TrashItem) {
    setBusy(true);
    try {
      await trashApi.restore(item.id);
      notifyTrashChanged();
      setToast({ kind: "restored", label: item.label, to: recordLink(item.kind, item.item_id) });
    } catch (e) {
      setToast({ kind: "error", text: trashErrorText(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div aria-live="polite" className="pointer-events-none fixed inset-x-4 bottom-4 z-50 flex justify-center sm:inset-x-auto sm:right-6">
      {toast && (
        <div className="pointer-events-auto flex max-w-md items-center gap-3 rounded-ctl bg-ink px-4 py-3 text-sm text-white shadow-[0_8px_24px_rgb(24_24_27/0.12)]">
          <Icon name={toast.kind === "error" ? "alert" : "trash"} size={18} />
          {toast.kind === "trashed" && (
            <>
              <span className="min-w-0 flex-1">
                <span className="font-medium">“{toast.item.label}”</span> moved to Trash.
              </span>
              <button type="button" className="cursor-pointer rounded-full px-2 py-1 font-semibold underline-offset-2 hover:underline" disabled={busy} onClick={() => void undo(toast.item)}>
                Undo
              </button>
              <Link to="/trash" className="rounded-full px-2 py-1 underline-offset-2 hover:underline" onClick={() => setToast(null)}>
                Open Trash
              </Link>
            </>
          )}
          {toast.kind === "restored" && (
            <>
              <span className="min-w-0 flex-1">
                <span className="font-medium">“{toast.label}”</span> restored.
              </span>
              <Link to={toast.to} className="rounded-full px-2 py-1 font-semibold underline-offset-2 hover:underline" onClick={() => setToast(null)}>
                Open
              </Link>
            </>
          )}
          {(toast.kind === "error" || toast.kind === "done") && <span className="min-w-0 flex-1">{toast.text}</span>}
          <button type="button" className="grid size-8 cursor-pointer place-items-center rounded-full hover:bg-white/15" aria-label="Dismiss" onClick={() => setToast(null)}>
            <Icon name="x" size={16} />
          </button>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ the control */

/**
 * Delete control for one record. The Super Admin moves it to the Trash (restorable for 30 days);
 * staff who may edit it can ask the Super Admin to delete it; everyone else sees nothing.
 * Keep it outside any <form>: its dialog buttons would submit that form.
 */
export function DeleteAction({
  kind,
  id,
  label,
  warehouseId,
  onDone,
  compact = false,
  open: openProp,
  onClose,
}: {
  kind: TrashKind;
  id: string;
  /** The record's name, as people know it ("CHK — Chicken", "Order SO-…"). */
  label: string;
  warehouseId?: string | null;
  /** After the record moved to the Trash (refresh the list, or leave the detail page). */
  onDone?: (item: TrashItem) => void;
  /** Icon-only button for table rows and cards. */
  compact?: boolean;
  /** Controlled: no button of its own; the caller opens the dialog (e.g. from a ⋯ menu). */
  open?: boolean;
  onClose?: () => void;
}) {
  const { session } = useAuth();
  const [ownOpen, setOwnOpen] = useState(false);
  const controlled = openProp !== undefined;
  const open = controlled ? openProp : ownOpen;
  const close = () => (controlled ? onClose?.() : setOwnOpen(false));
  if (!session) return null;
  const mode = deleteMode(session, kind, warehouseId);
  if (!mode) return null;
  const text = mode === "trash" ? "Delete" : "Request delete";
  // Rows and cards often open on click/Enter: keep this control's clicks and keys to itself.
  return (
    <span className="contents" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
      {controlled ? null : compact ? (
        <button type="button" className="icon-btn size-9 text-danger" aria-label={`${text} ${label}`} title={text} onClick={() => setOwnOpen(true)}>
          <Icon name="trash" size={16} />
        </button>
      ) : (
        <button type="button" className="btn-ghost text-danger" onClick={() => setOwnOpen(true)}>
          <Icon name="trash" size={16} />
          {text}
        </button>
      )}
      {open && (
        <DeleteDialog
          mode={mode}
          kind={kind}
          id={id}
          label={label}
          ready={twoStepReady(session)}
          onClose={close}
          onTrashed={(item) => {
            close();
            showToast({ kind: "trashed", item });
            notifyTrashChanged();
            onDone?.(item);
          }}
          onInstead={(text) => {
            close();
            showToast({ kind: "done", text });
            notifyTrashChanged(); // lists reload and show it as inactive
          }}
        />
      )}
    </span>
  );
}

function DeleteDialog({
  mode,
  kind,
  id,
  label,
  ready,
  onClose,
  onTrashed,
  onInstead,
}: {
  mode: "trash" | "request";
  kind: TrashKind;
  id: string;
  label: string;
  ready: boolean;
  onClose: () => void;
  onTrashed: (item: TrashItem) => void;
  onInstead: (text: string) => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const reasonId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<{ text: string; lines: string[] } | null>(null);
  const [sent, setSent] = useState(false);
  const [reason, setReason] = useState("");
  // The Super Admin sees what goes with it (or what keeps it) before confirming.
  const wantsPreview = mode === "trash" && ready;
  const [preview, setPreview] = useState<TrashPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);
  useEffect(() => {
    if (!wantsPreview) return;
    let live = true;
    trashApi
      .preview(kind, id)
      .then((p) => live && setPreview(p))
      .catch((e) => live && setPreviewError(trashErrorText(e)));
    return () => {
      live = false;
    };
  }, [wantsPreview, kind, id]);
  const blocked = preview !== null && !preview.deletable;

  async function doInstead() {
    if (busy || !preview?.instead) return;
    setBusy(true);
    setError(null);
    try {
      await trashApi.instead(kind, id);
      onInstead(`“${label}” ${preview.instead === "suspend" ? "suspended" : "deactivated"}.`);
    } catch (err) {
      setError({ text: trashErrorText(err), lines: [] });
    } finally {
      setBusy(false);
    }
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    e.stopPropagation(); // React bubbles through the portal: never submit a page form around us
    if (busy) return;
    if (mode === "trash" && (blocked || (!preview && !previewError))) return;
    setBusy(true);
    setError(null);
    try {
      if (mode === "trash") onTrashed(await trashApi.move(kind, id));
      else {
        await trashApi.request(kind, id, reason.trim());
        setSent(true);
      }
    } catch (err) {
      setError({ text: trashErrorText(err), lines: referenceLines(err) });
    } finally {
      setBusy(false);
    }
  }

  const noun = KIND_LABEL[kind].toLowerCase();
  let title: string;
  let body: ReactNode;
  let action: string | null;
  if (mode === "trash" && !ready) {
    title = "Turn on two-step sign-in";
    body = (
      <p>
        Deleting records needs two-step sign-in. Turn it on in{" "}
        <Link to="/settings" className="font-medium text-brand-primary underline underline-offset-2" onClick={onClose}>
          My account
        </Link>
        , then try again.
      </p>
    );
    action = null;
  } else if (mode === "trash" && blocked) {
    title = `“${label}” can't be deleted`;
    body = (
      <>
        <p>{preview.blocked.length ? "Records that are always kept use it:" : preview.message}</p>
        {preview.blocked.length > 0 && <RefList refs={preview.blocked} testId="delete-blocked" />}
        {preview.hint && <p>{preview.hint}</p>}
      </>
    );
    action = null;
  } else if (mode === "trash") {
    title = `Move “${label}” to Trash?`;
    const goes = preview?.goes_with ?? [];
    const unlinked = preview?.unlinked ?? [];
    body = (
      <>
        <p>
          This {noun} disappears from every list now. You can restore it from Trash for 30 days; after that it is deleted
          forever.
        </p>
        {!preview && !previewError && <p aria-live="polite">Checking what goes with it…</p>}
        {previewError && <p className="text-danger">{previewError}</p>}
        {goes.length > 0 && (
          <>
            <p className="font-medium text-ink">Also moves to the Trash with it:</p>
            <RefList refs={goes} testId="delete-goes-with" />
          </>
        )}
        {unlinked.length > 0 && (
          <>
            <p className="font-medium text-ink">Stays, but no longer linked to it:</p>
            <RefList refs={unlinked} testId="delete-unlinked" />
          </>
        )}
        {(goes.length > 0 || unlinked.length > 0) && <p>Restoring it brings all of this back.</p>}
      </>
    );
    action = busy ? "Moving…" : "Move to Trash";
  } else if (sent) {
    title = "Request sent";
    body = <p>The Super Admin has been asked to delete “{label}”. It stays as it is until they decide.</p>;
    action = null;
  } else {
    title = `Ask to delete “${label}”?`;
    body = (
      <>
        <p>Only the Super Admin can delete records. They will be asked to move this {noun} to the Trash.</p>
        <label htmlFor={reasonId} className="label mt-3">
          Reason (optional)
        </label>
        <textarea id={reasonId} className="input min-h-20 w-full" maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
      </>
    );
    action = busy ? "Sending…" : "Send request";
  }

  // Portal: the control may sit inside a page's <form>, and forms must not nest.
  return createPortal(
    <dialog
      ref={ref}
      onClose={(e) => {
        e.stopPropagation(); // React bubbles "close" through the portal to any dialog around us
        onClose();
      }}
      aria-labelledby={titleId}
      className="m-auto w-[min(32rem,calc(100vw-2rem))] rounded-card border-0 bg-card p-0 text-ink shadow-[0_8px_24px_rgb(24_24_27/0.12)] backdrop:bg-ink/40"
    >
      <form onSubmit={submit} noValidate>
        <div className="space-y-3 p-6">
          <h2 id={titleId} className="text-lg font-semibold">
            {title}
          </h2>
          <div className="space-y-2 text-sm text-ink-muted">{body}</div>
          {error && (
            <div role="alert" className="alert-danger text-sm">
              <p>{error.text}</p>
              {error.lines.length > 0 && (
                <ul className="mt-1 list-disc pl-5">
                  {error.lines.map((l) => (
                    <li key={l}>{l}</li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
        <div className="flex justify-end gap-2 border-t border-line px-6 py-4">
          <button type="button" className="btn-ghost" onClick={onClose} autoFocus>
            {action || (blocked && preview.instead) ? "Cancel" : "Close"}
          </button>
          {blocked && preview.instead && (
            <button type="button" className="btn-primary" disabled={busy} onClick={() => void doInstead()}>
              {busy ? "Saving…" : insteadLabel(preview.instead, kind)}
            </button>
          )}
          {action && (
            <button
              type="submit"
              className="btn bg-danger text-white hover:opacity-90"
              disabled={busy || (wantsPreview && !preview && !previewError)}
            >
              {action}
            </button>
          )}
        </div>
      </form>
    </dialog>,
    document.body,
  );
}

function RefList({ refs, testId }: { refs: Reference[]; testId: string }) {
  return (
    <ul className="list-disc space-y-0.5 pl-5 text-ink" data-testid={testId}>
      {refs.map((r) => (
        <li key={r.table}>{referenceLine(r)}</li>
      ))}
    </ul>
  );
}
