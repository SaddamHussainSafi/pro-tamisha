import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import { ENTITY_LABEL, mail, mailError, type EntityType, type LinkTarget, type MailLink } from "../../lib/mail";
import { useDebounced } from "../../lib/useDebounced";
import { Icon } from "../../ui/Icon";

const TYPES = Object.keys(ENTITY_LABEL) as EntityType[];

/** ERP records this email is connected to. Linking changes nothing in the record itself. */
export function LinksPanel({ messageId, links, onChange }: { messageId: string; links: MailLink[]; onChange: (l: MailLink[]) => void }) {
  const [adding, setAdding] = useState(false);
  const [type, setType] = useState<EntityType>("customer");
  const [q, setQ] = useState("");
  const dq = useDebounced(q.trim(), 250);
  const [hits, setHits] = useState<LinkTarget[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const id = useId();

  useEffect(() => {
    if (!adding || !dq) return setHits(null);
    let live = true;
    mail.linkTargets(type, dq).then(
      (r) => {
        if (!live) return;
        setHits(r);
        setError(null);
      },
      (err: unknown) => {
        if (!live) return;
        setHits([]);
        setError(mailError(err));
      },
    );
    return () => {
      live = false;
    };
  }, [adding, type, dq]);

  async function add(t: LinkTarget) {
    try {
      const l = await mail.link(messageId, { entity_type: t.entity_type, entity_id: t.entity_id });
      onChange([...links.filter((x) => x.id !== l.id), l]);
      setAdding(false);
      setQ("");
    } catch (err) {
      setError(mailError(err));
    }
  }
  async function remove(l: MailLink) {
    try {
      await mail.unlink(l.id);
      onChange(links.filter((x) => x.id !== l.id));
    } catch (err) {
      setError(mailError(err));
    }
  }

  return (
    <section aria-label="Linked records" className="space-y-2 print:hidden">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold">Linked records</h3>
        {!adding && (
          <button type="button" className="ml-auto inline-flex cursor-pointer items-center gap-1 text-sm font-medium text-brand-primary-hover hover:underline" onClick={() => setAdding(true)}>
            <Icon name="plus" size={14} />
            Link to a record
          </button>
        )}
      </div>
      {links.length === 0 && !adding && <p className="text-sm text-ink-muted">Not linked to a customer, supplier, order or invoice yet.</p>}
      {links.length > 0 && (
        <ul className="flex flex-wrap gap-2">
          {links.map((l) => (
            <li key={l.id} className="chip border-line bg-card py-1 pr-1 text-ink">
              <span className="text-ink-muted">{ENTITY_LABEL[l.entity_type] ?? l.entity_type}</span>
              {l.url && l.url !== "#" ? (
                <Link to={l.url} className="font-medium text-brand-primary-hover hover:underline">
                  {l.label}
                </Link>
              ) : (
                <span>{l.label}</span>
              )}
              <button type="button" className="grid size-5 cursor-pointer place-items-center rounded-full hover:bg-line" aria-label={`Unlink ${l.label}`} title="Unlink (the record is not changed)" onClick={() => void remove(l)}>
                <Icon name="x" size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {adding && (
        <div className="space-y-2 rounded-[var(--radius-ctl)] border border-line p-3">
          <div className="flex flex-wrap gap-2">
            <label className="sr-only" htmlFor={`${id}-t`}>
              Record type
            </label>
            <select id={`${id}-t`} className="input w-auto py-1.5" value={type} onChange={(e) => setType(e.target.value as EntityType)}>
              {TYPES.map((t) => (
                <option key={t} value={t}>
                  {ENTITY_LABEL[t]}
                </option>
              ))}
            </select>
            <label className="sr-only" htmlFor={`${id}-q`}>
              Find the record
            </label>
            <input id={`${id}-q`} className="input min-w-40 flex-1 py-1.5" placeholder="Name or number" value={q} autoFocus onChange={(e) => setQ(e.target.value)} />
            <button type="button" className="btn-ghost px-4 py-1.5" onClick={() => {
                setAdding(false);
                setQ("");
              }}>
              Cancel
            </button>
          </div>
          {hits && hits.length === 0 && !error && <p className="text-sm text-ink-muted">Nothing found.</p>}
          {hits && hits.length > 0 && (
            <ul className="divide-y divide-line" aria-label="Matching records">
              {hits.map((h) => (
                <li key={h.entity_id}>
                  <button type="button" className="flex w-full cursor-pointer items-center gap-2 px-1 py-2 text-left text-sm hover:bg-subtle" onClick={() => void add(h)}>
                    <Icon name="plus" size={14} className="text-ink-muted" />
                    {h.label}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
    </section>
  );
}
