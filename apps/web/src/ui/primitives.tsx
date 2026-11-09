import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { Icon, type IconName } from "./Icon";

/* ---------- StatusChip: colour + icon + text (never colour alone) ---------- */
export type Tone = "success" | "warning" | "danger" | "info" | "neutral";
const TONE: Record<Tone, { cls: string; icon: IconName }> = {
  success: { cls: "border-transparent bg-success/12 text-ink [&_svg]:text-success", icon: "check" },
  warning: { cls: "border-transparent bg-warning/12 text-ink [&_svg]:text-warning", icon: "clock" },
  danger: { cls: "border-transparent bg-danger/10 text-danger", icon: "alert" },
  info: { cls: "border-transparent bg-info/10 text-info", icon: "info" },
  neutral: { cls: "border-transparent bg-subtle text-ink-muted", icon: "file" },
};

export function StatusChip({ tone, children }: { tone: Tone; children: ReactNode }) {
  const t = TONE[tone];
  return (
    <span className={`chip ${t.cls}`}>
      <Icon name={t.icon} size={14} />
      {children}
    </span>
  );
}

/* ---------- SampleBanner: every mock screen must say so ---------- */
export function SampleBanner({ children }: { children?: ReactNode }) {
  return (
    <div role="note" className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-ctl bg-brand-accent/10 px-4 py-2.5 text-sm text-ink">
      <Icon name="eye" size={18} className="text-brand-primary" />
      <strong>Design preview · sample data.</strong>
      <span>Nothing here is saved.</span>
      {children}
    </div>
  );
}

/* ---------- PageHeader ---------- */
// One title, one muted line, and the screen's actions on the right (the primary one last).
export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-4">
      <div className="min-w-0 max-w-3xl">
        <h1 className="text-[28px] font-semibold leading-tight tracking-tight sm:text-[34px]">{title}</h1>
        {subtitle && <p className="mt-1 text-base text-ink-muted">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2.5">{actions}</div>}
    </div>
  );
}

/* ---------- Card title row with the round "open" arrow from the references ---------- */
export function CardTitle({ id, title, to, action }: { id?: string; title: ReactNode; to?: string; action?: ReactNode }) {
  return (
    // The title keeps at least 10rem; wide action rows (filters, CSV) drop below it on phones.
    <div className="flex flex-wrap items-center gap-3">
      <h2 id={id} className="min-w-[10rem] flex-1 text-xl font-semibold tracking-tight">{title}</h2>
      <div className="ml-auto flex flex-wrap items-center gap-2">
        {action}
        {to && (
          <Link to={to} className="icon-btn size-12" aria-label={`Open ${typeof title === "string" ? title : "details"}`}>
            <Icon name="arrowUpRight" size={22} />
          </Link>
        )}
      </div>
    </div>
  );
}

