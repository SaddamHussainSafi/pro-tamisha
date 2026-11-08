import type { UnitCode, UnitQty } from "../api";
import { cad, dec, qty, toFixed, type Dec } from "./decimal";

// Module 12 report helpers. Totals come from the server; the UI only formats, groups chart
// bars and builds CSV files. Quantities in different units are never added together.

export const MAX_DAYS = 731;

export type Range = { from: string; to: string };
export type Preset = "7d" | "30d" | "90d" | "ytd" | "custom";
export const PRESETS: { value: Preset; label: string }[] = [
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "90d", label: "Last 90 days" },
  { value: "ytd", label: "This year" },
  { value: "custom", label: "Custom" },
];

/** Local calendar day as YYYY-MM-DD (no UTC shift). */
export function isoDay(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

const parse = (s: string) => new Date(`${s}T12:00:00`);

export function addDays(day: string, n: number): string {
  const d = parse(day);
  d.setDate(d.getDate() + n);
  return isoDay(d);
}

export function daysBetween(a: string, b: string): number {
  return Math.round((parse(b).getTime() - parse(a).getTime()) / 86_400_000);
}

export function presetRange(p: Exclude<Preset, "custom">, today: string): Range {
  if (p === "ytd") return { from: `${today.slice(0, 4)}-01-01`, to: today };
  const n = p === "7d" ? 7 : p === "30d" ? 30 : 90;
  return { from: addDays(today, -(n - 1)), to: today };
}

/** Same checks the server makes, so the user sees them before a request. */
export function rangeProblem(r: Range): string | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(r.from) || !/^\d{4}-\d{2}-\d{2}$/.test(r.to)) return "Choose a start and an end date.";
  if (r.from > r.to) return "The start date is after the end date.";
  if (daysBetween(r.from, r.to) >= MAX_DAYS) return `Choose a range of at most ${MAX_DAYS} days.`;
  return null;
}

/** Day bars up to about two months, month bars beyond that. */
export const bucketFor = (r: Range): "day" | "month" => (daysBetween(r.from, r.to) <= 62 ? "day" : "month");

export const UNIT_LABEL: Record<UnitCode, string> = { BOX: "boxes", PIECE: "pieces", LB: "lb", KG: "kg" };
export const UNIT_ORDER: UnitCode[] = ["BOX", "PIECE", "LB", "KG"];

/** "14 boxes · 20 pieces" — one figure per unit, never summed across units. */
export function unitsText(units: UnitQty[]): string {
  if (!units.length) return "—";
  return [...units]
    .sort((a, b) => UNIT_ORDER.indexOf(a.unit_code) - UNIT_ORDER.indexOf(b.unit_code))
    .map((u) => `${qty(dec(u.qty))} ${UNIT_LABEL[u.unit_code]}`)
    .join(" · ");
}

export const money = (s: string) => cad(dec(s));
export const amount = (s: string, unit: UnitCode) => `${qty(dec(s))} ${UNIT_LABEL[unit]}`;
/** Signed movement: "+25", "−14", "0". */
export function signed(s: string): string {
  const v = dec(s);
  if (v === 0n) return "0";
  return `${v > 0n ? "+" : "−"}${qty(v < 0n ? -v : v)}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export function periodLabel(day: string, bucket: "day" | "month"): string {
  const m = MONTHS[Number(day.slice(5, 7)) - 1] ?? "";
  return bucket === "month" ? `${m} ${day.slice(2, 4)}` : `${m} ${Number(day.slice(8, 10))}`;
}

export type ChartBar = { label: string; value: number; display: string };
export type ChartUnit = "day" | "week" | "month" | "quarter";
export const CHART_TITLE: Record<ChartUnit, string> = {
  day: "Net sales by day",
  week: "Net sales by week",
  month: "Net sales by month",
  quarter: "Net sales by 3-month period",
};

/**
 * Few, readable bars: up to 14 days stay daily, longer day series are added up per 7 days and
 * month series over 12 months per 3 months (exact decimals), counted from the first day of the
 * period. Each bar is labelled with its first day or month; the bar value only sets its height.
 */
export function chartBars(series: { period: string; net_sales: string }[], bucket: "day" | "month"): { unit: ChartUnit; bars: ChartBar[] } {
  const unit: ChartUnit = bucket === "day" ? (series.length > 14 ? "week" : "day") : series.length > 12 ? "quarter" : "month";
  const size = unit === "week" ? 7 : unit === "quarter" ? 3 : 1;
  const bars: ChartBar[] = [];
  for (let i = 0; i < series.length; i += size) {
    const chunk = series.slice(i, i + size);
    const first = chunk[0];
    if (!first) continue;
    const total: Dec = chunk.reduce((s, p) => s + dec(p.net_sales), 0n);
    bars.push({ label: periodLabel(first.period, bucket), value: Number(toFixed(total, 2)), display: cad(total) });
  }
  return { unit, bars };
}

/** Index of the largest bar (first one on ties), or -1 when all are zero. */
export function peak(bars: ChartBar[]): number {
  let best = -1;
  bars.forEach((b, i) => {
    if (b.value > 0 && (best < 0 || b.value > (bars[best]?.value ?? 0))) best = i;
  });
  return best;
}

/**
 * RFC 4180 CSV with CRLF line ends. Text that a spreadsheet would run as a formula
 * (starting with = + - @, tab or CR) gets a leading apostrophe; plain numbers are left alone.
 */
export function toCsv(header: string[], rows: (string | number | null)[][]): string {
  const cell = (v: string | number | null) => {
    let s = v === null ? "" : String(v);
    if (/^[=+\-@\t\r]/.test(s) && !/^-?\d+(\.\d+)?$/.test(s)) s = `'${s}`;
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return [header, ...rows].map((r) => r.map(cell).join(",")).join("\r\n") + "\r\n";
}

export function csvName(report: string, r: Range): string {
  return `pro-trading-${report}-${r.from}-to-${r.to}.csv`;
}

/** Browser download of a CSV built from the rows on screen. */
export function downloadCsv(name: string, csv: string): void {
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
