import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, has, type Customer, type CustomerGroup, type PriceEntry, type PriceScope, type PriceVersion, type Product, type UnitCode } from "../api";
import { useAuth } from "../auth";
import { draftCode, priceBooks } from "../lib/caseSizes";
import { pricingMessage, rateText, scopeLabel } from "../lib/pricingErrors";
import { NumericInput } from "../ui/NumericInput";
import { CardTitle, EmptyState, LoadingRows, StatusChip } from "../ui/primitives";

const UNIT_TEXT: Record<UnitCode, string> = { BOX: "per box", PIECE: "per piece", LB: "per lb", KG: "per kg" };

/**
 * Product page › Prices. Shows the prices in force for this product. A change is written into a
 * price-book draft (made from the current book if none is open); prices change only when the
 * draft is activated, which needs price.approve — the versioning rules of Module 02 still apply.
 */
export function ProductPrices({ prod }: { prod: Product }) {
  const { session } = useAuth();
  const [books, setBooks] = useState<{ current?: PriceVersion; draft?: PriceVersion } | null>(null);
  const [rows, setRows] = useState<PriceEntry[]>([]);
  const [draftRows, setDraftRows] = useState<PriceEntry[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [groups, setGroups] = useState<CustomerGroup[]>([]);
  const [form, setForm] = useState<{ unit: UnitCode; scope: PriceScope; target: string; rate: string } | null>(null);
  const [msg, setMsg] = useState<{ tone: "error" | "ok"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const canRead = !!session && has(session, "price.read");
  const canDraft = !!session && has(session, "price.draft");
  const canApprove = !!session && has(session, "price.approve");

  const load = useCallback(async () => {
    if (!canRead) return;
    const b = priceBooks(await api.priceVersions());
    setBooks(b);
    setRows(b.current ? (await api.priceEntries(b.current.id, { product_id: prod.id, limit: 200 })).items : []);
    setDraftRows(b.draft ? (await api.priceEntries(b.draft.id, { product_id: prod.id, limit: 200 })).items : []);
  }, [canRead, prod.id]);
  useEffect(() => {
    load().catch(() => setBooks({}));
  }, [load]);
  useEffect(() => {
    if (!canDraft) return;
    api.customers({ limit: 500 }).then(setCustomers, () => setCustomers([]));
    api.customerGroups().then(setGroups, () => setGroups([]));
  }, [canDraft]);

  if (!canRead) return null;
  const units = Array.from(new Set<UnitCode>([...prod.unit_profiles.filter((p) => p.active).map((p) => p.unit_code), prod.stock_unit]));
  const key = (e: PriceEntry) => `${e.unit_code}|${e.scope}|${e.customer_id ?? e.group_id ?? ""}`;
  const live = new Map(rows.map((e) => [key(e), e.rate]));
  const pending = draftRows.filter((e) => live.get(key(e)) !== e.rate);

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!form || !books) return;
    setMsg(null);
    if (form.scope !== "STANDARD" && !form.target) return setMsg({ tone: "error", text: form.scope === "CUSTOMER" ? "Choose the customer." : "Choose the group." });
    if (!form.rate) return setMsg({ tone: "error", text: "Enter the price." });
    setBusy(true);
    try {
      const draft = books.draft ?? (await api.createPriceVersion({ version_code: draftCode(), notes: `Changed from product ${prod.sku}`, based_on_id: books.current?.id ?? null }));
      const saved = await api.setPrice(draft.id, {
        product_id: prod.id,
        unit_code: form.unit,
        scope: form.scope,
        customer_id: form.scope === "CUSTOMER" ? form.target : null,
        group_id: form.scope === "GROUP" ? form.target : null,
        rate: form.rate,
        version: draft.version,
      });
      setForm(null);
      setMsg({ tone: "ok", text: `Saved in draft ${saved.version_code}. The price changes when the draft is activated.` });
      await load();
    } catch (err) {
      setMsg({ tone: "error", text: pricingMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  async function activate() {
    if (!books?.draft) return;
    setMsg(null);
    setBusy(true);
    try {
      const v = await api.activatePriceVersion(books.draft.id, books.draft.version);
      setMsg({ tone: "ok", text: `Draft ${v.version_code} is now the price book in force.` });
      await load();
    } catch (err) {
      setMsg({ tone: "error", text: pricingMessage(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card space-y-4 p-6" aria-labelledby="pp-h">
      <CardTitle
        id="pp-h"
        title="Prices"
        action={
          canDraft && !form ? (
            <button type="button" className="btn-ghost" onClick={() => setForm({ unit: units[0] ?? "BOX", scope: "STANDARD", target: "", rate: "" })}>
              Change price
            </button>
          ) : undefined
        }
      />
      {msg && (
        <p role={msg.tone === "error" ? "alert" : "status"} className={msg.tone === "error" ? "alert-danger" : "alert-success"}>
          {msg.text}
        </p>
      )}
      {books === null ? (
        <LoadingRows rows={2} cols={3} />
      ) : !books.current ? (
        <EmptyState title="No price book in force yet" hint="Import a price list under Products › Prices." />
      ) : rows.length === 0 ? (
        <p className="text-sm text-ink-muted">No price for this product in {books.current.version_code}.</p>
      ) : (
        <table className="w-full text-sm">
          <caption className="sr-only">Prices in force for {prod.name}</caption>
          <thead>
            <tr className="text-left text-ink-muted">
              <th className="py-2 font-medium">Who</th>
              <th className="py-2 font-medium">Unit</th>
              <th className="py-2 text-right font-medium">Price</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-t border-line">
                <td className="py-2.5">{scopeLabel(r.scope, r.target)}</td>
                <td className="py-2.5">{UNIT_TEXT[r.unit_code]}</td>
                <td className="num py-2.5 text-right font-semibold">{rateText(r.rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {books?.current && (
        <p className="text-sm text-ink-muted">
          From price book{" "}
          <Link className="text-brand-primary underline" to={`/products/prices/${books.current.id}`}>
            {books.current.version_code}
          </Link>
          .
        </p>
      )}
      {books?.draft && pending.length > 0 && (
        <div className="space-y-2 rounded-2xl bg-subtle p-4 text-sm">
          <p className="flex flex-wrap items-center gap-2 font-medium">
            <StatusChip tone="warning">Waiting</StatusChip>
            New prices in draft{" "}
            <Link className="text-brand-primary underline" to={`/products/prices/${books.draft.id}`}>
              {books.draft.version_code}
            </Link>
          </p>
          <ul className="space-y-1">
            {pending.map((r) => (
              <li key={r.id}>
                {scopeLabel(r.scope, r.target)}, {UNIT_TEXT[r.unit_code]}: <span className="num font-semibold">{rateText(r.rate)}</span>
                {live.has(key(r)) && <span className="text-ink-muted"> (now {rateText(live.get(key(r)) ?? "0")})</span>}
              </li>
            ))}
          </ul>
          {canApprove ? (
            <button type="button" className="btn-primary" disabled={busy} onClick={() => void activate()}>
              Activate draft now
            </button>
          ) : (
            <p className="text-ink-muted">A manager activates the draft to put these prices in force.</p>
          )}
        </div>
      )}
      {form && (
        <form className="grid gap-3 rounded-2xl border border-line p-4 md:grid-cols-2" aria-label="Change price" onSubmit={save}>
          <div>
            <label className="label" htmlFor="pp-unit">
              Unit
            </label>
            <select id="pp-unit" className="input" value={form.unit} onChange={(e) => setForm({ ...form, unit: e.target.value as UnitCode })}>
              {units.map((u) => (
                <option key={u} value={u}>
                  {UNIT_TEXT[u]}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="pp-scope">
              Price for
            </label>
            <select id="pp-scope" className="input" value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value as PriceScope, target: "" })}>
              <option value="STANDARD">Everyone (standard price)</option>
              <option value="GROUP">A customer group</option>
              <option value="CUSTOMER">One customer</option>
            </select>
          </div>
          {form.scope !== "STANDARD" && (
            <div>
              <label className="label" htmlFor="pp-target">
                {form.scope === "CUSTOMER" ? "Customer" : "Group"}
              </label>
              <select id="pp-target" className="input" value={form.target} onChange={(e) => setForm({ ...form, target: e.target.value })}>
                <option value="">Choose…</option>
                {(form.scope === "CUSTOMER" ? customers.map((c) => ({ id: c.id, name: `${c.name}${c.external_ref ? ` (${c.external_ref})` : ""}` })) : groups.map((g) => ({ id: g.id, name: g.name }))).map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </select>
            </div>
          )}
          <NumericInput label="New price" unit="CAD" places={4} value={form.rate} onChange={(v) => setForm({ ...form, rate: v })} />
          <p className="text-sm text-ink-muted md:col-span-2">The change goes into a price-book draft{books?.draft ? ` (${books.draft.version_code})` : ""}. Issued invoices keep their old prices.</p>
          <div className="flex justify-end gap-2 md:col-span-2">
            <button type="button" className="btn-ghost" onClick={() => setForm(null)}>
              Cancel
            </button>
            <button className="btn-primary" disabled={busy}>
              Save to draft
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
