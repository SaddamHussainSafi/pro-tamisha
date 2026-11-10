import { describe, expect, it } from "vitest";
import { ApiError, type Session } from "../api";
import { daysLeftText, deleteMode, recordLink, referenceLines, trashErrorText, twoStepReady } from "./trash";

const WH = "wh-1";
function session(grants: [string, string | null][], mfa = { enrolled: true, required: false }): Session {
  return {
    employee: { id: "e1", email: "e@example.test", display_name: "E", company_id: "c1" },
    mfa_required: mfa.required,
    mfa_enrolled: mfa.enrolled,
    permissions: grants.map(([p]) => p),
    grants: grants.map(([permission, warehouse_id]) => ({ permission, warehouse_id })),
    expires_at: "2099-01-01T00:00:00Z",
  };
}

describe("deleteMode", () => {
  it("gives the Super Admin the Trash for every kind", () => {
    const s = session([["trash.manage", null]]);
    for (const k of ["product", "user", "notification", "sales_order"] as const) expect(deleteMode(s, k, WH)).toBe("trash");
  });

  it("a warehouse-scoped trash.manage grant is not enough", () => {
    expect(deleteMode(session([["trash.manage", WH]]), "notification")).toBeNull();
  });

  it("lets editors ask, with the record's own permission and scope", () => {
    const manager = session([
      ["catalog.manage", null],
      ["sales_order.manage", WH],
    ]);
    expect(deleteMode(manager, "product")).toBe("request");
    expect(deleteMode(manager, "sales_order", WH)).toBe("request");
    expect(deleteMode(manager, "sales_order", "other-wh")).toBeNull();
    expect(deleteMode(manager, "user")).toBeNull(); // only the Super Admin deletes users
    expect(deleteMode(manager, "notification")).toBeNull();
    // catalog edits need a company-wide grant
    expect(deleteMode(session([["catalog.manage", WH]]), "customer")).toBeNull();
  });

  it("shows nothing to read-only staff", () => {
    const viewer = session([
      ["catalog.read", null],
      ["sales_order.read", null],
    ]);
    expect(deleteMode(viewer, "product")).toBeNull();
    expect(deleteMode(viewer, "sales_order", WH)).toBeNull();
  });
});

describe("helpers", () => {
  it("twoStepReady needs an enrolled and passed two-step sign-in", () => {
    expect(twoStepReady(session([], { enrolled: true, required: false }))).toBe(true);
    expect(twoStepReady(session([], { enrolled: false, required: false }))).toBe(false);
    expect(twoStepReady(session([], { enrolled: true, required: true }))).toBe(false);
  });

  it("days left", () => {
    expect(daysLeftText(0)).toBe("Today");
    expect(daysLeftText(1)).toBe("1 day");
    expect(daysLeftText(30)).toBe("30 days");
  });

  it("links to restored records", () => {
    expect(recordLink("product", "p1")).toBe("/products/p1");
    expect(recordLink("sales_order", "o1")).toBe("/orders/o1");
    expect(recordLink("case_size", "x")).toBe("/products/case-sizes");
    expect(recordLink("website_entry", "s1")).toBe("/order-connect/submissions/s1");
  });

  it("names what blocks a delete", () => {
    const e = new ApiError(409, "IN_USE", "Island Grocers can't be deleted: it is used by 2 sales orders (SO-1, SO-2); 1 invoice.", {
      references: [
        { table: "sales_orders", noun: "sales order", count: 2, examples: ["SO-1", "SO-2"] },
        { table: "invoices", noun: "invoice", count: 1, examples: [] },
        { table: "audit_events", noun: "audit log entry", count: 3, examples: [] },
      ],
    });
    expect(referenceLines(e)).toEqual(["2 sales orders (SO-1, SO-2)", "1 invoice", "3 audit log entries"]);
    expect(trashErrorText(e)).toBe("Island Grocers can't be deleted while other records use it:");
    expect(referenceLines(new Error("x"))).toEqual([]);
    expect(trashErrorText(new ApiError(403, "TWO_STEP_REQUIRED", "x"))).toMatch(/two-step/);
  });
});
