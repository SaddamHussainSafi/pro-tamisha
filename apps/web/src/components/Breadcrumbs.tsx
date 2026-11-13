import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { crumbsFor, headingLabel } from "../lib/breadcrumbs";
import { Icon } from "../ui/Icon";


const link = "truncate rounded-sm text-ink-muted underline-offset-4 transition-colors duration-150 hover:text-ink hover:underline";

const SIZE = {
  // In the top bar on wide screens: the page's place in the app, at reading size.
  header: { nav: "hidden min-w-0 flex-1 text-[17px] lg:block", list: "h-12 w-fit max-w-full rounded-full bg-card pl-1 pr-5", home: "size-10", homeIcon: 20, sep: 16, item: "max-w-[16rem]" },
  // Phones and tablets: the top bar is full, so it sits at the top of the page.
  page: { nav: "mb-3 min-w-0 text-[15px] lg:hidden", list: "", home: "size-8", homeIcon: 17, sep: 14, item: "max-w-[14rem]" },
} as const;

/**
 * Global breadcrumbs (Home › Section › Record) for every screen below the first level: in the
 * top bar on wide screens, at the top of the page on phones and tablets.
 * Built from the address; a record's name comes from the page's own heading once it loads.
 * Phones keep Home, the parent and the current page; the middle steps fold into "…".
 */
export function Breadcrumbs({ place }: { place: keyof typeof SIZE }) {
  const z = SIZE[place];
  const sep = <Icon name="chevron" size={z.sep} className="text-ink-muted/60" />;
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
    <nav aria-label="Breadcrumb" className={z.nav}>
      <ol className={`flex min-w-0 items-center gap-2 ${z.list}`}>
        <li className="flex shrink-0 items-center">
          <Link to="/" className={`grid ${z.home} place-items-center rounded-full text-ink-muted transition-colors duration-150 hover:bg-subtle hover:text-ink`} aria-label="Overview" title="Overview">
            <Icon name="home" size={z.homeIcon} />
          </Link>
        </li>
        {folded && (
          <li className="flex shrink-0 items-center gap-2 sm:hidden" aria-hidden>
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
            <li key={`${c.to}-${i}`} className={`${hideOnPhone} min-w-0 items-center gap-2 ${current ? "flex-1" : `shrink-0 ${z.item}`}`}>
              {sep}
              {current ? (
                <span aria-current="page" className="truncate font-semibold text-ink" title={text}>
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
