/** Short axis label: 12500 → "12.5K". Display only; never used for money math. */
export function shortNum(v: number) {
  if (v >= 1_000_000) return `${+(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1000) return `${+(v / 1000).toFixed(1)}K`;
  return `${Math.round(v)}`;
}

export function greeting(d = new Date()) {
  const h = d.getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export function longDate(d = new Date()) {
  return d.toLocaleDateString("en-CA", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}

/** Today's date on this computer as YYYY-MM-DD, the value a date input takes. Forms that record
 * when something happens start with it; the person can still pick any other date. */
export function todayIso(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
