// Magnifier on the scan review (components/scanner/ScanLens.tsx): which part of the original
// page to show for a line, and how large. Pure functions, unit-tested.
//
// The office order sheet has three tables side by side, each ITEM | Q | PKG | WEIGHT | NOTES.
// The reader's box for a line often covers the product name only, so the window does not centre
// on that box: it starts at the item name and always spans a whole table to its right (owner,
// 2026-11-11: "at least 4 columns must be visible: item, Q, PKG, weight, notes").

export const LENS_W = 600;
export const LENS_H = 200;
const W = LENS_W;
const H = LENS_H;

/** Width of one of the sheet's three tables, as a share of the page width. */
export const TABLE_SPAN = 0.31;
/** A little room left of the item name. */
const LEAD = 0.012;

/** Where a line was read on the original: page and a box in 0..1 page coordinates. */
export type LensBox = { page: number; x0: number; y0: number; x1: number; y1: number };

/** 0 = automatic (the whole table row fits). Never more than 4x: at 4x a quarter of the page
 * width is visible, which still holds item, Q, PKG and weight of a table. */
export const LENS_ZOOMS = [0, 2, 3, 4] as const;
export type LensZoom = (typeof LENS_ZOOMS)[number];

/** A line's stored box, or null when the reader did not say where it was. */
export function lensBox(b: unknown): LensBox | null {
  if (!b || typeof b !== "object") return null;
  const o = b as Record<string, unknown>;
  const n = (v: unknown) => (v === undefined || v === null || v === "" ? NaN : Number(v));
  const box = { page: n(o.page), x0: n(o.x0), y0: n(o.y0), x1: n(o.x1), y1: n(o.y1) };
  if (!Object.values(box).every(Number.isFinite) || box.page < 1 || box.x1 <= box.x0 || box.y1 <= box.y0) return null;
  return box;
}

/** The stretch of the page to frame: from just left of the item name, at least one table wide
 * (more when the line's own box is wider), never past the page's right edge. */
export function lensFrame(b: LensBox): { left: number; span: number } {
  const left = Math.max(0, b.x0 - LEAD);
  const span = Math.min(1 - left, Math.max(TABLE_SPAN, b.x1 - b.x0 + 2 * LEAD));
  return { left, span };
}

/** Zoom used for "Auto": the framed stretch fills the window's width, between 2x and 4x. */
export function autoZoom(b: LensBox): number {
  return Math.min(4, Math.max(2, 1 / lensFrame(b).span));
}

/** Where to draw the page inside a W x H window: the frame's left edge (the item name) at the
 * window's left, the line's middle at the window's middle. `ratio` is page height / width.
 * Returns the page image's size and offset, and the band outlining the line, in px. */
export function lensLayout(b: LensBox, zoom: number, ratio: number, w = W, h = H) {
  const pw = w * zoom;
  const ph = pw * ratio;
  const { left } = lensFrame(b);
  const visible = w / pw; // share of the page width the window shows
  const x = Math.min(left, Math.max(0, 1 - visible)); // keep the page's right edge in view
  const cy = ((b.y0 + b.y1) / 2) * ph;
  const imgLeft = -x * pw;
  const imgTop = h / 2 - cy;
  const { span } = lensFrame(b);
  const bandLeft = Math.max(2, imgLeft + (b.x0 - LEAD / 2) * pw);
  const bandRight = Math.min(w - 2, imgLeft + (left + span) * pw);
  return {
    width: pw,
    height: ph,
    left: imgLeft,
    top: imgTop,
    box: { left: bandLeft, top: h / 2 - ((b.y1 - b.y0) / 2) * ph, width: Math.max(0, bandRight - bandLeft), height: (b.y1 - b.y0) * ph },
  };
}

/** Sharper page images for higher zoom (the server renders PDF pages at these dpi). */
export const lensDpi = (zoom: number) => (zoom <= 2.5 ? 220 : 300);
