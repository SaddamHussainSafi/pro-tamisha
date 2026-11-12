import { initials } from "../../lib/mail";

export function Star({ on, size = 16 }: { on: boolean; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden className={`shrink-0 ${on ? "text-warning" : "text-ink-muted"}`} fill={on ? "currentColor" : "none"} stroke="currentColor" strokeWidth={1.8} strokeLinejoin="round">
      <path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1.1 6.2L12 17.3l-5.6 2.9 1.1-6.2L3 9.6l6.2-.9z" />
    </svg>
  );
}

// Avatar tints from our own palette (series colours + brand), picked by name so a sender keeps one.
const TINTS = ["bg-brand-accent/20", "bg-series-2/15", "bg-series-3/18", "bg-series-4/15", "bg-line"];
export function Avatar({ name, size = 40 }: { name: string; size?: number }) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return (
    <span aria-hidden style={{ width: size, height: size, fontSize: size * 0.38 }} className={`grid shrink-0 place-items-center rounded-full font-semibold text-ink ${TINTS[h % TINTS.length]}`}>
      {initials(name)}
    </span>
  );
}
