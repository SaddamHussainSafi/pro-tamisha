import { describe, expect, it } from "vitest";
import {
  actionLabel,
  customKey,
  draftOf,
  fieldProblems,
  missingSystemFields,
  moveField,
  movePermission,
  normalizeHost,
  pct,
  publishProblems,
  qtyText,
  relTime,
  sender,
  type FormContent,
  type FormField,
} from "./orderConnect";

const f = (key: string, kind: FormField["kind"], required = true, options: string[] = []): FormField => ({
  key,
  kind,
  label: key,
  help_text: "",
  required,
  options,
});

describe("builder helpers", () => {
  it("moves a field without mutating the list", () => {
    const list = ["a", "b", "c", "d"];
    expect(moveField(list, 0, 2)).toEqual(["b", "c", "a", "d"]);
    expect(moveField(list, 3, 0)).toEqual(["d", "a", "b", "c"]);
    expect(moveField(list, 1, 9)).toBe(list);
    expect(list).toEqual(["a", "b", "c", "d"]);
  });

  it("makes unique custom keys from labels", () => {
    expect(customKey("Store type!", [])).toBe("custom_store_type");
    expect(customKey("Store type", [f("custom_store_type", "TEXT")])).toBe("custom_store_type_2");
    expect(customKey("¿¿", [])).toBe("custom_field");
    expect(customKey("Café opening hours and other very long wording", [])).toMatch(/^custom_[a-z0-9_]{1,24}$/);
  });

  it("lists system fields that were removed", () => {
    const missing = missingSystemFields([f("customer_name", "TEXT"), f("products", "PRODUCTS")]).map(([k]) => k);
    expect(missing).toContain("email");
    expect(missing).not.toContain("products");
  });

  it("applies the same publish rules as the server", () => {
    expect(publishProblems([f("products", "PRODUCTS"), f("business_name", "TEXT"), f("phone", "PHONE")])).toEqual([]);
    expect(publishProblems([f("products", "PRODUCTS", false), f("email", "EMAIL")])).toEqual([
      "a required Products field",
      "a required name or business name",
    ]);
  });

  it("checks choice lists", () => {
    expect(fieldProblems(f("custom_x", "SELECT", false, ["A"]))).toMatch(/two options/);
    expect(fieldProblems(f("custom_x", "SELECT", false, ["A", "A"]))).toMatch(/different/);
    expect(fieldProblems(f("custom_x", "SELECT", false, ["A", " "]))).toMatch(/empty/);
    expect(fieldProblems(f("custom_x", "SELECT", false, ["A", "B"]))).toBeNull();
    expect(fieldProblems({ ...f("custom_x", "TEXT"), label: "  " })).toMatch(/label/);
  });

  it("starts a new draft (revision 0) from published content", () => {
    const c = { status: "PUBLISHED", revision: 0, title: "T", intro: "", success_message: "S", theme: { accent: "", radius: "small" }, logo_id: null, show_prices: false, product_scope: "ALL", scope_ids: [], fields: [] } as unknown as FormContent;
    expect(draftOf(c).revision).toBe(0);
    expect(draftOf({ ...c, status: "DRAFT", revision: 4 }).revision).toBe(4);
  });
});

describe("allowed websites", () => {
  it("accepts names and URLs and keeps only the host", () => {
    expect(normalizeHost("https://Shop.Example.com/order?x=1")).toEqual({ host: "shop.example.com" });
    expect(normalizeHost("*.example.org")).toEqual({ host: "*.example.org" });
  });
  it("refuses IPs, ports, credentials and single names", () => {
    for (const bad of ["localhost", "192.168.1.10", "x.test:8080", "user@evil.test", "", "-bad-.com"]) {
      expect("error" in normalizeHost(bad)).toBe(true);
    }
  });
});

describe("review wording", () => {
  it("labels moves by where they come from", () => {
    expect(actionLabel("NEW", "REVIEWING")).toBe("Start review");
    expect(actionLabel("APPROVED", "REVIEWING")).toBe("Undo approval");
    expect(actionLabel("SPAM", "REVIEWING")).toBe("Reopen");
    expect(actionLabel("REVIEWING", "APPROVED")).toBe("Approve");
  });
  it("needs approval rights to approve or undo an approval", () => {
    expect(movePermission("REVIEWING", "APPROVED")).toBe("order_connect.approve");
    expect(movePermission("APPROVED", "REJECTED")).toBe("order_connect.approve");
    expect(movePermission("NEW", "SPAM")).toBe("order_connect.review");
  });
  it("names the sender, never showing erased data", () => {
    expect(sender({ business_name: "Corner", customer_name: "Ada", erased: false })).toBe("Corner");
    expect(sender({ business_name: "", customer_name: "Ada", erased: false })).toBe("Ada");
    expect(sender({ business_name: "Corner", customer_name: "Ada", erased: true })).toBe("Personal data erased");
  });
});

describe("formatting", () => {
  it("shows quantities with unit names", () => {
    expect(qtyText("1", "BOX")).toBe("1 box");
    expect(qtyText("12", "PIECE")).toBe("12 pieces");
    expect(qtyText("2.5", "LB")).toBe("2.5 lb");
  });
  it("never invents a conversion rate", () => {
    expect(pct(null)).toBe("—");
    expect(pct(0.256)).toBe("26%");
  });
  it("gives short relative times", () => {
    const now = new Date("2026-11-09T12:00:00Z");
    expect(relTime("2026-11-09T11:59:40Z", now)).toBe("just now");
    expect(relTime("2026-11-09T11:30:00Z", now)).toBe("30 min ago");
    expect(relTime("2026-11-09T07:00:00Z", now)).toBe("5 h ago");
  });
});
