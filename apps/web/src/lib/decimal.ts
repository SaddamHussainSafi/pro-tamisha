// Exact decimal arithmetic for money/weights in the UI. Never use JS floats for these values.
// Values are BigInt scaled by 10^SCALE. The server remains the source of truth for totals.

const SCALE = 6;
const ONE = 10n ** BigInt(SCALE);
const RE = /^\s*(-)?(\d+)(?:\.(\d+))?\s*$/;

export type Dec = bigint;

export function isDecimal(s: string): boolean {
  return RE.test(s);
}

export function dec(s: string): Dec {
  const m = RE.exec(s);
  if (!m) throw new Error(`Not a decimal: ${JSON.stringify(s)}`);
  const neg = m[1];
  const int = m[2] ?? "0";
  const frac = m[3] ?? "";
  if (frac.length > SCALE) throw new Error(`Too many decimal places: ${s}`);
  const v = BigInt(int) * ONE + BigInt(frac.padEnd(SCALE, "0"));
  return neg ? -v : v;
}

export const add = (...xs: Dec[]): Dec => xs.reduce((a, b) => a + b, 0n);

/** Multiply then round half-up (away from zero) back to SCALE. */
export function mul(a: Dec, b: Dec): Dec {
  const p = a * b;
  const half = ONE / 2n;
  return p >= 0n ? (p + half) / ONE : -((-p + half) / ONE);
}

/** Round half-up to `places` and render as a plain string. */
export function toFixed(v: Dec, places: number): string {
  const unit = 10n ** BigInt(SCALE - places);
  const abs = v < 0n ? -v : v;
  const r = (abs + unit / 2n) / unit;
  const s = r.toString().padStart(places + 1, "0");
  const body = places ? `${s.slice(0, -places)}.${s.slice(-places)}` : s;
  return (v < 0n && r !== 0n ? "-" : "") + body;
}

function group(intPart: string): string {
  return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** CAD with explicit currency code, e.g. "CAD 1,234.50". */
export function cad(v: Dec): string {
  const s = toFixed(v, 2);
  const neg = s.startsWith("-");
  const [i = "0", f = "00"] = (neg ? s.slice(1) : s).split(".");
  return `${neg ? "−" : ""}CAD ${group(i)}.${f}`;
}

export function qty(v: Dec, places = 2): string {
  const s = toFixed(v, places);
  const [i = "0", f] = s.split(".");
  return f === undefined ? group(i) : `${group(i)}.${f}`;
}
