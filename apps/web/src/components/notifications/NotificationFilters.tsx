import { CATEGORIES, CATEGORY_LABEL, SEVERITIES, SEVERITY } from "../../lib/notifications";
import type { Category, Severity } from "../../lib/notificationsApi";
import { Icon } from "../../ui/Icon";

export type InboxFilter = { status: "unread" | "all" | "archived"; category: Category | ""; severity: Severity | ""; q: string };
const NO_FILTER: InboxFilter = { status: "all", category: "", severity: "", q: "" };

const STATUS = [
  { value: "all", label: "All" },
  { value: "unread", label: "Unread" },
  { value: "archived", label: "Archived" },
] as const;

/** Status chips, priority and category pickers, and a search box (focus with "/"). */
export function NotificationFilters({ value, onChange }: { value: InboxFilter; onChange: (f: InboxFilter) => void }) {
  const set = (p: Partial<InboxFilter>) => onChange({ ...value, ...p });
  const active = value.category || value.severity || value.q || value.status !== "all";
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Status">
        {STATUS.map((o) => (
          <button
            key={o.value}
            type="button"
            aria-pressed={value.status === o.value}
            onClick={() => set({ status: o.value })}
            className={`chip cursor-pointer px-3.5 py-1.5 text-[13px] transition-colors duration-150 ${value.status === o.value ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:border-brand-gray/60 hover:bg-subtle"}`}
          >
            {o.label}
          </button>
        ))}
      </div>
      <div className="flex w-full min-w-0 flex-wrap items-center gap-2 lg:ml-auto lg:w-auto lg:flex-nowrap">
        <label className="min-w-0 flex-1 lg:flex-none">
          <span className="sr-only">Priority</span>
          <select className="input w-full rounded-full py-2 lg:w-40" value={value.severity} onChange={(e) => set({ severity: e.target.value as Severity | "" })}>
            <option value="">Any priority</option>
            {SEVERITIES.slice()
              .reverse()
              .map((s) => (
                <option key={s} value={s}>
                  {SEVERITY[s].label}
                </option>
              ))}
          </select>
        </label>
        <label className="min-w-0 flex-1 lg:flex-none">
          <span className="sr-only">Category</span>
          <select className="input w-full rounded-full py-2 lg:w-52" value={value.category} onChange={(e) => set({ category: e.target.value as Category | "" })}>
            <option value="">All categories</option>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {CATEGORY_LABEL[c]}
              </option>
            ))}
          </select>
        </label>
        <label className="relative w-full min-w-0 sm:flex-1 lg:w-auto lg:flex-none">
          <span className="sr-only">Search notifications</span>
          <Icon name="search" size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-ink-muted" />
          <input data-page-search className="input w-full rounded-full py-2 pl-10 lg:w-64" type="search" value={value.q} maxLength={100} placeholder="Search notifications" onChange={(e) => set({ q: e.target.value })} />
        </label>
        {active && (
          <button type="button" className="cursor-pointer rounded-full px-3 py-2 text-sm font-medium text-ink-muted hover:bg-card hover:text-ink" onClick={() => onChange(NO_FILTER)}>
            Clear filters
          </button>
        )}
      </div>
    </div>
  );
}
