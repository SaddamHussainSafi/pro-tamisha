import { useCallback, useEffect, useState, type FormEvent } from "react";
import { api, ApiError, canInWarehouse, hasCompanyWide, type Warehouse } from "../api";
import { useAuth } from "../auth";

function message(err: unknown): string {
  if (!(err instanceof ApiError)) return "Something went wrong. Try again.";
  switch (err.code) {
    case "VERSION_CONFLICT":
      return "Someone else changed this warehouse. The list has been reloaded — review and retry.";
    case "DUPLICATE":
      return "A warehouse with that code or name already exists.";
    case "FORBIDDEN":
      return "You don't have permission for this action.";
    case "MFA_REQUIRED":
      return "Verify two-step authentication to perform this action.";
    case "VALIDATION":
      return "Check the highlighted fields. Codes use A–Z, 0–9, - and _.";
    default:
      return err.message;
  }
}

export function WarehousesPage() {
  const { session } = useAuth();
  const [rows, setRows] = useState<Warehouse[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<Warehouse | null>(null);

  const load = useCallback(async () => {
    try {
      setRows(await api.warehouses());
      setLoadError(false);
    } catch {
      setLoadError(true);
    }
  }, []);
  useEffect(() => void load(), [load]);

  if (!session) return null;
  const canCreate = hasCompanyWide(session, "warehouse.manage");

  async function create(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api.createWarehouse({ code: code.trim().toUpperCase(), name: name.trim() });
      setCode("");
      setName("");
      await load();
    } catch (err) {
      setError(message(err));
    }
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!editing) return;
    setError(null);
    try {
      await api.updateWarehouse(editing.id, { name: editing.name, active: editing.active, version: editing.version });
      setEditing(null);
      await load();
    } catch (err) {
      setError(message(err));
      if (err instanceof ApiError && err.code === "VERSION_CONFLICT") {
        setEditing(null);
        await load();
      }
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Settings · Warehouses</h1>
        <p className="text-sm text-ink-muted">Warehouses visible to your role.</p>
      </div>
      {error && <div role="alert" className="alert-danger">{error}</div>}

      {canCreate && (
        <form onSubmit={create} className="card flex flex-wrap items-end gap-3 p-4" aria-label="Add warehouse">
          <div className="w-40">
            <label className="label" htmlFor="wh-code">Code</label>
            <input id="wh-code" className="input uppercase" required maxLength={32} pattern="[A-Za-z0-9_\-]+"
              value={code} onChange={(e) => setCode(e.target.value)} />
          </div>
          <div className="min-w-60 flex-1">
            <label className="label" htmlFor="wh-name">Name</label>
            <input id="wh-name" className="input" required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <button className="btn-primary" type="submit">Add warehouse</button>
        </form>
      )}

      <div className="card overflow-hidden">
        {loadError ? (
          <div className="p-6 text-sm">
            <p className="text-danger">Couldn't load warehouses.</p>
            <button className="btn-ghost mt-3" onClick={() => void load()}>Retry</button>
          </div>
        ) : rows === null ? (
          <p className="p-6 text-sm text-ink-muted">Loading…</p>
        ) : rows.length === 0 ? (
          <p className="p-6 text-sm text-ink-muted">No warehouses yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead className="sticky top-0 bg-canvas text-left text-xs uppercase tracking-wide text-ink-muted">
              <tr>
                <th className="px-4 py-2.5">Code</th>
                <th className="px-4 py-2.5">Name</th>
                <th className="px-4 py-2.5">Status</th>
                <th className="px-4 py-2.5 text-right">Version</th>
                <th className="px-4 py-2.5" />
              </tr>
            </thead>
            <tbody>
              {rows.map((w) =>
                editing?.id === w.id ? (
                  <tr key={w.id} className="border-t border-line bg-canvas/60">
                    <td className="px-4 py-2 font-medium">{w.code}</td>
                    <td className="px-4 py-2" colSpan={3}>
                      <form id="edit-wh" onSubmit={save} className="flex items-center gap-3">
                        <input aria-label="Warehouse name" className="input" required value={editing.name}
                          onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
                        <label className="flex items-center gap-2 whitespace-nowrap">
                          <input type="checkbox" checked={editing.active}
                            onChange={(e) => setEditing({ ...editing, active: e.target.checked })} />
                          Active
                        </label>
                      </form>
                    </td>
                    <td className="space-x-2 px-4 py-2 text-right whitespace-nowrap">
                      <button className="btn-primary" form="edit-wh" type="submit">Save</button>
                      <button className="btn-ghost" type="button" onClick={() => setEditing(null)}>Cancel</button>
                    </td>
                  </tr>
                ) : (
                  <tr key={w.id} className="border-t border-line">
                    <td className="px-4 py-2.5 font-medium">{w.code}</td>
                    <td className="px-4 py-2.5">{w.name}</td>
                    <td className="px-4 py-2.5">
                      <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${w.active ? "text-success" : "text-ink-muted"}`}>
                        <span aria-hidden className={`h-2 w-2 rounded-full ${w.active ? "bg-success" : "bg-brand-gray"}`} />
                        {w.active ? "Active" : "Inactive"}
                      </span>
                    </td>
                    <td className="num px-4 py-2.5 text-right">{w.version}</td>
                    <td className="px-4 py-2.5 text-right">
                      {canInWarehouse(session, "warehouse.manage", w.id) && (
                        <button className="btn-ghost" onClick={() => setEditing(w)}>Edit</button>
                      )}
                    </td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