/* ---------- Trend: arrow + text, coloured by direction ---------- */
export function Trend({ dir, children }: { dir: "up" | "down"; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1 text-sm font-semibold ${dir === "up" ? "text-success" : "text-danger"}`}>
      <Icon name={dir === "up" ? "trendUp" : "trendDown"} size={14} />
      {children}
    </span>
  );
}

/* ---------- KPI card: big number, one short label, optional icon and plain-text note ---------- */
// Notes are text, not chips: only problems get a colour.
const NOTE_TEXT: Record<Tone, string> = {
  success: "text-ink-muted",
  info: "text-ink-muted",
  neutral: "text-ink-muted",
  warning: "font-medium text-warning",
  danger: "font-medium text-danger",
};
export function KpiCard({
  label,
  value,
  unit,
  note,
  tone,
  icon,
  accent = false,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  note?: ReactNode;
  tone?: Tone;
  /** Omit when every card in the row would show the same icon. */
  icon?: IconName;
  accent?: boolean;
}) {
  if (accent)
    return (
      <div className={`flex flex-col justify-between gap-4 rounded-card bg-brand-primary p-6 text-white sm:gap-6 ${icon ? "sm:min-h-[200px]" : ""}`}>
        {/* The icon tile shows from sm up; phones get a shorter card (note on top, then label + figure). */}
        {icon && (
          <div className={`items-start justify-between gap-3 ${note ? "flex" : "hidden sm:flex"}`}>
            <span className="hidden size-14 place-items-center rounded-ctl bg-white/15 sm:grid"><Icon name={icon} size={28} /></span>
            {note && <span className="ml-auto pt-1 text-right text-sm font-medium text-white">{note}</span>}
          </div>
        )}
        <div className="min-w-0">
          <div className="text-base font-medium">{label}</div>
          <div className="num mt-1.5 flex items-baseline gap-1.5 whitespace-nowrap text-[clamp(1.75rem,2.2vw,2.5rem)] font-semibold leading-none tracking-tight">
            {value}
            {unit && <span className="text-lg font-medium">{unit}</span>}
          </div>
          {!icon && note && <div className="mt-2 text-sm font-medium text-white">{note}</div>}
        </div>
      </div>
    );
  const noteEl = note && <span className={`text-sm ${NOTE_TEXT[tone ?? "neutral"]}`}>{note}</span>;
  return (
    <div className={`card flex flex-col justify-between gap-4 p-6 sm:gap-6 ${icon ? "sm:min-h-[200px]" : ""}`}>
      {icon && (
        <div className={`items-start justify-between gap-3 ${noteEl ? "flex" : "hidden sm:flex"}`}>
          <span className="hidden size-14 place-items-center rounded-ctl bg-brand-accent/10 text-brand-primary sm:grid"><Icon name={icon} size={28} /></span>
          {noteEl && <span className="ml-auto pt-1 text-right">{noteEl}</span>}
        </div>
      )}
      <div className="min-w-0">
        <div className="text-base font-medium text-ink-muted">{label}</div>
        <div className="num mt-1.5 flex items-baseline gap-1.5 whitespace-nowrap text-[clamp(1.75rem,2.2vw,2.5rem)] font-semibold leading-none tracking-tight">
          {value}
          {unit && <span className="text-lg font-medium text-ink-muted">{unit}</span>}
        </div>
        {!icon && noteEl && <div className="mt-2">{noteEl}</div>}
      </div>
    </div>
  );
}

/* ---------- Compact stat (icon tile + label + figure) ---------- */
export function Stat({ icon, label, value, note }: { icon: IconName; label: string; value: ReactNode; note?: ReactNode }) {
  return (
    <div className="flex min-w-0 items-center gap-4">
      <span className="grid size-16 shrink-0 place-items-center rounded-ctl bg-card text-brand-primary"><Icon name={icon} size={28} /></span>
      <div className="min-w-0">
        <div className="text-sm text-ink-muted">{label}</div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="num truncate text-[28px] font-semibold tracking-tight">{value}</span>
          {note}
        </div>
      </div>
    </div>
  );
}

/* ---------- Bar chart with striped bars and one highlighted bar ---------- */
export type Bar = { label: string; value: number; display: string };
export function BarChart({ bars, highlight, ariaLabel, axis }: { bars: Bar[]; highlight: number; ariaLabel: string; axis: (v: number) => string }) {
  const max = Math.max(1, ...bars.map((b) => b.value));
  // Four whole, "nice" steps. Quarter steps of the max printed "$1 $1 $1 $0" with no data
  // and ticks like 11.25 for odd maxima.
  const step = Math.max(1, niceStep(max / 4));
  const top = step * 4;
  const ticks = [4, 3, 2, 1, 0].map((i) => step * i);
  const avg = bars.length ? bars.reduce((s, b) => s + b.value, 0) / bars.length : 0;
  return (
    <figure className="m-0">
      <div className="flex h-[22rem] gap-4 pt-16" role="img" aria-label={ariaLabel}>
        <div className="flex flex-col justify-between pb-9 text-right text-sm text-ink-muted" aria-hidden>
          {ticks.map((t) => <span key={t} className="num leading-none">{axis(t)}</span>)}
        </div>
        <div className="relative flex min-w-0 flex-1 gap-2.5 sm:gap-5">
          {avg > 0 && (
            <div aria-hidden className="pointer-events-none absolute inset-x-0 border-t-2 border-dashed border-brand-accent/70" style={{ bottom: `calc(36px + (100% - 36px) * ${avg / top})` }} />
          )}
          {bars.map((b, i) => {
            const on = i === highlight;
            return (
              <div key={b.label} className="flex h-full min-w-0 flex-1 flex-col items-center gap-2.5">
                <div className="relative flex min-h-0 w-full flex-1 items-end justify-center">
                  {on && (
                    <div aria-hidden className="absolute z-10 -translate-y-3 whitespace-nowrap rounded-ctl border border-line bg-card px-4 py-2 text-center" style={{ bottom: `${(b.value / top) * 100}%` }}>
                      <div className="text-xs text-ink-muted">{b.label}</div>
                      <div className="num text-base font-semibold">{b.display}</div>
                    </div>
                  )}
                  <div className={`w-full max-w-16 rounded-ctl ${on ? "hatch-accent" : "hatch"}`} style={{ height: `${Math.max(2, (b.value / top) * 100)}%` }} title={`${b.label}: ${b.display}`} />
                </div>
                <span className={`h-[26px] rounded-full px-2.5 text-sm leading-[26px] ${on ? "bg-brand-accent/15 font-semibold text-brand-primary-hover" : "text-ink-muted"}`}>{b.label}</span>
              </div>
            );
          })}
        </div>
      </div>
      <table className="sr-only">
        <caption>{ariaLabel}</caption>
        <tbody>{bars.map((b) => <tr key={b.label}><th scope="row">{b.label}</th><td>{b.display}</td></tr>)}</tbody>
      </table>
    </figure>
  );
}

function niceStep(raw: number) {
  if (raw <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(raw));
  const n = raw / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

/* ---------- Donut: part-to-whole for up to 4 groups, legend always shown ---------- */
const SERIES = ["var(--color-series-1)", "var(--color-series-2)", "var(--color-series-3)", "var(--color-series-4)"] as const;
export type Slice = { label: string; value: number };
export function DonutChart({ slices, centerLabel, ariaLabel }: { slices: Slice[]; centerLabel: string; ariaLabel: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const total = slices.reduce((s, x) => s + x.value, 0);
  const size = 208;
  const stroke = 28;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const live = slices.filter((s) => s.value > 0).length;
  const gap = live > 1 ? 4 : 0;
  let offset = 0;
  const focus = hover !== null ? slices[hover] : null;
  return (
    <figure className="m-0 flex flex-col items-center gap-6 sm:flex-row sm:items-center sm:justify-center xl:flex-col 2xl:flex-row">
      <div className="relative shrink-0" style={{ width: size, height: size }} role="img" aria-label={ariaLabel}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-90" aria-hidden>
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--color-subtle)" strokeWidth={stroke} />
          {total > 0 &&
            slices.map((s, i) => {
              if (s.value <= 0) return null;
              const len = (s.value / total) * c;
              const dash = Math.max(0.5, len - gap);
              const el = (
                <circle
                  key={s.label}
                  cx={size / 2}
                  cy={size / 2}
                  r={r}
                  fill="none"
                  stroke={SERIES[i % SERIES.length]}
                  strokeWidth={hover === i ? stroke + 6 : stroke}
                  strokeDasharray={`${dash} ${c - dash}`}
                  strokeDashoffset={-offset}
                  className="cursor-pointer"
                  onMouseEnter={() => setHover(i)}
                  onMouseLeave={() => setHover(null)}
                >
                  <title>{`${s.label}: ${s.value}`}</title>
                </circle>
              );
              offset += len;
              return el;
            })}
        </svg>
        <div className="pointer-events-none absolute inset-0 grid place-content-center text-center" aria-hidden>
          <div className="num text-[38px] font-semibold leading-none tracking-tight">{focus ? focus.value : total}</div>
          <div className="mt-1.5 text-sm text-ink-muted">{focus ? focus.label : centerLabel}</div>
        </div>
      </div>
      <ul className="w-full min-w-0 max-w-xs space-y-1">
        {slices.map((s, i) => (
          <li
            key={s.label}
            className={`flex items-center gap-3 rounded-ctl px-3 py-2.5 ${hover === i ? "bg-subtle" : ""}`}
            onMouseEnter={() => setHover(i)}
            onMouseLeave={() => setHover(null)}
          >
            <span aria-hidden className="size-3.5 shrink-0 rounded-full" style={{ background: SERIES[i % SERIES.length] }} />
            <span className="min-w-0 flex-1 truncate text-sm font-medium">{s.label}</span>
            <span className="num text-sm font-semibold">{s.value}</span>
            <span className="num w-11 text-right text-sm text-ink-muted">{total ? Math.round((s.value / total) * 100) : 0}%</span>
          </li>
        ))}
      </ul>
      <table className="sr-only">
        <caption>{ariaLabel}</caption>
        <tbody>{slices.map((s) => <tr key={s.label}><th scope="row">{s.label}</th><td>{s.value}</td></tr>)}</tbody>
      </table>
    </figure>
  );
}

/* ---------- Progress: brand-green fill on a striped track ---------- */
export function Progress({ label, value, max, display }: { label: string; value: number; max: number; display: string }) {
  const pct = max > 0 ? Math.min(100, (value / max) * 100) : 0;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm text-ink-muted">{label}</span>
        <span className="num text-sm font-semibold">{display}</span>
      </div>
      <div className="hatch mt-2.5 h-4 overflow-hidden rounded-full" role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={max} aria-valuenow={value} aria-valuetext={display}>
        <div className="h-full rounded-full bg-brand-accent transition-[width] duration-500 ease-out" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/* ---------- Initials avatar ---------- */
export function Avatar({ name, tone = "accent" }: { name: string; tone?: "accent" | "ink" }) {
  const parts = name.replace(/[^\p{L}\p{N}\s]/gu, "").trim().split(/\s+/);
  const ini = ((parts[0]?.[0] ?? "") + (parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "")).toUpperCase();
  return (
    <span aria-hidden className={`grid size-11 shrink-0 place-items-center rounded-full text-sm font-semibold ${tone === "accent" ? "bg-brand-accent/15 text-brand-primary-hover" : "bg-subtle text-ink"}`}>
      {ini || "?"}
    </span>
  );
}

/* ---------- FilterBar: search + chip filters ---------- */
export function FilterBar<T extends string>({
  search,
  onSearch,
  options,
  value,
  onChange,
  placeholder = "Search",
  children,
}: {
  search: string;
  onSearch: (s: string) => void;
  options: readonly { value: T; label: string; count?: number }[];
  value: T;
  onChange: (v: T) => void;
  placeholder?: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="flex min-w-0 flex-wrap gap-1.5" role="group" aria-label="Status filter">
        {options.map((o) => (
          <button
            key={o.value}
            type="button"
            aria-pressed={value === o.value}
            onClick={() => onChange(o.value)}
            className={`chip cursor-pointer px-3.5 py-1.5 text-[13px] transition-colors duration-150 ${value === o.value ? "border-brand-secondary bg-brand-secondary text-white" : "border-line bg-card text-ink hover:border-brand-gray/60 hover:bg-subtle"}`}
          >
            {o.label}
            {o.count !== undefined && <span className="num opacity-80">{o.count}</span>}
          </button>
        ))}
      </div>
      {/* Full width on phones (search grows, filters wrap); pinned right from sm up. */}
      <div className="flex w-full min-w-0 flex-wrap items-center gap-2 sm:ml-auto sm:w-auto sm:flex-nowrap">
        {children}
        <label className="relative min-w-0 flex-1 sm:flex-none">
          <span className="sr-only">{placeholder}</span>
          <Icon name="search" size={16} className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-ink-muted" />
          {/* data-page-search: the "/" shortcut focuses this field. */}
          <input data-page-search className="input w-full rounded-full py-2 pl-10 sm:w-64" type="search" value={search} placeholder={placeholder} onChange={(e) => onSearch(e.target.value)} />
        </label>
      </div>
    </div>
  );
}

/* ---------- Empty / Error / Loading ---------- */
export function EmptyState({ title, hint, action, icon = "inbox" }: { title: string; hint?: string; action?: ReactNode; icon?: IconName }) {
  return (
    <div className="grid place-items-center gap-2 px-6 py-12 text-center">
      <span aria-hidden className="mb-1 grid size-12 place-items-center rounded-full bg-subtle text-ink-muted"><Icon name={icon} size={22} /></span>
      <div className="font-medium">{title}</div>
      {hint && <p className="max-w-sm text-sm text-ink-muted">{hint}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorState({ title, detail, onRetry }: { title: string; detail?: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="grid place-items-center gap-2 px-6 py-12 text-center">
      <span className="grid size-14 place-items-center rounded-full bg-danger/10"><Icon name="alert" size={24} className="text-danger" /></span>
      <div className="font-medium text-danger">{title}</div>
      {detail && <p className="max-w-sm text-sm text-ink-muted">{detail}</p>}
      {onRetry && <button className="btn-ghost" onClick={onRetry}>Try again</button>}
    </div>
  );
}

// Table-shaped placeholder (header strip + rows at the real row height) so a list does not jump
// when its data arrives. Put it where the table will be, inside the same card.
const BAR_W = ["w-3/5", "w-4/5", "w-2/5", "w-3/4", "w-1/2", "w-2/3"];
export function LoadingRows({ rows = 6, cols = 5 }: { rows?: number; cols?: number }) {
  const grid = { gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` };
  return (
    <div role="status" aria-busy="true" aria-label="Loading" className="overflow-hidden">
      <div className="grid gap-5 border-b border-line bg-subtle px-5 py-[1.15rem]" style={grid} aria-hidden>
        {Array.from({ length: cols }, (_, c) => <div key={c} className="skeleton h-3 w-1/2" />)}
      </div>
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="grid gap-5 border-b border-line px-5 py-5 last:border-b-0" style={grid} aria-hidden>
          {Array.from({ length: cols }, (_, c) => <div key={c} className={`skeleton h-3.5 ${BAR_W[(r + c) % BAR_W.length]}`} />)}
        </div>
      ))}
      <span className="sr-only">Loading…</span>
    </div>
  );
}

