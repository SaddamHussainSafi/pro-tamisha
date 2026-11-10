import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../auth";
import {
  canManageTrash,
  daysLeftText,
  KIND_LABEL,
  recordLink,
  referenceLines,
  TRASH_DAYS,
  trashApi,
  trashErrorText,
  twoStepReady,
  useTrashChanges,
  type DeleteRequest,
  type TrashItem,
} from "../lib/trash";
import { DataGrid, type Column } from "../ui/DataGrid";
import { Icon } from "../ui/Icon";
import { ConfirmDialog, EmptyState, ErrorState, FilterBar, LoadingRows, PageHeader } from "../ui/primitives";

type Tab = "trash" | "requests";
type Msg = { tone: "ok" | "error"; text: string; lines?: string[]; to?: string } | null;
type Ask =
  | { kind: "purge"; item: TrashItem }
  | { kind: "empty" }
  | { kind: "approve"; req: DeleteRequest }
  | { kind: "dismiss"; req: DeleteRequest }
  | null;

const when = (iso: string) => new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
const match = (q: string, ...xs: (string | null)[]) => !q || xs.some((x) => x?.toLowerCase().includes(q));

/** Trash: deleted records wait here for 30 days (Super Admin only), plus staff delete requests. */
export function TrashPage() {
  const { session } = useAuth();
  const [params, setParams] = useSearchParams();
  const tab: Tab = params.get("tab") === "requests" ? "requests" : "trash";
  const [items, setItems] = useState<TrashItem[] | null>(null);
  const [reqs, setReqs] = useState<DeleteRequest[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [search, setSearch] = useState("");
  const [msg, setMsg] = useState<Msg>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [ask, setAsk] = useState<Ask>(null);

  const allowed = !!session && canManageTrash(session);
  const ready = !!session && twoStepReady(session);

  const load = useCallback(async () => {
    if (!allowed) return;
    try {
      const [t, r] = await Promise.all([trashApi.list(), trashApi.requests()]);
      setItems(t);
      setReqs(r);
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, [allowed]);
  useEffect(() => void load(), [load]);
  useTrashChanges(load);

  const q = search.trim().toLowerCase();
  const shownItems = useMemo(() => (items ?? []).filter((t) => match(q, t.label, KIND_LABEL[t.kind], t.trashed_by)), [items, q]);
  const shownReqs = useMemo(() => (reqs ?? []).filter((r) => match(q, r.label, KIND_LABEL[r.kind], r.requested_by, r.reason)), [reqs, q]);

  if (!session) return null;
  if (!allowed)
    return (
      <div className="card">
        <EmptyState icon="trash" title="Trash is for the Super Admin" hint="Ask your Super Admin if something needs to be deleted." />
      </div>
    );

  async function run(id: string, fn: () => Promise<Msg>) {
    setBusy(id);
    setMsg(null);
    try {
      setMsg(await fn());
    } catch (e) {
      setMsg({ tone: "error", text: trashErrorText(e), lines: referenceLines(e) });
    } finally {
      setBusy(null);
      void load();
    }
  }

  const restore = (t: TrashItem) =>
    run(t.id, async () => {
      await trashApi.restore(t.id);
      return { tone: "ok", text: `“${t.label}” is back.`, to: recordLink(t.kind, t.item_id) };
    });

  function confirm() {
    const a = ask;
    setAsk(null);
    if (!a) return;
    if (a.kind === "purge")
      void run(a.item.id, async () => {
        await trashApi.purge(a.item.id);
        return { tone: "ok", text: `“${a.item.label}” was deleted forever.` };
      });
    if (a.kind === "empty")
      void run("empty", async () => {
        const r = await trashApi.empty();
        return { tone: "ok", text: r.purged === 1 ? "1 item was deleted forever." : `${r.purged} items were deleted forever.` };
      });
    if (a.kind === "approve")
      void run(a.req.id, async () => {
        await trashApi.approve(a.req.id);
        return { tone: "ok", text: `“${a.req.label}” moved to Trash.` };
      });
    if (a.kind === "dismiss")
      void run(a.req.id, async () => {
        await trashApi.dismiss(a.req.id);
        return { tone: "ok", text: `Request to delete “${a.req.label}” dismissed. Nothing was deleted.` };
      });
  }

  const nameCell = (label: string, kind: TrashItem["kind"]) => (
    <span className="flex min-w-0 flex-col">
      <span className="truncate font-medium">{label}</span>
      <span className="text-sm text-ink-muted">{KIND_LABEL[kind]}</span>
    </span>
  );

  const trashCols: Column<TrashItem>[] = [
    { key: "name", header: "Name", cell: (t) => nameCell(t.label, t.kind), sort: (t) => t.label.toLowerCase() },
    { key: "by", header: "Deleted by", cell: (t) => t.trashed_by ?? "—", sort: (t) => t.trashed_by ?? "" },
    { key: "at", header: "Deleted", cell: (t) => when(t.trashed_at), sort: (t) => t.trashed_at },
    { key: "left", header: "Deleted forever in", cell: (t) => <span className={t.days_left <= 3 ? "font-medium text-danger" : ""}>{daysLeftText(t.days_left)}</span>, sort: (t) => t.purge_after },
    {
      key: "actions",
      header: "Actions",
      align: "right",
      cell: (t) => (
        <span className="flex justify-end gap-2">
          <button type="button" className="btn-ghost" disabled={!ready || busy === t.id} onClick={() => void restore(t)} aria-label={`Restore ${t.label}`}>
            <Icon name="restore" size={16} />
            Restore
          </button>
          <button type="button" className="btn-ghost text-danger" disabled={!ready || busy === t.id} onClick={() => setAsk({ kind: "purge", item: t })} aria-label={`Delete ${t.label} forever`}>
            Delete forever
          </button>
        </span>
      ),
    },
  ];

  const reqCols: Column<DeleteRequest>[] = [
    { key: "name", header: "Name", cell: (r) => nameCell(r.label, r.kind), sort: (r) => r.label.toLowerCase() },
    { key: "by", header: "Asked by", cell: (r) => r.requested_by ?? "—", sort: (r) => r.requested_by ?? "" },
    { key: "reason", header: "Reason", cell: (r) => r.reason || <span className="text-ink-muted">No reason given</span> },
    { key: "at", header: "Asked", cell: (r) => when(r.created_at), sort: (r) => r.created_at },
    {
      key: "actions",
      header: "Actions",
      align: "right",
      cell: (r) => (
        <span className="flex justify-end gap-2">
          <Link className="btn-ghost" to={recordLink(r.kind, r.item_id)} aria-label={`Open ${r.label}`}>
            Open
          </Link>
          <button type="button" className="btn-ghost text-danger" disabled={!ready || busy === r.id} onClick={() => setAsk({ kind: "approve", req: r })} aria-label={`Move ${r.label} to Trash`}>
            Move to Trash
          </button>
          <button type="button" className="btn-ghost" disabled={!ready || busy === r.id} onClick={() => setAsk({ kind: "dismiss", req: r })} aria-label={`Dismiss request for ${r.label}`}>
            Dismiss
          </button>
        </span>
      ),
    },
  ];

  const dialog = (() => {
    if (!ask) return { title: "", body: "", label: "", danger: false };
    if (ask.kind === "purge")
      return { title: `Delete “${ask.item.label}” forever?`, body: "It can't be restored after this.", label: "Delete forever", danger: true };
    if (ask.kind === "empty") {
      const n = items?.length ?? 0;
      return { title: "Empty the Trash?", body: `${n === 1 ? "1 item is" : `${n} items are`} deleted forever. This can't be undone.`, label: "Empty Trash", danger: true };
    }
    if (ask.kind === "approve")
      return {
        title: `Move “${ask.req.label}” to Trash?`,
        body: `${ask.req.requested_by ?? "Someone"} asked for this. You can restore it from Trash for ${TRASH_DAYS} days.`,
        label: "Move to Trash",
        danger: true,
      };
    return { title: `Dismiss the request for “${ask.req.label}”?`, body: "The record stays as it is.", label: "Dismiss", danger: false };
  })();

  return (
    <div className="space-y-5">
      <PageHeader
        title="Trash"
        subtitle={`Deleted records wait here for ${TRASH_DAYS} days, then they are deleted forever. Restore puts one back where it was.`}
        actions={
          tab === "trash" && (
            <button type="button" className="btn-ghost text-danger" disabled={!ready || !items?.length || busy === "empty"} onClick={() => setAsk({ kind: "empty" })}>
              <Icon name="trash" size={16} />
              Empty Trash
            </button>
          )
        }
      />

      {!ready && (
        <p className="alert-warning">
          Restoring and deleting need two-step sign-in. Turn it on in{" "}
          <Link to="/settings" className="font-medium underline underline-offset-2">
            My account
          </Link>
          .
        </p>
      )}
      {msg && (
        <div role={msg.tone === "error" ? "alert" : undefined} aria-live="polite" className={msg.tone === "error" ? "alert-danger" : "alert-success"}>
          <p>
            {msg.text}{" "}
            {msg.to && (
              <Link to={msg.to} className="font-medium underline underline-offset-2">
                Open it
              </Link>
            )}
          </p>
          {msg.lines && msg.lines.length > 0 && (
            <ul className="mt-1 list-disc pl-5">
              {msg.lines.map((l) => (
                <li key={l}>{l}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <FilterBar
        search={search}
        onSearch={setSearch}
        options={[
          { value: "trash" as Tab, label: "In Trash", count: items?.length },
          { value: "requests" as Tab, label: "Delete requests", count: reqs?.length },
        ]}
        value={tab}
        onChange={(t) => setParams(t === "requests" ? { tab: "requests" } : {}, { replace: true })}
        placeholder="Search the Trash"
      />

      {failed ? (
        <div className="card">
          <ErrorState title="Couldn't load the Trash" onRetry={() => void load()} />
        </div>
      ) : items === null || reqs === null ? (
        <div className="card overflow-clip">
          <LoadingRows cols={5} />
        </div>
      ) : tab === "trash" ? (
        <div className="card overflow-clip">
          <DataGrid
            rows={shownItems}
            columns={trashCols}
            rowKey={(t) => t.id}
            caption="Items in the Trash"
            empty={<EmptyState icon="trash" title={q ? "Nothing in the Trash matches" : "Trash is empty"} hint={q ? undefined : "Deleted records show up here for 30 days."} />}
          />
        </div>
      ) : (
        <div className="card overflow-clip">
          <DataGrid
            rows={shownReqs}
            columns={reqCols}
            rowKey={(r) => r.id}
            caption="Delete requests"
            empty={<EmptyState icon="inbox" title="No delete requests" hint="When staff ask for something to be deleted, it waits here for you." />}
          />
        </div>
      )}

      <ConfirmDialog open={!!ask} title={dialog.title} confirmLabel={dialog.label} danger={dialog.danger} onConfirm={confirm} onClose={() => setAsk(null)}>
        {dialog.body}
      </ConfirmDialog>
    </div>
  );
}
