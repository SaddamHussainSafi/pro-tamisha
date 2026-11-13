import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { crumbsFor, headingLabel } from "../lib/breadcrumbs";
import { Icon } from "../ui/Icon";

const sep = <Icon name="chevron" size={14} className="text-ink-muted/60" />;
const link = "truncate rounded-sm text-ink-muted underline-offset-4 transition-colors duration-150 hover:text-ink hover:underline";

/**
 * Global breadcrumbs (Home › Section › Record), shown above every screen below the first level.
 * Built from the address; a record's name comes from the page's own heading once it loads.
 * Phones keep Home, the parent and the current page; the middle steps fold into "…".
 */
export function Breadcrumbs() {
  const loc = useLocation();
  const crumbs = crumbsFor(loc.pathname, loc.search);
  const [heading, setHeading] = useState<string | null>(null);
  const needsHeading = crumbs.at(-1)?.label === null;

  useEffect(() => {
    if (!needsHeading) return;
    const main = document.getElementById("main");
    if (!main) return;
    const read = () => setHeading(headingLabel(main.querySelector("h1")?.textContent));
    read();
    const watch = new MutationObserver(read);
    watch.observe(main, { childList: true, subtree: true, characterData: true });
    return () => {
      watch.disconnect();
      setHeading(null);
    };
  }, [needsHeading, loc.pathname]);

  if (crumbs.length < 2) return null; // first-level screens: the heading says it all
  const last = crumbs.length - 1;
  const folded = crumbs.length > 2;
  return (
    <nav aria-label="Breadcrumb" className="mb-3 min-w-0 text-sm sm:mb-4">
      <ol className="flex min-w-0 items-center gap-1.5">
        <li className="flex shrink-0 items-center">
          <Link to="/" className="grid size-7 place-items-center rounded-full text-ink-muted transition-colors duration-150 hover:bg-card hover:text-ink" aria-label="Overview" title="Overview">
            <Icon name="home" size={16} />
          </Link>
        </li>
        {folded && (
          <li className="flex shrink-0 items-center gap-1.5 sm:hidden" aria-hidden>
            {sep}
            <span className="text-ink-muted">…</span>
          </li>
        )}
        {crumbs.map((c, i) => {
          const current = i === last;
          const text = c.label ?? heading ?? c.fallback ?? "Details";
          // On phones only the parent and the current page stay.
          const hideOnPhone = i < last - 1 ? "hidden sm:flex" : "flex";
          return (
            <li key={`${c.to}-${i}`} className={`${hideOnPhone} min-w-0 items-center gap-1.5 ${current ? "flex-1" : "shrink-0 max-w-[14rem]"}`}>
              {sep}
              {current ? (
                <span aria-current="page" className="truncate font-medium text-ink" title={text}>
                  {text}
                </span>
              ) : (
                <Link to={c.to} className={link} title={text}>
                  {text}
                </Link>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
