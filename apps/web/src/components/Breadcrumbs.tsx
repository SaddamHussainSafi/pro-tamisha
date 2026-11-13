import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { crumbsFor, headingLabel } from "../lib/breadcrumbs";
import { Icon } from "../ui/Icon";

const link = "truncate rounded-sm text-ink-muted underline-offset-4 transition-colors duration-150 hover:text-ink hover:underline";

const SIZE = {
  // Wide screens: one row above the page title, lined up with its left edge, in the top bar.
  header: { nav: "hidden min-w-0 flex-1 text-[17px] lg:block", list: "h-12 w-fit max-w-full gap-2.5 rounded-full bg-card px-6 shadow-[0_1px_2px_rgb(24_24_27/0.04)]", sep: 16, item: "max-w-[16rem]" },
  // Phones and tablets: the top bar is full, so the row sits at the top of the page.
  page: { nav: "mb-3 min-w-0 text-[15px] lg:hidden", list: "", sep: 14, item: "max-w-[14rem]" },
} as const;

/**
 * Global breadcrumbs, with the menu's own labels (Sales › Sales Invoices › New). Built from the
 * address and nav.ts; a record's name comes from the page's own heading once it loads. A section
 * without a page of its own is plain text; the current page is plain text with aria-current.
 * Phones keep the parent and the current page; the earlier steps fold into "…".
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

  // On a phone a single step would only repeat the page title right below it.
  if (crumbs.length === 0 || (place === "page" && crumbs.length < 2)) return null;
  const last = crumbs.length - 1;
  const folded = crumbs.length > 2;
  return (
    <nav aria-label="Breadcrumb" className={z.nav}>
      <ol className={`flex min-w-0 items-center gap-2 ${z.list}`}>
        {folded && (
          <li className="flex shrink-0 items-center gap-2 sm:hidden" aria-hidden>
            <span className="text-ink-muted">…</span>
          </li>
        )}
        {crumbs.map((c, i) => {
          const current = i === last;
          const text = c.label ?? heading ?? c.fallback ?? "Details";
          // On phones only the parent and the current page stay.
          const early = i < last - 1;
          return (
            <li key={`${c.to ?? c.label}-${i}`} className={`${early ? "hidden sm:flex" : "flex"} min-w-0 items-center gap-2 ${current ? "flex-1" : `shrink-0 ${z.item}`}`}>
              {/* the separator before the first visible step: after "…" on phones, none at the very start */}
              {i > 0 && <span className={i === last - 1 && folded ? "flex" : early || i === 1 ? "hidden sm:flex" : "flex"}>{sep}</span>}
              {current ? (
                <span aria-current="page" className="truncate font-semibold text-ink" title={text}>
                  {text}
                </span>
              ) : c.to ? (
                <Link to={c.to} className={link} title={text}>
                  {text}
                </Link>
              ) : (
                <span className="truncate text-ink-muted">{text}</span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
