import { describe, expect, it } from "vitest";
import { ApiError, type Customer } from "../api";
import { buildCreate, buildPatch, customerMessage, EMPTY_FORM, formFrom, formProblems, termsLabel } from "./customers";

const c: Customer = {
  id: "c1",
  external_ref: "C-1001",
  name: "Island Grocers",
  email: "ap@island.example",
  phone: null,
  group_id: "g1",
  payment_terms_days: 30,
  active: true,
  version: 3,
};

describe("customer form", () => {
  it("labels terms", () => {
    expect([termsLabel(0), termsLabel(1), termsLabel(30)]).toEqual(["Due on receipt", "Net 1 day", "Net 30 days"]);
  });

  it("checks fields like the server", () => {
    expect(formProblems(formFrom(c))).toEqual({});
    const bad = formProblems({ ...EMPTY_FORM, name: " ", email: "a@b", terms: "366", ref: "C 1" }, true);
    expect(Object.keys(bad).sort()).toEqual(["email", "name", "ref", "terms"]);
    expect(formProblems({ ...EMPTY_FORM, name: "X", terms: "1.5" }).terms).toBeDefined();
    expect(formProblems({ ...EMPTY_FORM, name: "X", email: "" })).toEqual({});
  });

  it("creates with trimmed values and nulls for empty", () => {
    expect(buildCreate({ ...EMPTY_FORM, ref: " C-9 ", name: " Spice ", email: " ", terms: "14" })).toEqual({
      external_ref: "C-9",
      name: "Spice",
      email: null,
      phone: null,
      group_id: null,
      payment_terms_days: 14,
    });
  });

  it("patches only what changed", () => {
    expect(buildPatch(c, formFrom(c))).toBeNull();
    expect(buildPatch(c, { ...formFrom(c), name: "Island Grocers " })).toBeNull(); // trailing space only
    expect(buildPatch(c, { ...formFrom(c), email: "", group_id: "", terms: "14", active: false })).toEqual({
      version: 3,
      email: "",
      clear_group: true,
      payment_terms_days: 14,
      active: false,
    });
    expect(buildPatch(c, { ...formFrom(c), phone: "416-555-0101", group_id: "g2" })).toEqual({ version: 3, phone: "416-555-0101", group_id: "g2" });
  });

  it("explains errors in plain words", () => {
    expect(customerMessage(new ApiError(409, "VERSION_CONFLICT", "x"))).toMatch(/Someone else changed/);
    expect(customerMessage(new ApiError(409, "DUPLICATE", "x"))).toMatch(/already uses this reference/);
    expect(customerMessage(new Error("boom"))).toBe("Something went wrong. Try again.");
  });
});
