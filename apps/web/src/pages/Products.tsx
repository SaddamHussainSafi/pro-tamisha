import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  api,
  ApiError,
  hasCompanyWide,
  type Category,
  type Product,
  type ProductSummary,
  type UnitCode,
  type UnitProfile,
  type UnitProfileIn,
  type WeightCalc,
} from "../api";
import { useAuth } from "../auth";
import { catalogMessage } from "../lib/catalogErrors";
import { dec, mul, cad, qty } from "../lib/decimal";
import { DataGrid, type Column } from "../ui/DataGrid";
import { NumericInput } from "../ui/NumericInput";
import { EmptyState, ErrorState, FilterBar, LoadingRows, PageHeader, StatusChip } from "../ui/primitives";
import { ProductPrices } from "./ProductPrices";

const UNITS: UnitCode[] = ["LB", "KG", "BOX", "PIECE"];

const profileLabel = (p: UnitProfile | UnitProfileIn) =>
  p.unit_code === "BOX" || p.unit_code === "PIECE"
    ? p.weight_lb_per_unit
      ? `${p.code} · ${qty(dec(p.weight_lb_per_unit), 2)} lb/${p.unit_code.toLowerCase()}`
      : `${p.code} · weight unknown`
    : `${p.code} · base ${p.unit_code}`;

/* ============================== List ============================== */

type ActiveFilter = "all" | "active" | "inactive";

