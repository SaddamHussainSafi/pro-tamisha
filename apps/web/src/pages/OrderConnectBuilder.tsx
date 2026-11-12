import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { errorText } from "../lib/errors";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, api, hasCompanyWide, type Category, type ProductSummary } from "../api";
import { useAuth } from "../auth";
import {
  CUSTOM_KINDS,
  KIND_LABEL,
  SYSTEM_FIELDS,
  customKey,
  draftOf,
  fieldProblems,
  missingSystemFields,
  moveField,
  oc,
  publishProblems,
  type DraftIn,
  type FieldKind,
  type FormContent,
  type FormField,
  type OcForm,
  type PreviewConfig,
} from "../lib/orderConnect";
import { Icon } from "../ui/Icon";
import { EmptyState, ErrorState, PageHeader, PageSkeleton, StatusChip } from "../ui/primitives";

const errText = errorText;

export function FormStateChip({ f }: { f: Pick<OcForm, "published" | "accepting" | "unpublished_changes" | "archived"> }) {
  if (f.archived) return <StatusChip tone="neutral">Archived</StatusChip>;
  if (!f.published) return <StatusChip tone="neutral">Not published</StatusChip>;
  if (!f.accepting) return <StatusChip tone="warning">Paused</StatusChip>;
  return <StatusChip tone="success">Live</StatusChip>;
}

function useCanManage() {
  const { session } = useAuth();
  return !!session && hasCompanyWide(session, "order_connect.manage");
}

/* ================================================================ forms list */

