import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { isAppPath, searchApi, typeIcon, type SearchPreview as Preview } from "../../lib/search";
import { Icon } from "../../ui/Icon";

/** Lazy preview of one result: key fields, related records (real links only) and actions. */
export function SearchPreview({ type, id, onOpen }: { type: string; id: string; onOpen: (url: string, type: string, id: string) => void }) {
  const [state, setState] = useState<{ key: string; data: Preview | null; error: boolean }>({ key: "", data: null, error: false });
  const key = `${type}:${id}`;
  useEffect(() => {
    let live = true;
    // Small delay: arrowing quickly through results does not fire a preview per row.
    const t = setTimeout(() => {
      searchApi.preview(type, id).then(
        (data) => live && setState({ key, data, error: false }),
        () => live && setState({ key, data: null, error: true }),
      );
    }, 120);
    return () => {
      live = false;
      clearTimeout(t);
    };
  }, [type, id, key]);

  const pv = state.key === key ? state.data : null;
  if (state.key === key && state.error) return <p className="p-5 text-sm text-ink-muted">Preview not available.</p>;
  if (!pv)
    return (
      <div className="space-y-3 p-5" role="status" aria-label="Loading preview">
        <div className="skeleton h-5 w-2/3" />
        <div className="skeleton h-3 w-1/2" />
        <div className="skeleton h-3 w-3/4" />
        <div className="skeleton h-3 w-2/5" />
      </div>
    );
  return (
    <div className="space-y-4 p-5" aria-live="polite">
      <div className="flex items-start gap-3">
        <span aria-hidden className="grid size-10 shrink-0 place-items-center rounded-full bg-brand-accent/12 text-brand-primary">
          <Icon name={typeIcon(pv.type)} size={20} />
        </span>
        <div className="min-w-0">
          <h3 className="break-words text-base font-semibold leading-tight">{pv.title}</h3>
          {pv.subtitle && <p className="mt-0.5 text-sm text-ink-muted">{pv.subtitle}</p>}
        </div>
      </div>
      {pv.fields.length > 0 && (
        <dl className="grid grid-cols-[minmax(0,auto)_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm">
          {pv.fields.map((f) => (
            <div key={f.label} className="contents">
              <dt className="text-ink-muted">{f.label}</dt>
              <dd className="num min-w-0 break-words">{f.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {pv.actions.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {pv.actions.map((a) =>
            a.kind === "download" ? (
              <a key={a.href} className="btn-ghost px-3.5 py-1.5" href={a.href} target="_blank" rel="noopener">
                <Icon name="download" size={16} /> {a.label}
              </a>
            ) : isAppPath(a.href) ? (
              <Link
                key={a.href}
                className="btn-ghost px-3.5 py-1.5"
                to={a.href}
                onClick={(e) => {
                  e.preventDefault();
                  onOpen(a.href, pv.type, pv.id);
                }}
              >
                <Icon name="arrowUpRight" size={16} /> {a.label}
              </Link>
            ) : null,
          )}
        </div>
      )}
      {pv.related.length > 0 && (
        <section aria-label="Related records">
          <h4 className="kicker mb-1.5">Related</h4>
          <ul className="space-y-0.5">
            {pv.related.map((r) => (
              <li key={`${r.type}:${r.id}`}>
                <Link
                  to={r.url}
                  onClick={(e) => {
                    e.preventDefault();
                    onOpen(r.url, r.type, r.id);
                  }}
                  className="flex items-center gap-2.5 rounded-ctl px-2 py-1.5 text-sm hover:bg-subtle"
                >
                  <Icon name={typeIcon(r.type)} size={16} className="shrink-0 text-ink-muted" />
                  <span className="min-w-0 flex-1 truncate font-medium">{r.title}</span>
                  <span className="truncate text-xs text-ink-muted">{r.subtitle}</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}
