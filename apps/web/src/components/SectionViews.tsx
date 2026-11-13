import { Link, useLocation } from "react-router-dom";
import { locate } from "../nav";

/**
 * Sub-screens of a menu entry (Products · Price book, Suppliers · Supplier payments) as a row
 * of links above the page. Shown on those screens only, not on a record's own page.
 */
export function SectionViews() {
  const loc = useLocation();
  const leaf = locate(loc.pathname, loc.search)?.leaf;
  const views = leaf?.views;
  if (!leaf || !views || !views.some((v) => v.to === loc.pathname)) return null;
  return (
    <nav aria-label={`${leaf.label} pages`} className="mb-4 flex flex-wrap gap-1.5">
      {views.map((v) => {
        const on = v.to === loc.pathname;
        return (
          <Link key={v.to} to={v.to} aria-current={on ? "page" : undefined} className={`chip px-4 py-2 text-sm ${on ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:bg-subtle"}`}>
            {v.label}
          </Link>
        );
      })}
    </nav>
  );
}
