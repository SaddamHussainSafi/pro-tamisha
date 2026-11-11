import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, hasCompanyWide, type CaseSize, type ProductSummary, type UnitCode } from "../api";
import { useAuth } from "../auth";
import { CASE_CODE_RE, caseSizeText, suggestCode } from "../lib/caseSizes";
import { catalogMessage } from "../lib/catalogErrors";
import { dec, qty } from "../lib/decimal";
import { DataGrid, type Column } from "../ui/DataGrid";
import { NumericInput } from "../ui/NumericInput";
import { CardGrid, EntityCard, Tile } from "../ui/CardGrid";
import { ViewToggle } from "../ui/ViewToggle";
import { DeleteAction } from "../ui/DeleteAction";
import { useTrashChanges } from "../lib/trash";
import { useViewMode } from "../lib/viewMode";
import { EmptyState, ErrorState, FilterBar, LoadingRows, PageHeader, StatusChip } from "../ui/primitives";

type Show = "active" | "all";
const SHOW: { value: Show; label: string }[] = [
  { value: "active", label: "In use" },
  { value: "all", label: "All, incl. retired" },
];
type ProductSizes = { product_id: string; product_name: string; sku: string; product_active: boolean; sizes: CaseSize[] };

/** Rows (already sorted by product, default first) grouped into one card per product. */
function groupByProduct(rows: CaseSize[]): ProductSizes[] {
  const out: ProductSizes[] = [];
  for (const r of rows) {
    const last = out[out.length - 1];
    if (last && last.product_id === r.product_id) last.sizes.push(r);
    else out.push({ product_id: r.product_id, product_name: r.product_name, sku: r.sku, product_active: r.product_active, sizes: [r] });
  }
  return out;
}

type Draft = { product_id: string; unit_code: UnitCode; weight: string; pieces: string; code: string; codeTouched: boolean; is_default: boolean };
const BLANK: Draft = { product_id: "", unit_code: "BOX", weight: "", pieces: "", code: "", codeTouched: false, is_default: false };

