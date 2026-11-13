import { describe, expect, it } from "vitest";
import { crumbsFor, headingLabel } from "./breadcrumbs";

const labels = (p: string, q = "") => crumbsFor(p, q).map((c) => c.label ?? `[${c.fallback}]`);

describe("global breadcrumbs", () => {
  it("names first-level screens with one step (the shell then shows none)", () => {
    expect(labels("/")).toEqual([]);
    expect(labels("/orders")).toEqual(["Orders"]);
  });
  it("names fixed sub-screens and tab screens", () => {
    expect(labels("/products/case-sizes")).toEqual(["Products", "Case sizes"]);
    expect(labels("/settings/users")).toEqual(["Settings", "User management"]);
    expect(labels("/inventory", "?tab=movements")).toEqual(["Inventory", "Stock ledger"]);
    expect(labels("/online-orders/requests")).toEqual(["Online Orders", "Requests"]);
  });
  it("leaves a record's name to its page heading, with a fallback", () => {
    const c = crumbsFor("/orders/7f3c1a2b-0000-4000-8000-000000000001");
    expect(c.map((x) => x.label)).toEqual(["Orders", null]);
    expect(c[1]?.fallback).toBe("Order");
    expect(labels("/scan/review/abc")).toEqual(["Scan Center", "To review", "[Reading]"]);
    expect(crumbsFor("/scan/review/abc")[1]?.to).toBe("/scan?tab=review");
  });
  it("uses the generic name for a record in the middle of the trail", () => {
    expect(labels("/packing/abc/invoice")).toEqual(["Packing", "Packing sheet", "Issue invoice"]);
    expect(crumbsFor("/packing/abc/invoice")[1]?.to).toBe("/packing/abc");
  });
  it("keeps a heading to one short line", () => {
    expect(headingLabel("  Order\n SO-1  ")).toBe("Order SO-1");
    expect(headingLabel("")).toBeNull();
    expect(headingLabel("x".repeat(80))?.length).toBe(61);
  });
});
