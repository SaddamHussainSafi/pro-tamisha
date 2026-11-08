import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const root = resolve(__dirname, "../../..");
const tokens = JSON.parse(readFileSync(resolve(root, "design/tokens.json"), "utf8"));
const css = readFileSync(resolve(__dirname, "theme.css"), "utf8");

const expected: Record<string, string> = {
  "brand-primary": tokens.colors.brand.primary,
  "brand-primary-hover": tokens.colors.brand.primaryHover,
  "brand-secondary": tokens.colors.brand.secondary,
  "brand-accent": tokens.colors.brand.accent,
  "brand-gray": tokens.colors.brand.gray,
  canvas: tokens.colors.surface.canvas,
  card: tokens.colors.surface.card,
  subtle: tokens.colors.surface.subtle,
  ink: tokens.colors.text.primary,
  "ink-muted": tokens.colors.text.muted,
  line: tokens.colors.border.default,
  success: tokens.colors.state.success,
  warning: tokens.colors.state.warning,
  danger: tokens.colors.state.danger,
  info: tokens.colors.state.info,
  "series-1": tokens.colors.series[0],
  "series-2": tokens.colors.series[1],
  "series-3": tokens.colors.series[2],
  "series-4": tokens.colors.series[3],
  ...Object.fromEntries(
    Object.entries(tokens.colors.ramp as Record<string, string>)
      .filter(([k]) => k !== "name")
      .map(([k, v]) => [`apple-${k}`, v]),
  ),
};

describe("theme.css mirrors design/tokens.json", () => {
  for (const [name, hex] of Object.entries(expected)) {
    it(name, () => {
      const m = css.match(new RegExp(`--color-${name}:\\s*(#[0-9A-Fa-f]{6});`));
      expect(m?.[1]?.toUpperCase()).toBe(hex.toUpperCase());
    });
  }
  it("product and company names", () => {
    expect(tokens.productName).toBe("Pro Trading");
    expect(tokens.companyName).toBe("Tamisha Trading Inc.");
  });
});
