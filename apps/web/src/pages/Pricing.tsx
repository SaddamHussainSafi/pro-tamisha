import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  api,
  ApiError,
  hasCompanyWide,
  priceCsvUrl,
  type Customer,
  type CustomerGroup,
  type PriceDiff,
  type PriceEntry,
  type PricePreview,
  type PriceQuote,
  type PriceScope,
  type PriceState,
  type PriceVersion,
  type ProductSummary,
  type UnitCode,
} from "../api";
import { useAuth } from "../auth";
import { dec } from "../lib/decimal";
import { pricingMessage, rateText, scopeLabel } from "../lib/pricingErrors";
import { useTrashChanges } from "../lib/trash";
import { DataGrid, type Column } from "../ui/DataGrid";
import { DeleteAction } from "../ui/DeleteAction";
import { NumericInput } from "../ui/NumericInput";
import { ConfirmDialog, EmptyState, ErrorState, FilterBar, LoadingRows, PageHeader, PageSkeleton, StatusChip, type Tone } from "../ui/primitives";

const UNITS: UnitCode[] = ["LB", "KG", "BOX", "PIECE"];
const UNIT_LABEL: Record<UnitCode, string> = { LB: "per lb", KG: "per kg", BOX: "per box", PIECE: "per piece" };


const STATE: Record<PriceState, { tone: Tone; label: string }> = {
  DRAFT: { tone: "info", label: "Draft" },
  SCHEDULED: { tone: "warning", label: "Scheduled" },
  CURRENT: { tone: "success", label: "Current" },
  SUPERSEDED: { tone: "neutral", label: "Superseded" },
  ARCHIVED: { tone: "neutral", label: "Cancelled" },
};
const StateChip = ({ s }: { s: PriceState }) => <StatusChip tone={STATE[s].tone}>{STATE[s].label}</StatusChip>;
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : "—");

/* ============================== Versions list ============================== */

type StateFilter = "all" | "DRAFT" | "ACTIVE" | "ARCHIVED";

