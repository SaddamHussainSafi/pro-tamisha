import { useCallback, useEffect, useState } from "react";
import { api, type RoleMatrix } from "../../api";
import { groupPermissions } from "../../lib/staff";
import { Icon } from "../../ui/Icon";
import { CardTitle, ErrorState, LoadingRows } from "../../ui/primitives";

/** Roles & permissions: who can do what. Read-only: access changes by assigning a role. */
export function Roles() {
  const [m, setM] = useState<RoleMatrix | null>(null);
  const [failed, setFailed] = useState(false);
  const load = useCallback(async () => {
    try {
      setM(await api.roleMatrix());
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);
  useEffect(() => void load(), [load]);

  if (failed) return <ErrorState title="Couldn't load roles" onRetry={() => void load()} />;
  if (!m)
    return (
      <div className="card p-6">
        <LoadingRows cols={6} />
      </div>
    );
  const groups = groupPermissions(m.permissions);

  return (
    <div className="space-y-5">
      <section aria-labelledby="roles-title" className="space-y-4">
        <h2 id="roles-title" className="sr-only">
          Roles
        </h2>
        <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3" aria-label="Roles">
          {m.roles.map((r) => (
            <li key={r.code} className="card flex flex-col gap-2 p-6">
              <div className="flex items-baseline justify-between gap-2">
                <h3 className="text-base font-semibold">{r.name}</h3>
                <span className="text-sm text-ink-muted">
                  {r.members} {r.members === 1 ? "person" : "people"}
                </span>
              </div>
              <p className="text-sm text-ink-muted">{r.description}</p>
            </li>
          ))}
        </ul>
      </section>
      <section className="card space-y-4 p-6" aria-labelledby="matrix-title">
        <CardTitle id="matrix-title" title="Permission matrix" />
        <p className="text-sm text-ink-muted">Roles are fixed. To change what someone can do, give them another role in User management. Only a Super Admin can do that.</p>
        <div className="relative -mx-6 overflow-x-auto px-6" tabIndex={0} role="region" aria-label="Permission matrix, scrolls sideways on small screens">
          <table className="w-full min-w-[46rem] border-separate border-spacing-0 text-sm">
            <caption className="sr-only">Which role holds which permission</caption>
            <thead>
              <tr>
                <th scope="col" className="th sticky left-0 z-20 min-w-[16rem]">
                  Permission
                </th>
                {m.roles.map((r) => (
                  <th key={r.code} scope="col" className="th text-center">
                    {r.name}
                  </th>
                ))}
              </tr>
            </thead>
            {groups.map((g) => (
              <tbody key={g.area}>
                <tr>
                  <th scope="colgroup" colSpan={m.roles.length + 1} className="sticky left-0 border-b border-line bg-card px-5 pt-5 pb-2 text-left text-sm font-semibold">
                    {g.area}
                  </th>
                </tr>
                {g.items.map((p) => (
                  <tr key={p.code}>
                    <th scope="row" className="td sticky left-0 z-10 bg-card text-left font-normal">
                      {p.description}
                    </th>
                    {m.roles.map((r) => {
                      const yes = r.permissions.includes(p.code);
                      return (
                        <td key={r.code} className="td text-center">
                          {yes ? (
                            <span className="inline-grid size-6 place-items-center rounded-full bg-brand-accent/15 text-brand-primary">
                              <Icon name="check" size={14} label={`${r.name}: yes`} />
                            </span>
                          ) : (
                            <span className="text-ink-muted">
                              <span aria-hidden>—</span>
                              <span className="sr-only">{r.name}: no</span>
                            </span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            ))}
          </table>
        </div>
      </section>
    </div>
  );
}