/** Whole-page placeholder: title, a line of filters and a card. Used while a screen or record loads. */
export function PageSkeleton({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" aria-busy="true" aria-label={label} className="space-y-5">
      <div className="space-y-3 pt-1" aria-hidden>
        <div className="skeleton h-8 w-56 sm:h-9" />
        <div className="skeleton h-4 w-full max-w-md" />
      </div>
      <div className="flex gap-2" aria-hidden>
        {[16, 20, 14].map((w) => <div key={w} className="skeleton h-8" style={{ width: `${w * 0.275}rem` }} />)}
      </div>
      <div className="card overflow-hidden" aria-hidden>
        <LoadingRows rows={5} cols={4} />
      </div>
      <span className="sr-only">{label}…</span>
    </div>
  );
}

/* ---------- ConfirmDialog: native <dialog> gives focus trap + Esc ---------- */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  danger,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  danger?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-labelledby={titleId}
      className="m-auto w-[min(32rem,calc(100vw-2rem))] rounded-card border-0 bg-card p-0 text-ink shadow-[0_8px_24px_rgb(24_24_27/0.12)] backdrop:bg-ink/40"
    >
      <div className="space-y-3 p-6">
        <h2 id={titleId} className="text-lg font-semibold">{title}</h2>
        <div className="text-sm text-ink-muted">{children}</div>
      </div>
      <div className="flex justify-end gap-2 border-t border-line px-6 py-4">
        <button className="btn-ghost" onClick={onClose} autoFocus>Cancel</button>
        <button className={danger ? "btn bg-danger text-white hover:opacity-90" : "btn-primary"} onClick={onConfirm}>{confirmLabel}</button>
      </div>
    </dialog>
  );
}