export function ProductsPage() {
  const { session } = useAuth();
  const nav = useNavigate();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<ActiveFilter>("active");
  const [category, setCategory] = useState("");
  const [cats, setCats] = useState<Category[]>([]);
  const [rows, setRows] = useState<ProductSummary[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [creating, setCreating] = useState(false);

  const load = useCallback(
    async (cursor?: string) => {
      try {
        const page = await api.products({
          search: search.trim() || undefined,
          category_id: category || undefined,
          active: status === "all" ? undefined : status === "active",
          cursor,
        });
        setRows((prev) => (cursor && prev ? [...prev, ...page.items] : page.items));
        setNext(page.next_cursor);
        setLoadError(false);
      } catch {
        setLoadError(true);
      }
    },
    [search, category, status],
  );

  useEffect(() => {
    const t = setTimeout(() => void load(), 200);
    return () => clearTimeout(t);
  }, [load]);
  useEffect(() => {
    api.categories().then(setCats, () => setCats([]));
  }, []);

  if (!session) return null;
  const canManage = hasCompanyWide(session, "catalog.manage");
  const catName = (id: string | null) => cats.find((c) => c.id === id)?.name ?? "—";

  const columns: Column<ProductSummary>[] = [
    { key: "sku", header: "SKU", cell: (r) => <span className="font-medium">{r.sku}</span>, sort: (r) => r.sku },
    { key: "name", header: "Name", cell: (r) => r.name, sort: (r) => r.name.toLowerCase() },
    { key: "cat", header: "Category", cell: (r) => catName(r.category_id), sort: (r) => catName(r.category_id) },
    { key: "unit", header: "Stock unit", cell: (r) => r.stock_unit },
    { key: "exp", header: "Expiry", cell: (r) => (r.tracks_expiry ? "Tracked" : "—") },
    {
      key: "status",
      header: "Status",
      cell: (r) => <StatusChip tone={r.active ? "success" : "neutral"}>{r.active ? "Active" : "Inactive"}</StatusChip>,
    },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title="Products"
        subtitle="Catalog, unit profiles and supplier aliases."
        actions={canManage && <button className="btn-primary" onClick={() => setCreating(true)}>New product</button>}
      />
      {creating && (
        <CreateProduct
          cats={cats}
          onCancel={() => setCreating(false)}
          onCreated={(p) => nav(`/products/${p.id}`)}
        />
      )}
      <FilterBar
        search={search}
        onSearch={setSearch}
        placeholder="Search SKU, name or alias"
        options={[
          { value: "active", label: "Active" },
          { value: "inactive", label: "Inactive" },
          { value: "all", label: "All" },
        ]}
        value={status}
        onChange={setStatus}
      >
        <label className="sr-only" htmlFor="cat-filter">Category</label>
        <select id="cat-filter" className="input w-44 py-1.5" value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">All categories</option>
          {cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
      </FilterBar>
      <div className="card overflow-hidden">
        {loadError ? (
          <ErrorState title="Couldn't load products" onRetry={() => void load()} />
        ) : rows === null ? (
          <LoadingRows cols={6} />
        ) : (
          <>
            <DataGrid
              rows={rows}
              columns={columns}
              rowKey={(r) => r.id}
              caption="Products"
              onOpen={(r) => nav(`/products/${r.id}`)}
              empty={<EmptyState title="No products match" hint="Try another search or filter." />}
            />
            {next && (
              <div className="border-t border-line p-3 text-center">
                <button className="btn-ghost" onClick={() => void load(next)}>Load more</button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/* ============================== Create ============================== */

type DraftProfile = { code: string; unit_code: UnitCode; weight: string; pieces: string; is_default: boolean };
const blankProfile = (): DraftProfile => ({ code: "", unit_code: "BOX", weight: "", pieces: "", is_default: false });

function CreateProduct({ cats, onCancel, onCreated }: { cats: Category[]; onCancel: () => void; onCreated: (p: Product) => void }) {
  const [sku, setSku] = useState("");
  const [name, setName] = useState("");
  const [cat, setCat] = useState("");
  const [unit, setUnit] = useState<UnitCode>("LB");
  const [expiry, setExpiry] = useState(false);
  const [profiles, setProfiles] = useState<DraftProfile[]>([{ ...blankProfile(), is_default: true }]);
  const [aliases, setAliases] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const setP = (i: number, patch: Partial<DraftProfile>) =>
    setProfiles((ps) => ps.map((p, j) => (j === i ? { ...p, ...patch } : patch.is_default ? { ...p, is_default: false } : p)));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const p = await api.createProduct({
        sku: sku.trim(),
        name: name.trim(),
        category_id: cat || null,
        stock_unit: unit,
        tracks_expiry: expiry,
        unit_profiles: profiles
          .filter((p) => p.code.trim())
          .map((p) => ({
            code: p.code.trim().toUpperCase(),
            unit_code: p.unit_code,
            weight_lb_per_unit: p.weight || null,
            pieces_per_unit: p.pieces || null,
            is_default: p.is_default,
          })),
        aliases: aliases
          .split("\n")
          .map((a) => a.trim())
          .filter(Boolean)
          .map((alias) => ({ alias })),
      });
      onCreated(p);
    } catch (err) {
      setError(catalogMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="card space-y-4 p-4" aria-label="New product">
      <h2 className="text-base font-semibold">New product</h2>
      {error && <div role="alert" className="alert-danger">{error}</div>}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <label className="label" htmlFor="p-sku">SKU</label>
          <input id="p-sku" className="input" required maxLength={64} pattern="[A-Za-z0-9._\-]+" value={sku} onChange={(e) => setSku(e.target.value)} />
        </div>
        <div className="sm:col-span-1 lg:col-span-2">
          <label className="label" htmlFor="p-name">Name</label>
          <input id="p-name" className="input" required maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="p-cat">Category</label>
          <select id="p-cat" className="input" value={cat} onChange={(e) => setCat(e.target.value)}>
            <option value="">None</option>
            {cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
        <div>
          <label className="label" htmlFor="p-unit">Stock unit</label>
          <select id="p-unit" className="input" value={unit} onChange={(e) => setUnit(e.target.value as UnitCode)}>
            <option value="LB">LB (pounds)</option>
            <option value="KG">KG (kilograms)</option>
          </select>
          <p className="mt-1 text-xs text-ink-muted">Cannot be changed after creation.</p>
        </div>
        <label className="flex items-center gap-2 self-center text-sm">
          <input type="checkbox" checked={expiry} onChange={(e) => setExpiry(e.target.checked)} /> Track expiry dates
        </label>
      </div>

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">Unit profiles</legend>
        <p className="text-xs text-ink-muted">How cases or pieces convert to weight. Leave weight empty if unknown — sales must then enter actual weights.</p>
        {profiles.map((p, i) => (
          <div key={i} className="flex flex-wrap items-end gap-2">
            <div className="w-28">
              <label className="label" htmlFor={`pr-code-${i}`}>Code</label>
              <input id={`pr-code-${i}`} className="input uppercase" maxLength={32} pattern="[A-Za-z0-9_\-]+" value={p.code} onChange={(e) => setP(i, { code: e.target.value })} />
            </div>
            <div className="w-28">
              <label className="label" htmlFor={`pr-unit-${i}`}>Unit</label>
              <select id={`pr-unit-${i}`} className="input" value={p.unit_code} onChange={(e) => setP(i, { unit_code: e.target.value as UnitCode })}>
                {UNITS.map((u) => <option key={u}>{u}</option>)}
              </select>
            </div>
            {(p.unit_code === "BOX" || p.unit_code === "PIECE") && (
              <>
                <NumericInput className="w-32" label="Weight / unit" unit="lb" places={4} value={p.weight} onChange={(v) => setP(i, { weight: v })} />
                <NumericInput className="w-28" label="Pieces / unit" places={0} value={p.pieces} onChange={(v) => setP(i, { pieces: v })} />
              </>
            )}
            <label className="flex items-center gap-1.5 pb-2 text-sm">
              <input type="radio" name="default-profile" checked={p.is_default} onChange={() => setP(i, { is_default: true })} /> Default
            </label>
            {profiles.length > 1 && (
              <button type="button" className="btn-ghost" aria-label={`Remove profile ${i + 1}`} onClick={() => setProfiles((ps) => ps.filter((_, j) => j !== i))}>Remove</button>
            )}
          </div>
        ))}
        <button type="button" className="btn-ghost" onClick={() => setProfiles((ps) => [...ps, blankProfile()])}>Add case size</button>
      </fieldset>

      <div>
        <label className="label" htmlFor="p-aliases">Aliases (one per line)</label>
        <textarea id="p-aliases" className="input min-h-16" value={aliases} onChange={(e) => setAliases(e.target.value)} placeholder="Names used by suppliers or on invoices" />
      </div>
      <div className="flex justify-end gap-2">
        <button type="button" className="btn-ghost" onClick={onCancel}>Cancel</button>
        <button type="submit" className="btn-primary" disabled={busy}>{busy ? "Saving…" : "Create product"}</button>
      </div>
    </form>
  );
}

/* ============================== Detail ============================== */

export function ProductDetailPage() {
  const { id = "" } = useParams();
  const { session } = useAuth();
  const [prod, setProd] = useState<Product | null>(null);
  const [cats, setCats] = useState<Category[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [edit, setEdit] = useState<{ name: string; category_id: string | null; tracks_expiry: boolean; active: boolean } | null>(null);

  const load = useCallback(async () => {
    try {
      setProd(await api.product(id));
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof ApiError && err.status === 404 ? "Product not found." : "Couldn't load product.");
    }
  }, [id]);
  useEffect(() => void load(), [load]);
  useEffect(() => {
    api.categories().then(setCats, () => setCats([]));
  }, []);

  if (!session) return null;
  if (loadError) return <ErrorState title={loadError} onRetry={() => void load()} />;
  if (!prod) return <LoadingRows cols={4} />;
  const canManage = hasCompanyWide(session, "catalog.manage");

  async function run(fn: () => Promise<unknown>) {
    setError(null);
    setConflict(false);
    try {
      await fn();
      await load();
      return true;
    } catch (err) {
      setError(catalogMessage(err));
      if (err instanceof ApiError && err.code === "VERSION_CONFLICT") {
        setConflict(true);
        setEdit(null);
        await load();
      }
      return false;
    }
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!edit || !prod) return;
    if (await run(() => api.updateProduct(prod.id, { ...edit, version: prod.version }))) setEdit(null);
  }

  return (
    <div className="space-y-5">
      <nav aria-label="Breadcrumb" className="text-sm"><Link className="text-ink-muted hover:underline" to="/products">Products</Link> / {prod.sku}</nav>
      <PageHeader
        title={prod.name}
        subtitle={<>SKU <span className="font-medium">{prod.sku}</span> · stock unit {prod.stock_unit} · version <span className="num">{prod.version}</span></>}
        actions={
          canManage && !edit && (
            <button className="btn-ghost" onClick={() => setEdit({ name: prod.name, category_id: prod.category_id, tracks_expiry: prod.tracks_expiry, active: prod.active })}>Edit</button>
          )
        }
      />
      {error && (
        <div role="alert" className={conflict ? "alert-warning" : "alert-danger"}>
          {error}
        </div>
      )}

      {edit && (
        <form onSubmit={save} className="card flex flex-wrap items-end gap-3 p-4" aria-label="Edit product">
          <div className="min-w-60 flex-1">
            <label className="label" htmlFor="e-name">Name</label>
            <input id="e-name" className="input" required maxLength={200} value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
          </div>
          <div className="w-48">
            <label className="label" htmlFor="e-cat">Category</label>
            <select id="e-cat" className="input" value={edit.category_id ?? ""} onChange={(e) => setEdit({ ...edit, category_id: e.target.value || null })}>
              <option value="">None</option>
              {cats.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
          </div>
          <label className="flex items-center gap-2 pb-2 text-sm"><input type="checkbox" checked={edit.tracks_expiry} onChange={(e) => setEdit({ ...edit, tracks_expiry: e.target.checked })} /> Track expiry</label>
          <label className="flex items-center gap-2 pb-2 text-sm"><input type="checkbox" checked={edit.active} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} /> Active</label>
          <button className="btn-primary" type="submit">Save</button>
          <button className="btn-ghost" type="button" onClick={() => setEdit(null)}>Cancel</button>
        </form>
      )}

      <ProductPrices prod={prod} />
      <div className="grid gap-5 lg:grid-cols-2">
        <Profiles prod={prod} canManage={canManage} run={run} />
        <Aliases prod={prod} canManage={canManage} run={run} />
      </div>
      <WeightCalculator prod={prod} />
    </div>
  );
}

type Run = (fn: () => Promise<unknown>) => Promise<boolean>;

function Profiles({ prod, canManage, run }: { prod: Product; canManage: boolean; run: Run }) {
  const [draft, setDraft] = useState<DraftProfile | null>(null);
  async function add(e: FormEvent) {
    e.preventDefault();
    if (!draft) return;
    const ok = await run(() =>
      api.addProfile(prod.id, {
        code: draft.code.trim().toUpperCase(),
        unit_code: draft.unit_code,
        weight_lb_per_unit: draft.weight || null,
        pieces_per_unit: draft.pieces || null,
      }),
    );
    if (ok) setDraft(null);
  }
  return (
    <section className="card p-4" aria-labelledby="profiles-h">
      <div className="mb-3 flex items-center justify-between">
        <h2 id="profiles-h" className="text-base font-semibold">Case sizes</h2>
        {canManage && !draft && <button className="btn-ghost" onClick={() => setDraft(blankProfile())}>Add case size</button>}
      </div>
      <p className="mb-3 text-xs text-ink-muted">A size is locked once created so invoices stay correct. To change one, add the new size, make it the default and deactivate the old one. All sizes: Products › Case sizes.</p>
      <ul className="divide-y divide-line">
        {prod.unit_profiles.map((p) => (
          <li key={p.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
            <span className={p.active ? "" : "text-ink-muted line-through"}>{profileLabel(p)}</span>
            {p.pieces_per_unit && <span className="text-xs text-ink-muted">{qty(dec(p.pieces_per_unit), 0)} pcs</span>}
            {p.is_default && <StatusChip tone="info">Default</StatusChip>}
            {!p.active && <StatusChip tone="neutral">Inactive</StatusChip>}
            {canManage && p.active && (
              <span className="ml-auto flex gap-1">
                {!p.is_default && (
                  <button className="btn-ghost" onClick={() => void run(() => api.profileCommand(prod.id, p.id, "make-default", prod.version))}>Make default</button>
                )}
                <button className="btn-ghost" onClick={() => void run(() => api.profileCommand(prod.id, p.id, "deactivate", prod.version))}>Deactivate</button>
              </span>
            )}
          </li>
        ))}
      </ul>
      {draft && (
        <form onSubmit={add} className="mt-3 flex flex-wrap items-end gap-2" aria-label="Add case size">
          <div className="w-28">
            <label className="label" htmlFor="np-code">Code</label>
            <input id="np-code" className="input uppercase" required maxLength={32} pattern="[A-Za-z0-9_\-]+" value={draft.code} onChange={(e) => setDraft({ ...draft, code: e.target.value })} />
          </div>
          <div className="w-28">
            <label className="label" htmlFor="np-unit">Unit</label>
            <select id="np-unit" className="input" value={draft.unit_code} onChange={(e) => setDraft({ ...draft, unit_code: e.target.value as UnitCode })}>
              {UNITS.map((u) => <option key={u}>{u}</option>)}
            </select>
          </div>
          {(draft.unit_code === "BOX" || draft.unit_code === "PIECE") && (
            <NumericInput className="w-32" label="Weight / unit" unit="lb" places={4} value={draft.weight} onChange={(v) => setDraft({ ...draft, weight: v })} />
          )}
          <button className="btn-primary" type="submit">Add</button>
          <button className="btn-ghost" type="button" onClick={() => setDraft(null)}>Cancel</button>
        </form>
      )}
    </section>
  );
}

function Aliases({ prod, canManage, run }: { prod: Product; canManage: boolean; run: Run }) {
  const [alias, setAlias] = useState("");
  const [source, setSource] = useState("");
  async function add(e: FormEvent) {
    e.preventDefault();
    if (await run(() => api.addAlias(prod.id, { alias: alias.trim(), source_label: source.trim() }))) {
      setAlias("");
      setSource("");
    }
  }
  return (
    <section className="card p-4" aria-labelledby="aliases-h">
      <h2 id="aliases-h" className="mb-3 text-base font-semibold">Aliases</h2>
      {prod.aliases.length === 0 ? (
        <p className="text-sm text-ink-muted">No aliases yet.</p>
      ) : (
        <ul className="divide-y divide-line">
          {prod.aliases.map((a) => (
            <li key={a.id} className="flex items-center gap-2 py-2 text-sm">
              <span>{a.alias}</span>
              {a.source_label && <span className="text-xs text-ink-muted">from {a.source_label}</span>}
              {canManage && (
                <button className="btn-ghost ml-auto" aria-label={`Remove alias ${a.alias}`} onClick={() => void run(() => api.removeAlias(prod.id, a.id))}>Remove</button>
              )}
            </li>
          ))}
        </ul>
      )}
      {canManage && (
        <form onSubmit={add} className="mt-3 flex flex-wrap items-end gap-2" aria-label="Add alias">
          <div className="min-w-40 flex-1">
            <label className="label" htmlFor="a-name">Alias</label>
            <input id="a-name" className="input" required maxLength={200} value={alias} onChange={(e) => setAlias(e.target.value)} />
          </div>
          <div className="w-36">
            <label className="label" htmlFor="a-src">Source (optional)</label>
            <input id="a-src" className="input" maxLength={100} value={source} onChange={(e) => setSource(e.target.value)} />
          </div>
          <button className="btn-primary" type="submit">Add alias</button>
        </form>
      )}
    </section>
  );
}

type Group = { key: number; profile_id: string; count: string; case_weight: string };

function WeightCalculator({ prod }: { prod: Product }) {
  const active = useMemo(() => prod.unit_profiles.filter((p) => p.active), [prod]);
  const first = active.find((p) => p.is_default) ?? active[0];
  const [groups, setGroups] = useState<Group[]>(() => [{ key: 0, profile_id: first?.id ?? "", count: "", case_weight: "" }]);
  const [price, setPrice] = useState("");
  const [result, setResult] = useState<WeightCalc | null>(null);
  const [error, setError] = useState<string | null>(null);

  const setG = (i: number, patch: Partial<Group>) => setGroups((gs) => gs.map((g, j) => (j === i ? { ...g, ...patch } : g)));

  async function calc(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setResult(null);
    try {
      setResult(
        await api.weightCalc(
          prod.id,
          groups.map((g) => ({ profile_id: g.profile_id, count: g.count, case_weight_lb: g.case_weight || null })),
        ),
      );
    } catch (err) {
      setError(catalogMessage(err));
    }
  }

  if (active.length === 0) return null;
  const amount = result && price ? mul(dec(result.total_lb), dec(price)) : null;

  return (
    <section className="card p-4" aria-labelledby="calc-h">
      <h2 id="calc-h" className="text-base font-semibold">Weight calculator</h2>
      <p className="mb-3 text-xs text-ink-muted">Mix case sizes in one line. Enter the actual case weight when known; otherwise the profile weight is used and the total is marked estimated.</p>
      <form onSubmit={calc} className="space-y-2" aria-label="Weight calculator">
        {groups.map((g, i) => (
          <div key={g.key} className="flex flex-wrap items-end gap-2">
            <div className="w-64">
              <label className="label" htmlFor={`g-prof-${g.key}`}>Profile</label>
              <select id={`g-prof-${g.key}`} className="input" value={g.profile_id} onChange={(e) => setG(i, { profile_id: e.target.value })}>
                {active.map((p) => <option key={p.id} value={p.id}>{profileLabel(p)}</option>)}
              </select>
            </div>
            <NumericInput className="w-28" label="Count" places={4} value={g.count} onChange={(v) => setG(i, { count: v })} />
            <NumericInput className="w-36" label="Actual weight / unit" unit="lb" places={4} value={g.case_weight} onChange={(v) => setG(i, { case_weight: v })} />
            {groups.length > 1 && (
              <button type="button" className="btn-ghost" aria-label={`Remove group ${i + 1}`} onClick={() => setGroups((gs) => gs.filter((_, j) => j !== i))}>Remove</button>
            )}
          </div>
        ))}
        <div className="flex flex-wrap items-end gap-2 pt-1">
          <button
            type="button"
            className="btn-ghost"
            onClick={() => setGroups((gs) => [...gs, { key: Math.max(...gs.map((x) => x.key)) + 1, profile_id: first?.id ?? "", count: "", case_weight: "" }])}
          >
            Add group
          </button>
          <NumericInput className="w-36" label="Price per lb (optional)" unit="CAD" places={4} value={price} onChange={setPrice} />
          <button className="btn-primary" type="submit">Calculate</button>
        </div>
      </form>
      {error && <div role="alert" className="alert-danger mt-3">{error}</div>}
      {result && (
        <div className="mt-4 border-t border-line pt-3" aria-live="polite">
          <table className="w-full text-sm">
            <caption className="sr-only">Weight breakdown</caption>
            <thead className="text-left text-xs uppercase tracking-wide text-ink-muted">
              <tr><th className="py-1">Profile</th><th className="py-1 text-right">Count</th><th className="py-1 text-right">lb / unit</th><th className="py-1 text-right">Weight (lb)</th><th className="py-1 pl-4">Source</th></tr>
            </thead>
            <tbody>
              {result.groups.map((g, i) => (
                <tr key={i} className="border-t border-line">
                  <td className="py-1.5">{active.find((p) => p.id === g.profile_id)?.code}</td>
                  <td className="num py-1.5 text-right">{qty(dec(g.count), 0)} {g.unit_code}</td>
                  <td className="num py-1.5 text-right">{qty(dec(g.weight_lb_per_unit), 2)}</td>
                  <td className="num py-1.5 text-right">{qty(dec(g.weight_lb), 2)}</td>
                  <td className="py-1.5 pl-4 text-xs">{g.weight_source === "ENTERED" ? "Actual" : g.weight_source === "PROFILE_DEFAULT" ? "Profile (estimated)" : "Base unit"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2">
            <div><span className="kicker">Total</span> <span className="num text-lg font-semibold" data-testid="total-lb">{qty(dec(result.total_lb), 2)} lb</span> <span className="num text-sm text-ink-muted">({qty(dec(result.total_kg), 2)} kg)</span></div>
            <div className="text-sm">{Object.entries(result.units).map(([u, n]) => `${qty(dec(n), 0)} ${u}`).join(" · ")}</div>
            {result.estimated ? <StatusChip tone="warning">Estimated — confirm on scale</StatusChip> : <StatusChip tone="success">Actual weights</StatusChip>}
            {amount !== null && <div><span className="kicker">Amount</span> <span className="num text-lg font-semibold" data-testid="amount">{cad(amount)}</span></div>}
          </div>
        </div>
      )}
    </section>
  );
}