export function OcFormsPage() {
  const { session } = useAuth();
  const canRead = !!session && hasCompanyWide(session, "order_connect.read");
  const canManage = useCanManage();
  const navigate = useNavigate();
  const [forms, setForms] = useState<OcForm[] | null>(null);
  const [error, setError] = useState("");
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [formErr, setFormErr] = useState("");

  const load = useCallback(() => {
    setError("");
    oc.forms().then(setForms, (e) => setError(errText(e)));
  }, []);
  useEffect(() => {
    if (canRead) load();
  }, [canRead, load]);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return setFormErr("Give the form a name");
    setBusy(true);
    try {
      const f = await oc.createForm(name.trim(), null);
      navigate(`/online-orders/forms/${f.id}`);
    } catch (err) {
      setFormErr(errText(err));
      setBusy(false);
    }
  };

  if (!canRead)
    return (
      <div className="space-y-5">
        <PageHeader title="Form builder" />
        <EmptyState title="You don't have access to Online Orders" hint="Ask an administrator for the Online Orders permissions." />
      </div>
    );

  return (
    <div className="space-y-5">
      <PageHeader
        title="Form builder"
        subtitle="Order and quote forms customers fill in on your website. Requests arrive in Online Orders."
        actions={canManage && <button className="btn-primary" onClick={() => setCreating(true)}><Icon name="plus" size={18} />New form</button>}
      />
      {creating && (
        <form onSubmit={create} className="card flex flex-wrap items-end gap-3 p-5" aria-label="New form">
          <div className="min-w-[16rem] flex-1">
            <label htmlFor="oc-new-name" className="label">Form name</label>
            <input id="oc-new-name" className="input" autoFocus value={name} maxLength={120} onChange={(e) => { setName(e.target.value); setFormErr(""); }} placeholder="Wholesale orders" />
          </div>
          <button type="submit" className="btn-primary" disabled={busy}>{busy ? "Creating…" : "Create form"}</button>
          <button type="button" className="btn-ghost" onClick={() => setCreating(false)}>Cancel</button>
          {formErr && <p role="alert" className="alert-danger w-full">{formErr}</p>}
        </form>
      )}
      {error ? (
        <div className="card"><ErrorState title="Couldn't load forms" detail={error} onRetry={load} /></div>
      ) : forms === null ? (
        <PageSkeleton label="Loading forms" />
      ) : forms.length === 0 ? (
        <div className="card">
          <EmptyState icon="globe" title="No order forms yet" hint="Create a form, pick its fields and publish it. Then paste one line of code into your website." />
        </div>
      ) : (
        <ul className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {forms.map((f) => (
            <li key={f.id} className="card flex flex-col gap-4 p-6">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <h2 className="truncate text-lg font-semibold">{f.name}</h2>
                  <p className="text-sm text-ink-muted">
                    {f.published ? `Version ${f.published_version_no} live` : "Draft only"}
                    {f.published && f.unpublished_changes ? " · unpublished changes" : ""}
                  </p>
                </div>
                <FormStateChip f={f} />
              </div>
              <dl className="grid grid-cols-2 gap-3 text-sm">
                <div><dt className="text-ink-muted">Open</dt><dd className="num text-xl font-semibold">{f.open_submissions}</dd></div>
                <div><dt className="text-ink-muted">All requests</dt><dd className="num text-xl font-semibold">{f.submissions}</dd></div>
              </dl>
              <div className="mt-auto flex flex-wrap gap-2">
                <Link to={`/online-orders/forms/${f.id}`} className="btn-primary">{canManage ? "Edit form" : "View form"}</Link>
                <Link to={`/online-orders/embed?form=${f.id}`} className="btn-ghost">Embed</Link>
                <Link to={`/online-orders/requests?form=${f.id}`} className="btn-ghost">Requests</Link>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/* ================================================================ builder */

type Tab = "fields" | "content" | "design" | "products";
const TABS: { id: Tab; label: string }[] = [
  { id: "fields", label: "Fields" },
  { id: "content", label: "Text" },
  { id: "design", label: "Design" },
  { id: "products", label: "Products" },
];

export function OcBuilderPage() {
  const { id = "" } = useParams();
  const canManage = useCanManage();
  const { session } = useAuth();
  const canRead = !!session && hasCompanyWide(session, "order_connect.read");
  const [form, setForm] = useState<(OcForm & { editing: FormContent }) | null>(null);
  const [accents, setAccents] = useState<{ value: string; label: string }[]>([]);
  const [draft, setDraft] = useState<DraftIn | null>(null);
  const [logoUrl, setLogoUrl] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [tab, setTab] = useState<Tab>("fields");
  const [error, setError] = useState("");
  const [msg, setMsg] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [catalog, setCatalog] = useState<PreviewConfig | null>(null);
  const [selected, setSelected] = useState(0);

  const adopt = useCallback((f: OcForm & { editing: FormContent }) => {
    setForm(f);
    setDraft(draftOf(f.editing));
    setLogoUrl(f.editing.logo_url);
    setDirty(false);
  }, []);
  const load = useCallback(() => {
    setError("");
    oc.form(id).then((f) => { setAccents(f.accents); adopt(f); }, (e) => setError(errText(e)));
    oc.previewConfig(id).then(setCatalog, () => undefined);
  }, [id, adopt]);
  useEffect(() => {
    if (canRead) load();
  }, [canRead, load]);

  // Leaving with unsaved edits asks first.
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const change = (patch: Partial<DraftIn>) => {
    setDraft((d) => (d ? { ...d, ...patch } : d));
    setDirty(true);
    setMsg(null);
  };
  const setFields = (fields: FormField[]) => change({ fields });

  const fieldErr = draft ? draft.fields.map(fieldProblems).find(Boolean) : null;
  const pubProblems = draft ? publishProblems(draft.fields) : [];

  const save = async (): Promise<(OcForm & { editing: FormContent }) | null> => {
    if (!draft) return null;
    if (fieldErr) {
      setMsg({ tone: "error", text: fieldErr });
      return null;
    }
    setBusy(true);
    try {
      const f = await oc.saveDraft(id, draft);
      adopt(f);
      oc.previewConfig(id).then(setCatalog, () => undefined);
      setMsg({ tone: "ok", text: "Draft saved. Customers still see the published version until you publish." });
      return f;
    } catch (e) {
      setMsg({ tone: "error", text: e instanceof ApiError && e.code === "VERSION_CONFLICT" ? "Someone else changed this form. Reload to see their changes." : errText(e) });
      return null;
    } finally {
      setBusy(false);
    }
  };
  const publish = async () => {
    if (pubProblems.length) return setMsg({ tone: "error", text: `Before publishing, add ${pubProblems.join(", ")}.` });
    let f = form;
    if (dirty || !f?.unpublished_changes) {
      f = dirty ? await save() : f;
      if (!f) return;
    }
    if (!f?.unpublished_changes) return setMsg({ tone: "ok", text: "Nothing new to publish." });
    setBusy(true);
    try {
      const out = await oc.publish(id, f.editing.revision);
      adopt(out);
      setMsg({ tone: "ok", text: `Published. Version ${out.published_version_no} is now live on every page that embeds this form.` });
    } catch (e) {
      setMsg({ tone: "error", text: errText(e) });
    } finally {
      setBusy(false);
    }
  };
  const toggleAccepting = async () => {
    if (!form) return;
    try {
      const f = await oc.patchForm(id, { version: form.version, accepting: !form.accepting });
      setForm({ ...form, ...f });
    } catch (e) {
      setMsg({ tone: "error", text: errText(e) });
    }
  };

  if (!canRead) return <EmptyState title="You don't have access to Online Orders" />;
  if (error) return <div className="card"><ErrorState title="Couldn't load this form" detail={error} onRetry={load} /></div>;
  if (!form || !draft) return <PageSkeleton label="Loading form" />;
  const ro = !canManage;

  return (
    <div className="space-y-5">
      <PageHeader
        title={form.name}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <FormStateChip f={form} />
            <span>{form.published ? `Version ${form.published_version_no} is live` : "Not published yet"}</span>
            {(dirty || form.unpublished_changes) && form.published && <StatusChip tone="warning">{dirty ? "Unsaved changes" : "Draft not published"}</StatusChip>}
          </span>
        }
        actions={
          canManage && (
            <>
              {form.published && (
                <button className="btn-ghost" onClick={toggleAccepting} aria-pressed={!form.accepting}>
                  {form.accepting ? "Pause orders" : "Resume orders"}
                </button>
              )}
              <button className="btn-ghost" onClick={() => void save()} disabled={busy || !dirty}>Save draft</button>
              <button className="btn-primary" onClick={() => void publish()} disabled={busy}>Publish</button>
            </>
          )
        }
      />
      {msg && (
        <p role={msg.tone === "error" ? "alert" : "status"} className={msg.tone === "error" ? "alert-danger" : "alert-success"}>{msg.text}</p>
      )}
      {!ro && pubProblems.length > 0 && (
        <p className="alert-warning">To publish, the form needs {pubProblems.join(", ")}.</p>
      )}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]">
        <section className="card min-w-0 p-5 sm:p-6" aria-label="Form editor">
          <div role="tablist" aria-label="Editor sections" className="mb-5 flex flex-wrap gap-1.5">
            {TABS.map((t) => (
              <button
                key={t.id}
                role="tab"
                id={`oc-tab-${t.id}`}
                aria-selected={tab === t.id}
                aria-controls={`oc-panel-${t.id}`}
                className={`chip cursor-pointer px-4 py-2 text-sm ${tab === t.id ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:bg-subtle"}`}
                onClick={() => setTab(t.id)}
              >
                {t.label}
              </button>
            ))}
          </div>
          <div role="tabpanel" id={`oc-panel-${tab}`} aria-labelledby={`oc-tab-${tab}`}>
            <fieldset disabled={ro} className="min-w-0 space-y-4">
              {tab === "fields" && <FieldsEditor fields={draft.fields} onChange={setFields} selected={selected} onSelect={setSelected} />}
              {tab === "content" && <ContentEditor draft={draft} change={change} />}
              {tab === "design" && (
                <DesignEditor accents={accents} draft={draft} change={change} logoUrl={logoUrl} onLogo={(lid, url) => { setLogoUrl(url); change({ logo_id: lid }); }} onError={(t) => setMsg({ tone: "error", text: t })} />
              )}
              {tab === "products" && <ProductsEditor draft={draft} change={change} />}
            </fieldset>
          </div>
        </section>
        <Preview draft={draft} logoUrl={logoUrl} catalog={catalog} />
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- fields */

function FieldsEditor({ fields, onChange, selected, onSelect }: { fields: FormField[]; onChange: (f: FormField[]) => void; selected: number; onSelect: (i: number) => void }) {
  const [drag, setDrag] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const live = useRef<HTMLParagraphElement>(null);
  const say = (t: string) => {
    if (live.current) live.current.textContent = t;
  };
  const move = (from: number, to: number) => {
    if (to < 0 || to >= fields.length) return;
    onChange(moveField(fields, from, to));
    onSelect(to);
    say(`${fields[from]?.label ?? "Field"} moved to position ${to + 1} of ${fields.length}`);
  };
  const update = (i: number, patch: Partial<FormField>) => onChange(fields.map((f, j) => (j === i ? { ...f, ...patch } : f)));
  const remove = (i: number) => {
    const label = fields[i]?.label ?? "Field";
    onChange(fields.filter((_, j) => j !== i));
    onSelect(Math.max(0, i - 1));
    say(`${label} removed`);
  };
  const add = (key: string, kind: FieldKind, label: string) => {
    const f: FormField = { key, kind, label, help_text: "", required: false, options: kind === "SELECT" ? ["Option 1", "Option 2"] : [] };
    onChange([...fields, f]);
    onSelect(fields.length);
    setAdding(false);
    say(`${label} added at the end`);
  };
  const missing = missingSystemFields(fields);

  return (
    <div className="min-w-0">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-semibold">Fields</h2>
        <span className="text-xs text-ink-muted">Drag or use the arrows to reorder. Click a field to edit it.</span>
      </div>
      <ol className="space-y-1.5" aria-label="Form fields in order">
        {fields.map((f, i) => {
          const open = i === selected;
          const problem = fieldProblems(f);
          return (
            <li
              key={f.key}
              draggable={!open}
              onDragStart={(e) => { setDrag(i); e.dataTransfer.effectAllowed = "move"; }}
              onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }}
              onDrop={(e) => { e.preventDefault(); if (drag !== null) move(drag, i); setDrag(null); }}
              onDragEnd={() => setDrag(null)}
              className={`rounded-ctl border ${open ? "border-brand-primary bg-brand-accent/8" : "border-line bg-card"} ${drag === i ? "opacity-50" : ""}`}
            >
              <div className="flex items-center gap-1 px-2 py-1">
                <span aria-hidden className="cursor-grab px-1 text-ink-muted">⋮⋮</span>
                <button type="button" className="min-w-0 flex-1 cursor-pointer truncate py-2 text-left text-sm" onClick={() => onSelect(open ? -1 : i)} aria-expanded={open}>
                  <span className="font-medium">{f.label || "Untitled"}</span>
                  {f.required && <span className="text-danger" aria-label=" (required)"> *</span>}
                  <span className="ml-2 text-xs text-ink-muted">{KIND_LABEL[f.kind]}</span>
                  {problem && <span className="ml-2 text-xs text-danger">needs attention</span>}
                </button>
                <button type="button" className="grid size-9 cursor-pointer place-items-center rounded-full hover:bg-subtle disabled:opacity-30" disabled={i === 0} onClick={() => move(i, i - 1)} aria-label={`Move ${f.label} up`}>
                  <Icon name="chevronDown" size={16} className="rotate-180" />
                </button>
                <button type="button" className="grid size-9 cursor-pointer place-items-center rounded-full hover:bg-subtle disabled:opacity-30" disabled={i === fields.length - 1} onClick={() => move(i, i + 1)} aria-label={`Move ${f.label} down`}>
                  <Icon name="chevronDown" size={16} />
                </button>
              </div>
              {open && (
                <div className="grid grid-cols-1 gap-4 border-t border-line px-4 py-4 sm:grid-cols-2">
                  <p className="text-xs text-ink-muted sm:col-span-2">{SYSTEM_FIELDS[f.key]?.hint ?? "Custom field (shown to staff on the request)"}</p>
                  <div>
                    <label htmlFor="oc-f-label" className="label">Label</label>
                    <input id="oc-f-label" className="input" value={f.label} maxLength={120} onChange={(e) => update(i, { label: e.target.value })} />
                  </div>
                  <div>
                    <label htmlFor="oc-f-help" className="label">Help text <span className="font-normal text-ink-muted">(optional)</span></label>
                    <input id="oc-f-help" className="input" value={f.help_text} maxLength={300} onChange={(e) => update(i, { help_text: e.target.value })} />
                  </div>
                  {f.kind === "SELECT" && (
                    <div className="sm:col-span-2">
                      <label htmlFor="oc-f-opts" className="label">Options, one per line</label>
                      <textarea id="oc-f-opts" className="input min-h-28" value={f.options.join("\n")} onChange={(e) => update(i, { options: e.target.value.split("\n").slice(0, 50) })} />
                    </div>
                  )}
                  <label className="flex cursor-pointer items-center gap-3 text-sm sm:col-span-2">
                    <input type="checkbox" className="size-5" checked={f.required} onChange={(e) => update(i, { required: e.target.checked })} />
                    Customers must fill this in
                  </label>
                  {problem && <p className="text-sm text-danger sm:col-span-2" role="alert">{problem}</p>}
                  <div className="sm:col-span-2">
                    <button type="button" className="btn-ghost text-danger" onClick={() => remove(i)}>
                      <Icon name="x" size={16} />Remove field
                    </button>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ol>
      {fields.length === 0 && <EmptyState title="No fields" hint="Add a field to start." />}
      <p ref={live} className="sr-only" aria-live="polite" />
      <div className="mt-3">
        {adding ? (
          <div className="rounded-ctl border border-line p-3">
            <div className="mb-2 text-sm font-medium">Add a field</div>
            <div className="flex flex-wrap gap-1.5">
              {missing.map(([k, sf]) => (
                <button key={k} type="button" className="chip cursor-pointer border-line bg-card px-3 py-1.5 hover:bg-subtle" onClick={() => add(k, sf.kind, sf.label)}>
                  <Icon name="plus" size={14} />{sf.label}
                </button>
              ))}
              {CUSTOM_KINDS.map((c) => (
                <button key={c.kind} type="button" className="chip cursor-pointer border-line bg-card px-3 py-1.5 hover:bg-subtle" onClick={() => add(customKey(c.label, fields), c.kind, c.label)}>
                  <Icon name="plus" size={14} />{c.label}
                </button>
              ))}
            </div>
            <button type="button" className="btn-ghost mt-3" onClick={() => setAdding(false)}>Close</button>
          </div>
        ) : (
          <button type="button" className="btn-ghost" onClick={() => setAdding(true)} disabled={fields.length >= 30}><Icon name="plus" size={16} />Add field</button>
        )}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- text */

function ContentEditor({ draft, change }: { draft: DraftIn; change: (p: Partial<DraftIn>) => void }) {
  return (
    <>
      <div>
        <label htmlFor="oc-title" className="label">Title</label>
        <input id="oc-title" className="input" value={draft.title} maxLength={120} aria-describedby="oc-title-help" onChange={(e) => change({ title: e.target.value })} />
        <p id="oc-title-help" className="mt-1 text-sm text-ink-muted">Shown only on the direct link. On your website the page keeps its own title, header and footer.</p>
      </div>
      <div>
        <label htmlFor="oc-intro" className="label">Introduction <span className="font-normal text-ink-muted">(optional)</span></label>
        <textarea id="oc-intro" className="input min-h-24" value={draft.intro} maxLength={1000} onChange={(e) => change({ intro: e.target.value })} placeholder="Order fresh produce for next-day delivery." />
      </div>
      <div>
        <label htmlFor="oc-success" className="label">Message after sending</label>
        <textarea id="oc-success" className="input min-h-24" value={draft.success_message} maxLength={500} onChange={(e) => change({ success_message: e.target.value })} />
        <p className="mt-1 text-sm text-ink-muted">Customers also see their reference number (for example OC-7K3M9Q).</p>
      </div>
    </>
  );
}

/* ---------------------------------------------------------------- design */

function DesignEditor({
  accents,
  draft,
  change,
  logoUrl,
  onLogo,
  onError,
}: {
  accents: { value: string; label: string }[];
  draft: DraftIn;
  change: (p: Partial<DraftIn>) => void;
  logoUrl: string | null;
  onLogo: (id: string | null, url: string | null) => void;
  onError: (t: string) => void;
}) {
  const [uploading, setUploading] = useState(false);
  const upload = async (file: File | undefined) => {
    if (!file) return;
    if (!["image/png", "image/jpeg", "image/webp"].includes(file.type)) return onError("Use a PNG, JPEG or WebP logo.");
    if (file.size > 1024 * 1024) return onError("Logos must be 1 MB or smaller.");
    setUploading(true);
    try {
      const lg = await oc.uploadLogo(file);
      onLogo(lg.id, lg.url);
    } catch (e) {
      onError(errText(e));
    } finally {
      setUploading(false);
    }
  };
  return (
    <>
      <fieldset>
        <legend className="label">Button colour</legend>
        <div className="flex flex-wrap gap-2">
          {accents.map((a) => (
            <label key={a.value} className={`flex cursor-pointer items-center gap-2 rounded-full border px-3 py-1.5 text-sm ${draft.theme.accent === a.value ? "border-brand-secondary" : "border-line"}`}>
              <input type="radio" name="oc-accent" className="sr-only" checked={draft.theme.accent === a.value} onChange={() => change({ theme: { ...draft.theme, accent: a.value } })} />
              <span aria-hidden className="size-5 rounded-full" style={{ background: a.value }} />
              {a.label}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset>
        <legend className="label">Corners</legend>
        <div className="flex flex-wrap gap-2">
          {(["none", "small", "large"] as const).map((r) => (
            <label key={r} className={`cursor-pointer rounded-full border px-4 py-1.5 text-sm capitalize ${draft.theme.radius === r ? "border-brand-secondary bg-subtle" : "border-line"}`}>
              <input type="radio" name="oc-radius" className="sr-only" checked={draft.theme.radius === r} onChange={() => change({ theme: { ...draft.theme, radius: r } })} />
              {r === "none" ? "Square" : r === "small" ? "Slightly rounded" : "Rounded"}
            </label>
          ))}
        </div>
      </fieldset>
      <div>
        <span className="label">Logo</span>
        <div className="flex flex-wrap items-center gap-3">
          {logoUrl ? <img src={logoUrl} alt="Current logo" className="h-14 max-w-48 rounded-ctl border border-line bg-white object-contain p-1" /> : <span className="text-sm text-ink-muted">No logo</span>}
          <label className="btn-ghost cursor-pointer">
            <Icon name="upload" size={16} />{uploading ? "Uploading…" : "Upload approved logo"}
            <input type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" onChange={(e) => void upload(e.target.files?.[0])} />
          </label>
          {logoUrl && <button type="button" className="btn-ghost" onClick={() => onLogo(null, null)}>Remove</button>}
        </div>
        <p className="mt-1 text-sm text-ink-muted">PNG, JPEG or WebP, up to 1 MB. It is re-saved as a clean PNG; SVG is not accepted.</p>
      </div>
    </>
  );
}

/* ---------------------------------------------------------------- products */

function ProductsEditor({ draft, change }: { draft: DraftIn; change: (p: Partial<DraftIn>) => void }) {
  const [cats, setCats] = useState<Category[]>([]);
  const [prods, setProds] = useState<ProductSummary[]>([]);
  const [q, setQ] = useState("");
  useEffect(() => {
    api.categories().then(setCats, () => undefined);
    api.products({ active: true, limit: 1000 }).then((r) => setProds(r.items), () => undefined);
  }, []);
  const ids = new Set(draft.scope_ids);
  const toggle = (id: string) => change({ scope_ids: ids.has(id) ? draft.scope_ids.filter((x) => x !== id) : [...draft.scope_ids, id] });
  const shown = prods.filter((p) => !q.trim() || `${p.name} ${p.sku}`.toLowerCase().includes(q.trim().toLowerCase()));
  return (
    <>
      <fieldset>
        <legend className="label">Products customers can order</legend>
        <div className="flex flex-wrap gap-2">
          {([["ALL", "All active products"], ["CATEGORIES", "Chosen categories"], ["PRODUCTS", "Chosen products"]] as const).map(([v, l]) => (
            <label key={v} className={`cursor-pointer rounded-full border px-4 py-1.5 text-sm ${draft.product_scope === v ? "border-brand-secondary bg-subtle" : "border-line"}`}>
              <input type="radio" name="oc-scope" className="sr-only" checked={draft.product_scope === v} onChange={() => change({ product_scope: v, scope_ids: [] })} />
              {l}
            </label>
          ))}
        </div>
      </fieldset>
      {draft.product_scope === "CATEGORIES" && (
        <fieldset className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <legend className="sr-only">Categories</legend>
          {cats.filter((c) => c.active).map((c) => (
            <label key={c.id} className="flex cursor-pointer items-center gap-3 rounded-ctl border border-line px-3 py-2 text-sm">
              <input type="checkbox" className="size-5" checked={ids.has(c.id)} onChange={() => toggle(c.id)} />{c.name}
            </label>
          ))}
        </fieldset>
      )}
      {draft.product_scope === "PRODUCTS" && (
        <div className="space-y-2">
          <label className="relative block">
            <span className="sr-only">Search products</span>
            <input className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={`Search ${prods.length} products`} />
          </label>
          <p className="text-sm text-ink-muted">{draft.scope_ids.length} chosen</p>
          <fieldset className="max-h-80 space-y-1 overflow-y-auto rounded-ctl border border-line p-2">
            <legend className="sr-only">Products</legend>
            {shown.map((p) => (
              <label key={p.id} className="flex cursor-pointer items-center gap-3 rounded-ctl px-2 py-1.5 text-sm hover:bg-subtle">
                <input type="checkbox" className="size-5" checked={ids.has(p.id)} onChange={() => toggle(p.id)} />
                <span className="min-w-0 flex-1 truncate">{p.name}</span>
                <span className="text-xs text-ink-muted">{p.sku}</span>
              </label>
            ))}
          </fieldset>
        </div>
      )}
      <label className="flex cursor-pointer items-start gap-3 text-sm">
        <input type="checkbox" className="mt-0.5 size-5" checked={draft.show_prices} onChange={(e) => change({ show_prices: e.target.checked })} />
        <span>
          Show standard prices
          <span className="block text-ink-muted">Only the standard price book rate. Customer-specific prices, stock and costs are never shown. Customers can't send prices; the order is priced in Pro Trading.</span>
        </span>
      </label>
      <p className="text-sm text-ink-muted">Units come from each product's catalog setup. Inactive products never appear. The preview shows product changes after you save.</p>
    </>
  );
}

/* ---------------------------------------------------------------- live preview */

function Preview({ draft, logoUrl, catalog }: { draft: DraftIn; logoUrl: string | null; catalog: PreviewConfig | null }) {
  const [device, setDevice] = useState<"desktop" | "mobile">("desktop");
  const [step, setStep] = useState<"shop" | "checkout">("shop");
  const frame = useRef<HTMLIFrameElement>(null);
  const config = useMemo(() => {
    if (!catalog) return null;
    const base = catalog as unknown as { form: Record<string, unknown>; products: unknown[]; categories: unknown[] };
    return {
      ...base,
      form: { ...base.form, title: draft.title || "Untitled form", intro: draft.intro, success_message: draft.success_message, theme: draft.theme, logo_url: logoUrl, show_prices: draft.show_prices },
      fields: draft.fields,
    };
  }, [catalog, draft, logoUrl]);
  const post = useCallback(() => {
    if (config && frame.current?.contentWindow) frame.current.contentWindow.postMessage({ type: "oc-preview", config, step }, window.location.origin);
  }, [config, step]);
  useEffect(post, [post]);
  useEffect(() => {
    const onMsg = (e: MessageEvent) => {
      if (e.origin === window.location.origin && e.source === frame.current?.contentWindow && (e.data as { type?: string })?.type === "oc-preview-ready") post();
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, [post]);

  const seg = (on: boolean) => `chip cursor-pointer px-3.5 py-1.5 ${on ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:bg-subtle"}`;
  return (
    <section className="card min-w-0 p-5 sm:p-6" aria-label="Live preview">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <h2 className="mr-auto font-semibold">Live preview</h2>
        <div role="group" aria-label="Preview size" className="flex gap-1.5">
          <button type="button" className={seg(device === "desktop")} aria-pressed={device === "desktop"} onClick={() => setDevice("desktop")}>Desktop</button>
          <button type="button" className={seg(device === "mobile")} aria-pressed={device === "mobile"} onClick={() => setDevice("mobile")}>Mobile</button>
        </div>
        <label className="sr-only" htmlFor="oc-prev-step">Preview step</label>
        <select id="oc-prev-step" className="input w-auto py-1.5" value={step} onChange={(e) => setStep(e.target.value as typeof step)}>
          <option value="shop">Products and cart</option>
          <option value="checkout">Checkout</option>
        </select>
      </div>
      <div className="grid place-items-center overflow-hidden rounded-ctl bg-subtle p-3">
        <iframe
          ref={frame}
          title="Order form preview"
          src="/embed/preview"
          onLoad={post}
          className="h-[640px] rounded-ctl border border-line bg-white transition-[width] duration-200"
          style={{ width: device === "mobile" ? 390 : "100%", maxWidth: "100%" }}
        />
      </div>
      <p className="mt-2 text-sm text-ink-muted">This is how customers see the form. Nothing typed here is sent.</p>
    </section>
  );
}
