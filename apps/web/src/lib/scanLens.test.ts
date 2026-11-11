import { describe, expect, it } from "vitest";
import { autoZoom, LENS_H, LENS_W, lensBox, lensDpi, lensFrame, lensLayout, TABLE_SPAN } from "./scanLens";

// Real boxes from a Gemini reading of the office sheet (2026-11-11): the box often covers the
// product name only (THJ), sometimes more than one table (BFR).
const BFR = { page: 1, x0: 0.052, y0: 0.136, x1: 0.545, y1: 0.158 };
const THJ = { page: 1, x0: 0.355, y0: 0.581, x1: 0.546, y1: 0.602 };
const NAME_ONLY = { page: 1, x0: 0.66, y0: 0.3, x1: 0.74, y1: 0.32 }; // a fruits-table name

describe("scan magnifier", () => {
  it("reads a stored box, or none", () => {
    expect(lensBox({ page: 1, x0: "0.052", y0: "0.136", x1: "0.545", y1: "0.158" })).toEqual(BFR);
    expect(lensBox(null)).toBeNull();
    expect(lensBox({ page: 1, x0: "0.5", y0: "0.1", x1: "0.4", y1: "0.2" })).toBeNull();
    expect(lensBox({ page: 0, x0: 0, y0: 0, x1: 1, y1: 1 })).toBeNull();
    expect(lensBox({ x0: 0, y0: 0, x1: 1, y1: 1 })).toBeNull();
  });
  it("frames at least a whole table from the item name, never past the page", () => {
    expect(lensFrame(NAME_ONLY).left).toBeCloseTo(0.648);
    expect(lensFrame(NAME_ONLY).span).toBeCloseTo(TABLE_SPAN);
    expect(lensFrame(THJ).span).toBeCloseTo(TABLE_SPAN);
    expect(lensFrame(BFR).span).toBeGreaterThan(0.5); // a wide box is framed whole
    const edge = lensFrame({ page: 1, x0: 0.9, y0: 0, x1: 0.95, y1: 0.02 });
    expect(edge.left + edge.span).toBeCloseTo(1);
  });
  it("auto zoom fits the frame, between 2x and 4x (never so close that columns are lost)", () => {
    expect(autoZoom(NAME_ONLY)).toBeCloseTo(1 / TABLE_SPAN); // ~3.2x: the whole table row
    expect(autoZoom(BFR)).toBe(2);
    expect(autoZoom({ page: 1, x0: 0.5, y0: 0, x1: 0.51, y1: 0.02 })).toBeLessThanOrEqual(4);
  });
  it("puts the item name at the window's left and the line in the middle", () => {
    for (const z of [2, 3, 4]) {
      const l = lensLayout({ ...THJ, y0: 0.3, y1: 0.32 }, z, 0.77);
      expect(l.width).toBeCloseTo(LENS_W * z);
      expect(l.left + lensFrame(THJ).left * l.width).toBeCloseTo(0); // frame start at the left
      expect(l.top + 0.31 * l.height).toBeCloseTo(LENS_H / 2); // row centre at the middle
      expect(l.box.left).toBeGreaterThanOrEqual(2);
      expect(l.box.left + l.box.width).toBeLessThanOrEqual(LENS_W - 2);
    }
    // the visible width holds at least four of a table's five columns at the closest zoom
    expect(1 / 4).toBeGreaterThanOrEqual(TABLE_SPAN * 0.8);
  });
  it("keeps the page's right edge in view near the margin", () => {
    const l = lensLayout({ page: 1, x0: 0.9, y0: 0.5, x1: 0.95, y1: 0.52 }, 4, 0.77);
    expect(l.left + l.width).toBeCloseTo(LENS_W); // page's right edge at the window's right
  });
  it("asks for a sharper page at higher zoom", () => {
    expect(lensDpi(2)).toBe(220);
    expect(lensDpi(3.2)).toBe(300);
    expect(lensDpi(4)).toBe(300);
  });
});