/** Products › Case sizes: every product's case sizes in one place (e.g. Cassava 30 lb, 35 lb). */
export function CaseSizesPage() {
  const { session } = useAuth();
  const [rows, setRows] = useState<CaseSize[] | null>(null);
  const [products, setProducts] = useState<ProductSummary[]>([]);
  const [loadError, setLoadError] = useState(false);
  const [show, setShow] = useState<Show>("active");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [d, setD] = useState<Draft | null>(null);
  const [msg, setMsg] = useState<{ tone: "error" | "ok"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const canManage = !!session && hasCompanyWide(session, "catalog.manage");
  const [view, setView] = useViewMode("case-sizes");

  useEffect(() => {
    const t = setTimeout(() => setQ(search.trim()), 250);
    return () => clearTimeout(t);
  }, [search]);
  const load = useCallback(async () => {
    try {
      setRows(await api.caseSizes({ q: q || undefined, include_inactive: show === "all" }));
      setLoadError(false);
    } catch {
      setLoadError(true);
    }
  }, [q, show]);
  useEffect(() => void load(), [load]);
  useTrashChanges(load);
  useEffect(() => {
    if (canManage) api.products({ active: true, limit: 1000 }).then((p) => setProducts(p.items), () => setProducts([]));
  }, [canManage]);

  if (!session) return null;

  const set = (patch: Partial<Draft>) =>
    setD((x) => {
      if (!x) return x;
      const n = { ...x, ...patch };
      if (!n.codeTouched && ("weight" in patch || "unit_code" in patch)) n.code = suggestCode(n.unit_code, n.weight);
      return n;
    });

  async function command(r: CaseSize, cmd: "make-default" | "deactivate") {
    setMsg(null);
    setBusy(true);
    try {
      const prod = await api.product(r.product_id);
      await api.profileCommand(r.product_id, r.id, cmd, prod.version);
      setMsg({ tone: "ok", text: cmd === "make-default" ? `${r.code} is now the default size for ${r.product_name}.` : `${r.code} retired for ${r.product_name}.` });
      await load();
    } catch (err) {
      setMsg({ tone: "error", text: catalogMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  async function add(e: FormEvent) {
    e.preventDefault();
    if (!d) return;
    setMsg(null);
    if (!d.product_id) return setMsg({ tone: "error", text: "Choose the product." });
    if (!CASE_CODE_RE.test(d.code.trim())) return setMsg({ tone: "error", text: "Give the size a short name: letters, numbers, dash or underscore (e.g. 30LB)." });
    if (d.unit_code === "BOX" && !d.weight.trim()) return setMsg({ tone: "error", text: "Enter how many lb one box weighs." });
    setBusy(true);
    try {
      const p = await api.addProfile(d.product_id, {
        code: d.code.trim().toUpperCase(),
        unit_code: d.unit_code,
        weight_lb_per_unit: d.weight.trim() || null,
        pieces_per_unit: d.pieces.trim() || null,
        is_default: d.is_default,
      });
      setMsg({ tone: "ok", text: `Added ${d.code.trim().toUpperCase()} to ${p.name}.` });
      setD(null);
      await load();
    } catch (err) {
      setMsg({ tone: "error", text: catalogMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  const columns: Column<CaseSize>[] = [
    {
      key: "p",
      header: "Product",
      cell: (r) => (
        <Link className="font-medium text-brand-primary hover:underline" to={`/products/${r.product_id}`}>
          {r.product_name}
          <span className="font-normal text-ink-muted"> · {r.sku}</span>
        </Link>
      ),
      sort: (r) => r.product_name.toLowerCase(),
    },
    { key: "c", header: "Case size", cell: (r) => <span className="font-medium">{r.code}</span>, sort: (r) => r.code },
    { key: "u", header: "Unit", cell: (r) => r.unit_code.toLowerCase() },
    { key: "w", header: "Weight", align: "right", cell: (r) => <span className="num whitespace-nowrap">{r.weight_lb_per_unit ? `${qty(dec(r.weight_lb_per_unit), 2)} lb` : "—"}</span>, sort: (r) => (r.weight_lb_per_unit ? dec(r.weight_lb_per_unit) : 0n) },
    { key: "n", header: "Pieces", align: "right", cell: (r) => <span className="num">{r.pieces_per_unit ? qty(dec(r.pieces_per_unit), 0) : "—"}</span> },
    {
      key: "s",
      header: "Status",
      cell: (r) => (
        <span className="flex flex-wrap gap-1.5">
          {r.is_default && r.active && <StatusChip tone="info">Default</StatusChip>}
          {!r.active && <StatusChip tone="neutral">Retired</StatusChip>}
          {!r.product_active && <StatusChip tone="neutral">Product inactive</StatusChip>}
        </span>
      ),
    },
    ...(canManage
      ? [
          {
            key: "a",
            header: "Actions",
            cell: (r: CaseSize) =>
              r.active ? (
                <span className="flex gap-1.5">
                  {!r.is_default && (
                    <button type="button" className="btn-ghost px-3 py-1.5 text-xs" disabled={busy} aria-label={`Make ${r.code} the default for ${r.product_name}`} onClick={() => void command(r, "make-default")}>
                      Make default
                    </button>
                  )}
                  <button type="button" className="btn-ghost px-3 py-1.5 text-xs" disabled={busy} aria-label={`Retire ${r.code} for ${r.product_name}`} onClick={() => void command(r, "deactivate")}>
                    Retire
                  </button>
                </span>
              ) : null,
          },
        ]
      : []),
    {
      key: "delete",
      header: "",
      align: "right" as const,
      // The default size can't go (make another the default first).
      cell: (r: CaseSize) => (r.is_default ? null : <DeleteAction compact kind="case_size" id={r.id} label={`${r.code} (${r.sku})`} onDone={() => void load()} />),
    },
  ];

  return (
    <div className="space-y-5">
      <PageHeader
        title="Case sizes"
        subtitle="A product can come in several case sizes, e.g. Cassava 30 lb and 35 lb. The default size is filled in automatically wherever a case size is needed."
        actions={
          <>
            <ViewToggle value={view} onChange={setView} />
            {canManage && !d && (
              <button type="button" className="btn-accent" onClick={() => setD(BLANK)}>
                New case size
              </button>
            )}
          </>
        }
      />
      {msg && (
        <p role={msg.tone === "error" ? "alert" : "status"} className={msg.tone === "error" ? "alert-danger" : "alert-success"}>
          {msg.text}
        </p>
      )}
      {d && (
        <form className="card space-y-4 p-6" aria-labelledby="cs-new" onSubmit={add}>
          <h2 id="cs-new" className="text-xl font-semibold tracking-tight">
            New case size
          </h2>
          <div className="grid gap-4 md:grid-cols-3">
            <div>
              <label className="label" htmlFor="cs-product">
                Product
              </label>
              <select id="cs-product" className="input" value={d.product_id} onChange={(e) => set({ product_id: e.target.value })}>
                <option value="">Choose…</option>
                {products.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.sku})
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label" htmlFor="cs-unit">
                Sold as
              </label>
              <select id="cs-unit" className="input" value={d.unit_code} onChange={(e) => set({ unit_code: e.target.value as UnitCode })}>
                <option value="BOX">Box / case</option>
                <option value="PIECE">Piece</option>
              </select>
            </div>
            <NumericInput label={d.unit_code === "BOX" ? "Weight of one box" : "Weight of one piece (optional)"} unit="lb" places={4} value={d.weight} onChange={(v) => set({ weight: v })} />
            {d.unit_code === "BOX" && <NumericInput label="Pieces in one box (optional)" unit="pcs" places={0} value={d.pieces} onChange={(v) => set({ pieces: v })} />}
            <div>
              <label className="label" htmlFor="cs-code">
                Size name
              </label>
              <input id="cs-code" className="input uppercase" maxLength={32} value={d.code} placeholder="30LB" onChange={(e) => set({ code: e.target.value, codeTouched: true })} />
              <p className="mt-1 text-sm text-ink-muted">Short name shown on orders and invoices.</p>
            </div>
            <label className="flex items-center gap-2.5 self-center text-sm">
              <input type="checkbox" className="size-4 accent-brand-primary" checked={d.is_default} onChange={(e) => set({ is_default: e.target.checked })} />
              Make this the default size for the product
            </label>
          </div>
          {d.code && <p className="text-sm text-ink-muted">Preview: {caseSizeText({ code: d.code.toUpperCase(), unit_code: d.unit_code, weight_lb_per_unit: d.weight || null, pieces_per_unit: d.pieces || null })}</p>}
          <div className="flex justify-end gap-2">
            <button type="button" className="btn-ghost" onClick={() => setD(null)}>
              Cancel
            </button>
            <button className="btn-primary" disabled={busy}>
              Add case size
            </button>
          </div>
        </form>
      )}
      <FilterBar search={search} onSearch={setSearch} options={SHOW} value={show} onChange={setShow} placeholder="Search product or SKU" />
      {loadError ? (
        <ErrorState title="Couldn't load case sizes" onRetry={() => void load()} />
      ) : rows === null ? (
        <LoadingRows cols={6} />
      ) : view === "grid" ? (
        <CardGrid
          items={groupByProduct(rows)}
          keyOf={(g) => g.product_id}
          caption={`Case sizes (${rows.length})`}
          empty={<EmptyState title={q ? "No case sizes match" : "No case sizes yet"} />}
          render={(g) => (
            <EntityCard
              to={`/products/${g.product_id}`}
              title={g.product_name}
              subtitle={`${g.sku} · ${g.sizes.length} ${g.sizes.length === 1 ? "size" : "sizes"}`}
              media={<Tile text={g.product_name} muted={!g.product_active} />}
              dim={!g.product_active}
              chips={
                <ul className="relative z-10 w-full space-y-2" aria-label={`Sizes of ${g.product_name}`}>
                  {g.sizes.map((r) => (
                    <li key={r.id} className="flex flex-wrap items-center gap-2 rounded-2xl bg-subtle px-3 py-2 text-sm">
                      <span className={`min-w-0 flex-1 ${r.active ? "" : "text-ink-muted line-through"}`}>{caseSizeText(r)}</span>
                      {r.is_default && r.active && <StatusChip tone="info">Default</StatusChip>}
                      {!r.active && <StatusChip tone="neutral">Retired</StatusChip>}
                      {canManage && r.active && !r.is_default && (
                        <button type="button" className="btn-ghost px-2.5 py-1 text-xs" disabled={busy} aria-label={`Make ${r.code} the default for ${r.product_name}`} onClick={() => void command(r, "make-default")}>
                          Make default
                        </button>
                      )}
                      {canManage && r.active && (
                        <button type="button" className="btn-ghost px-2.5 py-1 text-xs" disabled={busy} aria-label={`Retire ${r.code} for ${r.product_name}`} onClick={() => void command(r, "deactivate")}>
                          Retire
                        </button>
                      )}
                      {!r.is_default && <DeleteAction compact kind="case_size" id={r.id} label={`${r.code} (${r.sku})`} onDone={() => void load()} />}
                    </li>
                  ))}
                </ul>
              }
            />
          )}
        />
      ) : (
        <DataGrid rows={rows} columns={columns} rowKey={(r) => r.id} caption={`Case sizes (${rows.length})`} empty={<EmptyState title={q ? "No case sizes match" : "No case sizes yet"} />} />
      )}
      <p className="text-sm text-ink-muted">A size's weight can't be edited once used, so invoices stay correct. To change it, add the new size, make it the default and retire the old one.</p>
    </div>
  );
}
