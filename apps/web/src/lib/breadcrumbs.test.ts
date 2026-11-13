import { describe, expect, it } from "vitest";
import { locate, NAV_ALL, navScreens } from "../nav";
import { crumbsFor, headingLabel } from "./breadcrumbs";

const labels = (p: string, q = "") => crumbsFor(p, q).map((c) => c.label ?? `[${c.fallback}]`);

describe("the approved menu", () => {
  it("lists the sections and entries in the approved order", () => {
    expect(NAV_ALL.map((n) => [n.label, ...(n.children ?? []).map((c) => c.label)])).toEqual([
      ["Overview"],
      ["Sales", "Sales Orders", "Sales Invoices", "Payments", "Customers"],
      ["Purchases", "Purchase Orders", "Purchase Invoices", "Suppliers"],
      ["Warehouse", "Packing", "Receiving", "Inventory", "Products", "Product Packages"],
      ["Scan", "Scan Box", "Scan History"],
      ["Documents", "Files"],
      ["Email"],
      ["Dashboard"],
      ["Settings", "General", "Users & Roles", "Integrations", "Online Order Settings", "Activity Log", "ERP Trash"],
    ]);
  });
  it("finds one current entry for a page, the longest address winning", () => {
    const at = (p: string, q = "") => {
      const h = locate(p, q);
      return h ? `${h.node.label}${h.leaf ? ` › ${h.leaf.label}` : ""}` : null;
    };
    expect(at("/")).toBe("Overview");
    expect(at("/orders/abc")).toBe("Sales › Sales Orders");
    expect(at("/online-orders/requests/abc")).toBe("Sales › Sales Orders");
    expect(at("/online-orders/settings")).toBe("Settings › Online Order Settings");
    expect(at("/products/abc")).toBe("Warehouse › Products");
    expect(at("/products/case-sizes")).toBe("Warehouse › Product Packages");
    expect(at("/purchases")).toBe("Purchases › Purchase Orders");
    expect(at("/purchases", "?view=verify")).toBe("Warehouse › Receiving");
    expect(at("/scan")).toBe("Scan");
    expect(at("/scan/review/abc")).toBe("Scan");
    expect(at("/scan", "?tab=history")).toBe("Scan › Scan History");
    expect(at("/gmail")).toBe("Scan › Scan Box");
    expect(at("/settings/roles")).toBe("Settings › Users & Roles");
    expect(at("/settings/security")).toBe("Settings › General");
    expect(at("/reports")).toBe("Dashboard");
    expect(at("/search")).toBeNull();
  });
  it("offers every screen with an address to ⌘K", () => {
    const s = navScreens();
    expect(s.find((x) => x.to === "/scan")?.label).toBe("Scan Center");
    expect(s.find((x) => x.to === "/products/prices")?.label).toBe("Warehouse › Price book");
    expect(s.some((x) => x.label.includes("Purchase Invoices"))).toBe(false); // no page yet
    // …and it is kept out of the sidebar until it has one
    expect(NAV_ALL.flatMap((n) => n.children ?? []).filter((c) => c.hidden).map((c) => c.label)).toEqual(["Purchase Invoices"]);
  });
});

describe("global breadcrumbs", () => {
  it("follows the menu labels", () => {
    expect(labels("/")).toEqual(["Overview"]);
    expect(labels("/reports")).toEqual(["Dashboard"]);
    expect(labels("/invoices/new")).toEqual(["Sales", "Sales Invoices", "New"]);
    expect(labels("/products/case-sizes")).toEqual(["Warehouse", "Product Packages"]);
    expect(labels("/scan")).toEqual(["Scan", "Scan Center"]);
    expect(labels("/gmail")).toEqual(["Scan", "Scan Box"]);
    expect(labels("/scan", "?tab=history")).toEqual(["Scan", "Scan History"]);
    expect(labels("/documents")).toEqual(["Documents", "Files"]);
    expect(labels("/settings/users")).toEqual(["Settings", "Users & Roles"]);
    expect(labels("/settings/roles")).toEqual(["Settings", "Users & Roles", "Roles & permissions"]);
    expect(labels("/activity")).toEqual(["Settings", "Activity Log"]);
  });
  it("links only steps that have a page: a plain section is text", () => {
    const c = crumbsFor("/invoices/new");
    expect(c.map((x) => x.to)).toEqual([null, "/invoices", "/invoices/new"]);
    expect(crumbsFor("/gmail").map((x) => x.to)).toEqual(["/scan", "/gmail"]);
  });
  it("names sub-screens, tabs and the online orders inside Sales Orders", () => {
    expect(labels("/products/prices")).toEqual(["Warehouse", "Products", "Price book"]);
    expect(labels("/inventory", "?tab=movements")).toEqual(["Warehouse", "Inventory", "Stock ledger"]);
    expect(labels("/online-orders/requests")).toEqual(["Sales", "Sales Orders", "Online", "Requests"]);
    expect(labels("/receivables")).toEqual(["Sales", "Payments", "Receivables"]);
    expect(labels("/suppliers/payments")).toEqual(["Purchases", "Suppliers", "Supplier payments"]);
    expect(labels("/search")).toEqual(["Search"]);
  });
  it("leaves a record's name to its page heading, with a fallback", () => {
    const c = crumbsFor("/purchases/7f3c1a2b-0000-4000-8000-000000000001");
    expect(c.map((x) => x.label)).toEqual(["Purchases", "Purchase Orders", null]);
    expect(c[2]?.fallback).toBe("Purchase");
    expect(labels("/scan/review/abc")).toEqual(["Scan", "Scan Center", "To review", "[Reading]"]);
    expect(crumbsFor("/scan/review/abc")[2]?.to).toBe("/scan?tab=review");
    expect(labels("/online-orders/requests/abc")).toEqual(["Sales", "Sales Orders", "Online", "Requests", "[Request]"]);
  });
  it("uses the generic name for a record in the middle of the trail", () => {
    expect(labels("/packing/abc/invoice")).toEqual(["Warehouse", "Packing", "Packing sheet", "Issue invoice"]);
    expect(crumbsFor("/packing/abc/invoice")[2]?.to).toBe("/packing/abc");
  });
  it("keeps a heading to one short line", () => {
    expect(headingLabel("  Order\n SO-1  ")).toBe("Order SO-1");
    expect(headingLabel("")).toBeNull();
    expect(headingLabel("x".repeat(80))?.length).toBe(61);
  });
});
