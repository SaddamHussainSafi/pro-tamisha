import { describe, expect, it } from "vitest";
import { add, cad, dec, isDecimal, mul, qty, toFixed } from "./decimal";

describe("decimal", () => {
  it("avoids float error", () => {
    expect(toFixed(add(dec("0.1"), dec("0.2")), 2)).toBe("0.30");
    expect(toFixed(add(dec("0.1"), dec("0.2")), 6)).toBe("0.300000");
  });
  it("multiplies weight × rate with half-up rounding", () => {
    expect(toFixed(mul(dec("41.35"), dec("1.15")), 2)).toBe("47.55"); // 47.5525
    expect(toFixed(mul(dec("2.5"), dec("0.005")), 2)).toBe("0.01"); // 0.0125
  });
  it("formats CAD with grouping and explicit currency", () => {
    expect(cad(dec("1234567.5"))).toBe("CAD 1,234,567.50");
    expect(cad(dec("-12.345"))).toBe("−CAD 12.35");
    expect(cad(dec("0"))).toBe("CAD 0.00");
  });
  it("formats quantities", () => {
    expect(qty(dec("1200"), 0)).toBe("1,200");
    expect(qty(dec("38.5"))).toBe("38.50");
  });
  it("rejects garbage", () => {
    expect(isDecimal("1e3")).toBe(false);
    expect(isDecimal("12.")).toBe(false);
    expect(() => dec("abc")).toThrow();
    expect(() => dec("1.1234567")).toThrow();
  });
});