export function PricingPage() {
  const { session } = useAuth();
  const nav = useNavigate();
  const [filter, setFilter] = useState<StateFilter>("all");
  const [search, setSearch] = useState("");
  const [rows, setRows] = useState<PriceVersion[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [panel, setPanel] = useState<"none" | "import" | "draft">("none");

  const load = useCallback(async () => {
    try {
      setRows(await api.priceVersions(filter === "all" ? undefined : filter));
      setLoadError(false);
    } catch {
      setLoadError(true);
    }
  }, [filter]);
  useEffect(() => void load(), [load]);
  useTrashChanges(load);

  if (!session) return null;
  const canDraft = hasCompanyWide(session, "price.draft");
  const shown = (rows ?? []).filter((v) => v.version_code.toLowerCase().includes(search.trim().toLowerCase()));

  const columns: Column<PriceVersion>[] = [
    {
      key: "code",
      header: "Version",
      cell: (r) => <Link className="font-medium text-brand-primary-hover underline-offset-2 hover:underline" to={`/products/prices/${r.id}`}>{r.version_code}</Link>,
      sort: (r) => r.version_code.toLowerCase(),
    },
    { key: "state", header: "Status", cell: (r) => <StateChip s={r.state} /> },
    { key: "eff", header: "Effective from", cell: (r) => when(r.effective_at), sort: (r) => r.effective_at ?? "" },
    { key: "n", header: "Prices", cell: (r) => <span className="num">{r.entry_count}</span>, align: "right", sort: (r) => BigInt(r.entry_count) },
    { key: "src", header: "Source", cell: (r) => (r.source_hash ? "CSV upload" : "Edited") },
    { key: "notes", header: "Notes", cell: (r) => <span className="text-ink-muted">{r.notes || "—"}</span> },
    { key: "delete", header: "", align: "right", cell: (r) => (r.status === "DRAFT" ? <DeleteAction kind="price_draft" id={r.id} label={`Price book ${r.version_code}`} compact /> : null) },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title="Prices"
        subtitle="Product prices, as versioned price books. Published prices never change; issued invoices keep their snapshot."
        actions={
          <div className="flex flex-wrap gap-2">
            <a className="btn-ghost" href={priceCsvUrl()} download>Download template</a>
            {canDraft && <button className="btn-ghost" onClick={() => setPanel("draft")}>New draft</button>}
            {canDraft && <button className="btn-primary" onClick={() => setPanel("import")}>Import CSV</button>}
          </div>
        }
      />
      {panel === "import" && <ImportPanel onCancel={() => setPanel("none")} onCreated={(v) => nav(`/products/prices/${v.id}`)} />}
      {panel === "draft" && <NewDraft onCancel={() => setPanel("none")} onCreated={(v) => nav(`/products/prices/${v.id}`)} />}
      <FilterBar
        search={search}
        onSearch={setSearch}
        placeholder="Search version code"
        options={[
          { value: "all", label: "All" },
          { value: "DRAFT", label: "Drafts" },
          { value: "ACTIVE", label: "Published" },
          { value: "ARCHIVED", label: "Cancelled" },
        ]}
        value={filter}
        onChange={setFilter}
      />
      <div className="card overflow-hidden">
        {loadError ? (
          <ErrorState title="Couldn't load price books" onRetry={() => void load()} />
        ) : rows === null ? (
          <LoadingRows cols={6} />
        ) : (
          <DataGrid
            rows={shown}
            columns={columns}
            rowKey={(r) => r.id}
            caption="Price book versions"
            onOpen={(r) => nav(`/products/prices/${r.id}`)}
            empty={<EmptyState title="No price books yet" hint="Import a CSV or start a new draft." />}
          />
        )}
      </div>
      <PriceLookup />
    </div>
  );
}

function NewDraft({ onCancel, onCreated }: { onCancel: () => void; onCreated: (v: PriceVersion) => void }) {
  const [code, setCode] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onCreated(await api.createPriceVersion({ version_code: code.trim(), notes }));
    } catch (err) {
      setError(pricingMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="card space-y-3 p-6" onSubmit={submit} aria-labelledby="nd-h">
      <h2 id="nd-h" className="text-base font-semibold">New draft</h2>
      <p className="text-sm text-ink-muted">Starts as a copy of the current price book. Edit rates, then activate.</p>
      {error && <div role="alert" className="alert-danger">{error}</div>}
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="label" htmlFor="nd-code">Version code</label>
          <input id="nd-code" className="input" required pattern="[A-Za-z0-9._\-]+" value={code} onChange={(e) => setCode(e.target.value)} placeholder="PB-2026-02" />
        </div>
        <div>
          <label className="label" htmlFor="nd-notes">Notes</label>
          <input id="nd-notes" className="input" value={notes} onChange={(e) => setNotes(e.target.value)} />
        </div>
      </div>
      <div className="flex justify-end gap-2">
        <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
        <button className="btn-primary" disabled={busy}>Create draft</button>
      </div>
    </form>
  );
}

/* ============================== CSV import ============================== */

function ImportPanel({ onCancel, onCreated }: { onCancel: () => void; onCreated: (v: PriceVersion) => void }) {
  const [csv, setCsv] = useState("");
  const [fileName, setFileName] = useState("");
  const [code, setCode] = useState("");
  const [notes, setNotes] = useState("");
  const [preview, setPreview] = useState<PricePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rowErrors, setRowErrors] = useState<PricePreview["errors"]>([]);
  const [busy, setBusy] = useState(false);

  async function onFile(f: File | undefined) {
    if (!f) return;
    setFileName(f.name);
    setCsv(await f.text());
    setPreview(null);
  }
  async function runPreview() {
    setBusy(true);
    setError(null);
    try {
      const p = await api.previewPriceCsv(csv);
      setPreview(p);
      setRowErrors(p.errors);
    } catch (err) {
      setError(pricingMessage(err));
    } finally {
      setBusy(false);
    }
  }
  async function create(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onCreated(await api.importPriceCsv({ version_code: code.trim(), notes, csv }));
    } catch (err) {
      setError(pricingMessage(err));
      if (err instanceof ApiError && Array.isArray(err.extra.errors)) setRowErrors(err.extra.errors as PricePreview["errors"]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card space-y-4 p-6" onSubmit={create} aria-labelledby="imp-h">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id="imp-h" className="text-base font-semibold">Import price list (CSV)</h2>
        <a className="text-sm text-brand-primary-hover underline" href={priceCsvUrl()} download>Template</a>
      </div>
      <p className="text-sm text-ink-muted">
        The file is the <em>complete</em> new price book. Columns: <code>sku, unit, rate</code> (required) and
        optional <code>product_name, currency, customer_ref, group_code</code>. Nothing is saved until you create the draft.
      </p>
      <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
        <div>
          <label className="label" htmlFor="imp-file">CSV file</label>
          <input id="imp-file" type="file" accept=".csv,text/csv" className="input" onChange={(e) => void onFile(e.target.files?.[0])} />
          {fileName && <p className="mt-1 text-xs text-ink-muted">{fileName} · {csv.split("\n").filter((l) => l.trim()).length - 1} rows</p>}
        </div>
        <div className="self-end">
          <button type="button" className="btn-ghost" disabled={!csv || busy} onClick={() => void runPreview()}>Check file</button>
        </div>
      </div>
      <details>
        <summary className="cursor-pointer text-sm text-ink-muted">Or paste CSV text</summary>
        <label className="sr-only" htmlFor="imp-text">CSV text</label>
        <textarea id="imp-text" className="input mt-2 h-32 font-mono text-xs" value={csv} onChange={(e) => { setCsv(e.target.value); setPreview(null); }} />
      </details>
      {error && <div role="alert" className="alert-danger">{error}</div>}
      {rowErrors.length > 0 && <RowErrors errors={rowErrors} />}
      {preview?.valid && (
        <div className="space-y-3" aria-live="polite">
          {preview.duplicate_of ? (
            <div role="alert" className="alert-warning">
              This exact price list was already uploaded. <Link className="underline" to={`/products/prices/${preview.duplicate_of}`}>Open the existing version</Link>.
            </div>
          ) : (
            <div className="alert-success" role="status">File is valid: {preview.entry_count} prices.</div>
          )}
          {preview.diff && <DiffView diff={preview.diff} title="Changes compared with the current price book" />}
          {!preview.duplicate_of && (
            <div className="grid gap-3 sm:grid-cols-2">
              <div>
                <label className="label" htmlFor="imp-code">Version code</label>
                <input id="imp-code" className="input" required pattern="[A-Za-z0-9._\-]+" value={code} onChange={(e) => setCode(e.target.value)} placeholder="PB-2026-02" />
              </div>
              <div>
                <label className="label" htmlFor="imp-notes">Notes</label>
                <input id="imp-notes" className="input" value={notes} onChange={(e) => setNotes(e.target.value)} />
              </div>
            </div>
          )}
        </div>
      )}
      <div className="flex justify-end gap-2">
        <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
        <button className="btn-primary" disabled={!preview?.valid || !!preview.duplicate_of || busy}>Create draft</button>
      </div>
    </form>
  );
}

function RowErrors({ errors }: { errors: PricePreview["errors"] }) {
  return (
    <div className="overflow-auto rounded border border-line" role="region" aria-label="Row errors" tabIndex={0}>
      <table className="w-full text-sm">
        <caption className="sr-only">Rows with problems</caption>
        <thead className="bg-canvas text-left">
          <tr><th className="px-3 py-1.5">Line</th><th className="px-3 py-1.5">Column</th><th className="px-3 py-1.5">Problem</th></tr>
        </thead>
        <tbody>
          {errors.map((e, i) => (
            <tr key={i} className="border-t border-line">
              <td className="num px-3 py-1.5">{e.row || "—"}</td>
              <td className="px-3 py-1.5">{e.field ?? "—"}</td>
              <td className="px-3 py-1.5"><span className="font-medium">{e.code}</span> · {e.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const CHANGE_TONE: Record<string, Tone> = { ADDED: "success", CHANGED: "warning", REMOVED: "danger" };

function DiffView({ diff, title }: { diff: PriceDiff; title: string }) {
  return (
    <section aria-label={title} className="space-y-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      <p className="text-sm" data-testid="diff-summary">
        <span className="num">{diff.changed}</span> changed · <span className="num">{diff.added}</span> added ·{" "}
        <span className="num">{diff.removed}</span> removed · <span className="num">{diff.unchanged}</span> unchanged
      </p>
      {diff.rows.length > 0 && (
        <div className="max-h-72 overflow-auto rounded border border-line" role="region" aria-label="Changed prices" tabIndex={0}>
          <table className="w-full text-sm">
            <caption className="sr-only">Changed prices</caption>
            <thead className="sticky top-0 bg-canvas text-left">
              <tr>
                <th className="px-3 py-1.5">Product</th>
                <th className="px-3 py-1.5">Unit</th>
                <th className="px-3 py-1.5">Applies to</th>
                <th className="px-3 py-1.5 text-right">Old</th>
                <th className="px-3 py-1.5 text-right">New</th>
                <th className="px-3 py-1.5">Change</th>
              </tr>
            </thead>
            <tbody>
              {diff.rows.map((r) => (
                <tr key={`${r.product_id}-${r.unit_code}-${r.scope}-${r.target ?? ""}`} className="border-t border-line">
                  <td className="px-3 py-1.5">{r.product_name} <span className="text-ink-muted">{r.sku}</span></td>
                  <td className="px-3 py-1.5">{UNIT_LABEL[r.unit_code]}</td>
                  <td className="px-3 py-1.5">{scopeLabel(r.scope, r.target)}</td>
                  <td className="num px-3 py-1.5 text-right">{r.old_rate ? rateText(r.old_rate) : "—"}</td>
                  <td className="num px-3 py-1.5 text-right">{r.new_rate ? rateText(r.new_rate) : "—"}</td>
                  <td className="px-3 py-1.5"><StatusChip tone={CHANGE_TONE[r.change] ?? "neutral"}>{r.change.toLowerCase()}</StatusChip></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {diff.truncated && <p className="text-xs text-ink-muted">Showing the first {diff.rows.length} changes.</p>}
    </section>
  );
}

/* ============================== Lookup ============================== */

function useMasterData() {
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [groups, setGroups] = useState<CustomerGroup[]>([]);
  useEffect(() => {
    api.products({ active: true, limit: 1000 }).then((p) => setProducts(p.items), () => setProducts([]));
    api.customers({ limit: 500 }).then(setCustomers, () => setCustomers([]));
    api.customerGroups().then(setGroups, () => setGroups([]));
  }, []);
  return { products, customers, groups };
}

function PriceLookup() {
  const { products, customers } = useMasterData();
  const [pid, setPid] = useState("");
  const [unit, setUnit] = useState<UnitCode>("LB");
  const [cid, setCid] = useState("");
  const [quote, setQuote] = useState<PriceQuote | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function look(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setQuote(null);
    try {
      setQuote(await api.resolvePrice({ product_id: pid, unit_code: unit, customer_id: cid || undefined }));
    } catch (err) {
      setError(pricingMessage(err));
    }
  }
  return (
    <section className="card p-6" aria-labelledby="look-h">
      <h2 id="look-h" className="text-base font-semibold">Price check</h2>
      <p className="mb-3 text-sm text-ink-muted">Customer price beats group price beats standard price.</p>
      <form className="grid items-end gap-3 sm:grid-cols-[2fr_1fr_2fr_auto]" onSubmit={look}>
        <div>
          <label className="label" htmlFor="lk-prod">Product</label>
          <select id="lk-prod" className="input" required value={pid} onChange={(e) => setPid(e.target.value)}>
            <option value="">Choose…</option>
            {products.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.sku})</option>)}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="lk-unit">Unit</label>
          <select id="lk-unit" className="input" value={unit} onChange={(e) => setUnit(e.target.value as UnitCode)}>
            {UNITS.map((u) => <option key={u} value={u}>{UNIT_LABEL[u]}</option>)}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="lk-cust">Customer</label>
          <select id="lk-cust" className="input" value={cid} onChange={(e) => setCid(e.target.value)}>
            <option value="">No customer (standard)</option>
            {customers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
        <button className="btn-primary">Check price</button>
      </form>
      <div aria-live="polite" className="mt-3">
        {error && <div role="alert" className="alert-warning">{error}</div>}
        {quote && (
          <p className="text-sm" data-testid="quote">
            <span className="num text-lg font-semibold">{rateText(quote.rate)}</span> {UNIT_LABEL[quote.unit_code]} ·{" "}
            <StatusChip tone="info">{scopeLabel(quote.scope, null).split(" ·")[0]} price</StatusChip> · from {quote.version_code}
          </p>
        )}
      </div>
    </section>
  );
}

/* ============================== Version detail ============================== */

type ScopeFilter = "all" | PriceScope;

export function PriceVersionPage() {
  const { id = "" } = useParams();
  const { session } = useAuth();
  const nav = useNavigate();
  const [v, setV] = useState<PriceVersion | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [entries, setEntries] = useState<PriceEntry[] | null>(null);
  const [total, setTotal] = useState(0);
  const [next, setNext] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [scope, setScope] = useState<ScopeFilter>("all");
  const [unit, setUnit] = useState("");
  const [diff, setDiff] = useState<PriceDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [dialog, setDialog] = useState<"none" | "activate" | "discard" | "cancel">("none");
  const [effective, setEffective] = useState("");

  const loadVersion = useCallback(async () => {
    try {
      const got = await api.priceVersion(id);
      setV(got);
      api.priceDiff(id).then(setDiff, () => setDiff(null));
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setNotFound(true);
      else setError(pricingMessage(err));
    }
  }, [id]);

  const loadEntries = useCallback(
    async (cursor?: string) => {
      try {
        const page = await api.priceEntries(id, {
          q: search.trim() || undefined,
          scope: scope === "all" ? undefined : scope,
          unit_code: unit || undefined,
          cursor,
        });
        setEntries((prev) => (cursor && prev ? [...prev, ...page.items] : page.items));
        setNext(page.next_cursor);
        setTotal(page.total);
      } catch (err) {
        setError(pricingMessage(err));
      }
    },
    [id, search, scope, unit],
  );

  useEffect(() => void loadVersion(), [loadVersion]);
  useEffect(() => {
    const t = setTimeout(() => void loadEntries(), 200);
    return () => clearTimeout(t);
  }, [loadEntries]);

  async function run(fn: () => Promise<PriceVersion | undefined>) {
    setError(null);
    setConflict(false);
    try {
      const out = await fn();
      if (out) setV(out);
      await loadEntries();
      api.priceDiff(id).then(setDiff, () => setDiff(null));
      return true;
    } catch (err) {
      setError(pricingMessage(err));
      if (err instanceof ApiError && err.status === 409) {
        setConflict(true);
        await loadVersion();
        await loadEntries();
      }
      return false;
    }
  }

  if (!session) return null;
  if (notFound) return <ErrorState title="Price book not found" detail="It may have been discarded." />;
  if (!v) return <PageSkeleton />;
  const canDraft = hasCompanyWide(session, "price.draft");
  const canApprove = hasCompanyWide(session, "price.approve");
  const editable = v.status === "DRAFT" && canDraft;

  const columns: Column<PriceEntry>[] = [
    { key: "name", header: "Product", cell: (r) => r.product_name, sort: (r) => r.product_name.toLowerCase() },
    { key: "sku", header: "SKU", cell: (r) => <span className="text-ink-muted">{r.sku}</span>, sort: (r) => r.sku },
    { key: "unit", header: "Unit", cell: (r) => UNIT_LABEL[r.unit_code] },
    { key: "scope", header: "Applies to", cell: (r) => scopeLabel(r.scope, r.target), sort: (r) => r.scope },
    { key: "rate", header: "Rate", cell: (r) => <span className="num">{rateText(r.rate)}</span>, align: "right", sort: (r) => dec(r.rate) },
  ];
  if (editable)
    columns.push({
      key: "rm",
      header: "Actions",
      cell: (r) => (
        <button
          className="btn-ghost px-2 py-0.5 text-xs"
          aria-label={`Remove ${r.product_name} ${UNIT_LABEL[r.unit_code]} ${scopeLabel(r.scope, r.target)}`}
          onClick={(e) => {
            e.stopPropagation();
            void run(() => api.removePrice(v.id, r.id, v.version));
          }}
        >
          Remove
        </button>
      ),
    });

  return (
    <div className="space-y-5">
      <nav aria-label="Breadcrumb" className="text-sm"><Link className="text-brand-primary-hover underline" to="/products">Products</Link> / <Link className="text-brand-primary-hover underline" to="/products/prices">Prices</Link> / {v.version_code}</nav>
      <PageHeader
        title={v.version_code}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <StateChip s={v.state} /> {v.entry_count} prices · effective {when(v.effective_at)}
            {v.notes && <span className="text-ink-muted">· {v.notes}</span>}
          </span>
        }
        actions={
          <div className="flex flex-wrap gap-2">
            <a className="btn-ghost" href={priceCsvUrl(v.id)} download>Export CSV</a>
            {v.status === "DRAFT" && <DeleteAction kind="price_draft" id={v.id} label={`Price book ${v.version_code}`} onDone={() => nav("/products/prices")} />}
            {v.status === "DRAFT" && canDraft && <button className="btn-ghost" onClick={() => setDialog("discard")}>Discard draft</button>}
            {v.status === "DRAFT" && canApprove && <button className="btn-primary" onClick={() => setDialog("activate")}>Activate…</button>}
            {v.state === "SCHEDULED" && canApprove && <button className="btn-ghost" onClick={() => setDialog("cancel")}>Cancel schedule</button>}
          </div>
        }
      />
      {v.stale && (
        <div role="status" className="alert-warning">
          A different price book became current after this draft was started. Review the changes below before activating.{" "}
          {v.current_version_id && <Link className="underline" to={`/products/prices/${v.current_version_id}`}>Open current price book</Link>}
        </div>
      )}
      {v.status !== "DRAFT" && <p className="text-sm text-ink-muted">Published price books are read-only. To change prices, create a new draft from the Pricing page.</p>}
      {error && <div role="alert" className={conflict ? "alert-warning" : "alert-danger"}>{error}</div>}
      {editable && <SetPrice version={v} onSave={(b) => run(() => api.setPrice(v.id, b))} />}

      <FilterBar
        search={search}
        onSearch={setSearch}
        placeholder="Search product or SKU"
        options={[
          { value: "all", label: "All" },
          { value: "STANDARD", label: "Standard" },
          { value: "GROUP", label: "Group" },
          { value: "CUSTOMER", label: "Customer" },
        ]}
        value={scope}
        onChange={setScope}
      >
        <label className="sr-only" htmlFor="unit-filter">Unit</label>
        <select id="unit-filter" className="input w-36 py-1.5" value={unit} onChange={(e) => setUnit(e.target.value)}>
          <option value="">All units</option>
          {UNITS.map((u) => <option key={u} value={u}>{UNIT_LABEL[u]}</option>)}
        </select>
      </FilterBar>
      <div className="card overflow-hidden">
        {entries === null ? (
          <LoadingRows cols={5} />
        ) : (
          <>
            <DataGrid
              rows={entries}
              columns={columns}
              rowKey={(r) => r.id}
              caption={`Prices in ${v.version_code} (${total})`}
              empty={<EmptyState title="No prices match" hint={editable ? "Add a price above." : "Try another filter."} />}
            />
            {next && (
              <div className="border-t border-line p-3 text-center">
                <button className="btn-ghost" onClick={() => void loadEntries(next)}>Load more</button>
              </div>
            )}
          </>
        )}
      </div>
      {diff && diff.against_version_id && (
        <div className="card p-6">
          <DiffView diff={diff} title={v.state === "CURRENT" ? "Changes from the previous price book" : "Changes compared with the current price book"} />
        </div>
      )}

      <ConfirmDialog
        open={dialog === "activate"}
        title={`Activate ${v.version_code}?`}
        confirmLabel="Activate"
        onClose={() => setDialog("none")}
        onConfirm={async () => {
          setDialog("none");
          await run(() => api.activatePriceVersion(v.id, v.version, effective ? new Date(effective).toISOString() : undefined));
        }}
      >
        <p>
          The {v.entry_count} prices become read-only and apply to <strong>new</strong> orders and invoices from the start time. Invoices
          already issued keep their prices.
        </p>
        {diff && <p className="mt-2">{diff.changed} changed · {diff.added} added · {diff.removed} removed.</p>}
        <label className="label mt-3" htmlFor="act-when">Start (leave empty for now)</label>
        <input id="act-when" type="datetime-local" className="input" value={effective} onChange={(e) => setEffective(e.target.value)} />
      </ConfirmDialog>
      <ConfirmDialog
        open={dialog === "discard"}
        title={`Discard ${v.version_code}?`}
        confirmLabel="Discard draft"
        danger
        onClose={() => setDialog("none")}
        onConfirm={async () => {
          setDialog("none");
          try {
            await api.discardPriceVersion(v.id, v.version);
            nav("/products/prices");
          } catch (err) {
            setError(pricingMessage(err));
            if (err instanceof ApiError && err.status === 409) await loadVersion();
          }
        }}
      >
        The draft and its prices are deleted. Published price books are not affected.
      </ConfirmDialog>
      <ConfirmDialog
        open={dialog === "cancel"}
        title={`Cancel ${v.version_code}?`}
        confirmLabel="Cancel schedule"
        danger
        onClose={() => setDialog("none")}
        onConfirm={async () => {
          setDialog("none");
          await run(() => api.cancelPriceVersion(v.id, v.version));
        }}
      >
        It will not take effect. The current price book stays in force.
      </ConfirmDialog>
    </div>
  );
}

function SetPrice({ version, onSave }: { version: PriceVersion; onSave: (b: Parameters<typeof api.setPrice>[1]) => Promise<boolean> }) {
  const { products, customers, groups } = useMasterData();
  const [pid, setPid] = useState("");
  const [unit, setUnit] = useState<UnitCode>("LB");
  const [scope, setScope] = useState<PriceScope>("STANDARD");
  const [target, setTarget] = useState("");
  const [rate, setRate] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    const ok = await onSave({
      product_id: pid,
      unit_code: unit,
      scope,
      customer_id: scope === "CUSTOMER" ? target : null,
      group_id: scope === "GROUP" ? target : null,
      rate,
      version: version.version,
    });
    setBusy(false);
    if (ok) setRate("");
  }
  return (
    <form className="card grid items-end gap-3 p-6 lg:grid-cols-[2fr_1fr_1fr_2fr_1fr_auto]" onSubmit={submit} aria-labelledby="sp-h">
      <h2 id="sp-h" className="text-base font-semibold lg:col-span-6">Set a price</h2>
      <div>
        <label className="label" htmlFor="sp-prod">Product</label>
        <select id="sp-prod" className="input" required value={pid} onChange={(e) => setPid(e.target.value)}>
          <option value="">Choose…</option>
          {products.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.sku})</option>)}
        </select>
      </div>
      <div>
        <label className="label" htmlFor="sp-unit">Unit</label>
        <select id="sp-unit" className="input" value={unit} onChange={(e) => setUnit(e.target.value as UnitCode)}>
          {UNITS.map((u) => <option key={u} value={u}>{UNIT_LABEL[u]}</option>)}
        </select>
      </div>
      <div>
        <label className="label" htmlFor="sp-scope">Applies to</label>
        <select id="sp-scope" className="input" value={scope} onChange={(e) => { setScope(e.target.value as PriceScope); setTarget(""); }}>
          <option value="STANDARD">Everyone</option>
          <option value="GROUP">Customer group</option>
          <option value="CUSTOMER">One customer</option>
        </select>
      </div>
      <div>
        <label className="label" htmlFor="sp-target">{scope === "GROUP" ? "Group" : scope === "CUSTOMER" ? "Customer" : "Target"}</label>
        <select id="sp-target" className="input" disabled={scope === "STANDARD"} required={scope !== "STANDARD"} value={target} onChange={(e) => setTarget(e.target.value)}>
          <option value="">{scope === "STANDARD" ? "—" : "Choose…"}</option>
          {scope === "GROUP" && groups.map((g) => <option key={g.id} value={g.id}>{g.name} ({g.code})</option>)}
          {scope === "CUSTOMER" && customers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </div>
      <NumericInput label="Rate (CAD)" value={rate} onChange={setRate} places={4} unit={UNIT_LABEL[unit]} />
      <button className="btn-primary" disabled={busy || !rate}>Save price</button>
    </form>
  );
}
