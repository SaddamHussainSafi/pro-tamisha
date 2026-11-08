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
