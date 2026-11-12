import { describe, expect, it } from "vitest";
import type { Session } from "../api";
import { formatMoney, highlight, isAppPath, latestOnly, quickActions, searchPageUrl, searchQueryString } from "./search";

const marked = (text: string, terms: string[]) =>
  highlight(text, terms)
    .filter((s) => s.hit)
    .map((s) => s.text);

const session = (grants: [string, string | null][]): Session => ({
  employee: { id: "e", email: "x@example.test", display_name: "X", company_id: "c" },
  mfa_required: false,
  mfa_enrolled: false,
  permissions: [...new Set(grants.map(([p]) => p))],
  grants: grants.map(([permission, warehouse_id]) => ({ permission, warehouse_id })),
  expires_at: "2099-01-01T00:00:00Z",
});

describe("search helpers", () => {
  it("highlights words ignoring case and accents, keeping the original text", () => {
    expect(marked("Café Ñandú Island", ["cafe", "nandu"])).toEqual(["Café", "Ñandú"]);
    expect(marked("Island Grocers", ["isla", "groc"])).toEqual(["Isla", "Groc"]);
    expect(highlight("abc", []).map((s) => s.text).join("")).toBe("abc");
    expect(marked("TT2026-000123", ["tt2026-000123"])).toEqual(["TT2026-000123"]);
    expect(marked("aaaa", ["aa"])).toEqual(["aaaa"]);
    expect(highlight("", ["x"])).toEqual([]);
  });

  it("formats money from decimal strings without floats", () => {
    expect(formatMoney("1234567.5", "CAD")).toBe("CAD 1,234,567.50");
    expect(formatMoney("0.10", null)).toBe("0.10");
    expect(formatMoney("-1500.00", "USD")).toBe("USD -1,500.00");
    expect(formatMoney(null, "CAD")).toBeNull();
  });

  it("builds query strings and see-all links", () => {
    expect(searchQueryString({ q: "amount:>1000 date:2026-11", type: "invoice", perGroup: 4, sort: "relevance" })).toBe(
      "q=amount%3A%3E1000+date%3A2026-11&type=invoice&per_group=4",
    );
    expect(searchQueryString({ q: "x", sort: "newest", cursor: "abc" })).toBe("q=x&cursor=abc&sort=newest");
    expect(searchPageUrl("TFS-0001", "lot")).toBe("/search?q=TFS-0001&type=lot");
  });

  it("only follows app paths", () => {
    expect(isAppPath("/invoices/1")).toBe(true);
    expect(isAppPath("//evil.example/x")).toBe(false);
    expect(isAppPath("https://evil.example")).toBe(false);
    expect(isAppPath("javascript:alert(1)")).toBe(false);
  });

  it("keeps only the newest request current", () => {
    const g = latestOnly();
    const a = g.next();
    const b = g.next();
    expect(a.signal.aborted).toBe(true);
    expect(g.isCurrent(a.token)).toBe(false);
    expect(g.isCurrent(b.token)).toBe(true);
    g.cancel();
    expect(b.signal.aborted).toBe(true);
    expect(g.isCurrent(b.token)).toBe(false);
  });

  it("offers only quick actions the person may use", () => {
    expect(quickActions(null)).toEqual([]);
    const warehouse = quickActions(session([["inventory.read", "w1"], ["catalog.read", "w1"]])).map((a) => a.label);
    expect(warehouse).toEqual(["Inventory"]);
    const owner = quickActions(
      session([
        ["invoice.issue", null],
        ["sales_order.manage", null],
        ["catalog.manage", null],
        ["inventory.read", null],
        ["scan.create", null],
        ["report.read", null],
      ]),
    ).map((a) => a.label);
    expect(owner).toEqual(["New invoice", "New order", "Add product", "Add customer", "Inventory", "Scan Center", "Reports"]);
    // catalog.manage limited to one warehouse does not offer company-wide master data
    expect(quickActions(session([["catalog.manage", "w1"]])).map((a) => a.label)).toEqual([]);
  });
});
