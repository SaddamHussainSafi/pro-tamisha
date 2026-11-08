import { describe, expect, it } from "vitest";
import { bucketFor, chartBars, csvName, peak, presetRange, rangeProblem, signed, toCsv, unitsText } from "./reports";

describe("ranges", () => {
  it("presets end today and count today", () => {
    expect(presetRange("7d", "2026-11-08")).toEqual({ from: "2026-11-02", to: "2026-11-08" });
    expect(presetRange("30d", "2026-03-01")).toEqual({ from: "2026-01-31", to: "2026-03-01" });
    expect(presetRange("ytd", "2026-11-08")).toEqual({ from: "2026-01-01", to: "2026-11-08" });
  });

  it("mirrors the server's range checks", () => {
    expect(rangeProblem({ from: "2026-11-01", to: "2026-11-08" })).toBeNull();
    expect(rangeProblem({ from: "2026-11-09", to: "2026-11-08" })).toMatch(/after the end/);
    expect(rangeProblem({ from: "2024-11-01", to: "2026-11-08" })).toMatch(/at most 731 days/);
    expect(rangeProblem({ from: "2024-11-08", to: "2026-11-07" })).toBeNull(); // 730 days apart
    expect(rangeProblem({ from: "", to: "2026-11-08" })).toMatch(/start and an end/);
  });

  it("uses day bars up to two months, then months", () => {
    expect(bucketFor({ from: "2026-09-07", to: "2026-11-08" })).toBe("day");
    expect(bucketFor({ from: "2026-09-06", to: "2026-11-08" })).toBe("month");
  });
});

describe("units", () => {
  it("never adds boxes and pieces together", () => {
    expect(
      unitsText([
        { unit_code: "PIECE", qty: "20.0000" },
        { unit_code: "BOX", qty: "1400.5000" },
      ]),
    ).toBe("1,400.50 boxes · 20.00 pieces");
    expect(unitsText([])).toBe("—");
  });

  it("signs movements", () => {
    expect([signed("25.0000"), signed("-14.0000"), signed("0.0000")]).toEqual(["+25.00", "−14.00", "0"]);
  });
});

describe("chart bars", () => {
  const days = (n: number) =>
    Array.from({ length: n }, (_, i) => ({ period: `2026-10-${String(i + 1).padStart(2, "0")}`, net_sales: i === n - 1 ? "386.00" : "0.10" }));

  it("adds days up per 7 days with exact sums", () => {
    const { unit, bars } = chartBars(days(30), "day");
    expect(unit).toBe("week");
    expect(bars).toHaveLength(5); // 4 full weeks + 2 days
    expect(bars[0]).toEqual({ label: "Oct 1", value: 0.7, display: "CAD 0.70" });
    expect(bars[4]).toEqual({ label: "Oct 29", value: 386.1, display: "CAD 386.10" });
    expect(peak(bars)).toBe(4);
  });

  it("keeps short ranges daily and long month ranges in 3-month groups", () => {
    expect(chartBars(days(14), "day").unit).toBe("day");
    const months = Array.from({ length: 24 }, (_, i) => ({ period: `${2025 + Math.floor(i / 12)}-${String((i % 12) + 1).padStart(2, "0")}-01`, net_sales: "1.00" }));
    const q = chartBars(months, "month");
    expect(q.unit).toBe("quarter");
    expect(q.bars.map((b) => b.label).slice(0, 2)).toEqual(["Jan 25", "Apr 25"]);
    expect(q.bars.every((b) => b.display === "CAD 3.00")).toBe(true);
    expect(chartBars(months.slice(0, 12), "month").bars).toHaveLength(12);
    expect(peak([{ label: "a", value: 0, display: "" }])).toBe(-1);
  });
});

describe("csv", () => {
  it("quotes, uses CRLF and defuses formulas but not negative numbers", () => {
    const csv = toCsv(
      ["Customer", "Net sales", "Change"],
      [
        ['Island "Fresh", Ltd', "274.00", "-14.0000"],
        ["=HYPERLINK(\"x\")", "+1", null],
        ["@SUM(A1)", "-", "line\nbreak"],
      ],
    );
    expect(csv).toBe(
      'Customer,Net sales,Change\r\n"Island ""Fresh"", Ltd",274.00,-14.0000\r\n"\'=HYPERLINK(""x"")",\'+1,\r\n\'@SUM(A1),\'-,"line\nbreak"\r\n',
    );
  });

  it("names files by report and period", () => {
    expect(csvName("customers", { from: "2026-10-10", to: "2026-11-08" })).toBe("pro-trading-customers-2026-10-10-to-2026-11-08.csv");
  });
});
