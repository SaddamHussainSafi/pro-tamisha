import type { ViewMode } from "../lib/viewMode";
import { Icon } from "./Icon";

export function ViewToggle({ value, onChange }: { value: ViewMode; onChange: (v: ViewMode) => void }) {
  const opts = [
    { v: "grid" as const, label: "Grid", icon: "overview" as const },
    { v: "list" as const, label: "List", icon: "menu" as const },
  ];
  return (
    <div role="group" aria-label="View" className="inline-flex rounded-full border border-line bg-card p-1">
      {opts.map((o) => (
        <button
          key={o.v}
          type="button"
          aria-pressed={value === o.v}
          onClick={() => onChange(o.v)}
          className={`inline-flex cursor-pointer items-center gap-1.5 rounded-full px-3.5 py-1.5 text-[13px] font-medium transition-colors ${value === o.v ? "bg-brand-secondary text-white" : "text-ink hover:bg-subtle"}`}
        >
          <Icon name={o.icon} size={15} />
          {o.label}
        </button>
      ))}
    </div>
  );
}
