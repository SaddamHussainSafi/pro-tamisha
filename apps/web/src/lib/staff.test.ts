import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import type { StaffMember } from "../api";
import { deviceLabel, eventLabel, filterStaff, groupPermissions, passwordProblem, permissionArea, roleLabel, ROLES, staffMessage, tempPassword, tokenFromHash } from "./staff";

describe("staff helpers", () => {
  it("checks passwords like the server", () => {
    expect(passwordProblem("short")).toMatch(/12 characters/);
    expect(passwordProblem("aaaaaaaaaaaaa")).toMatch(/less guessable/);
    expect(passwordProblem("sam@example.test", "Sam@Example.test")).toMatch(/email/);
    expect(passwordProblem("Fresh-Cassava-2026!")).toBeNull();
  });

  it("makes random 16-character temporary passwords that pass the rule", () => {
    const a = tempPassword();
    expect(a).toHaveLength(16);
    expect(a).not.toBe(tempPassword());
    expect(passwordProblem(a)).toBeNull();
    expect(tempPassword(() => new Uint32Array(16))).toBe("AAAAAAAAAAAAAAAA"); // deterministic source
  });

  it("labels roles and errors plainly", () => {
    expect([roleLabel("SALES_OPERATOR"), roleLabel("X")]).toEqual(["Sales", "X"]);
    expect(staffMessage(new ApiError(422, "SELF_CHANGE", "x"))).toMatch(/own account/);
    expect(staffMessage(new ApiError(409, "EMAIL_TAKEN", "x"))).toMatch(/already signs in/);
  });
});


const person = (over: Partial<StaffMember>): StaffMember => ({
  id: "1",
  email: "sam@example.test",
  display_name: "Sam Sales",
  active: true,
  status: "ACTIVE",
  roles: ["SALES_OPERATOR"],
  mfa_enrolled: false,
  locked: false,
  last_seen_at: null,
  last_login_at: null,
  invite_expires_at: null,
  created_at: "2026-11-09T00:00:00Z",
  ...over,
});

describe("user management helpers", () => {
  it("has the owner's six roles", () => {
    expect(ROLES.map((r) => r.label)).toEqual(["Super Admin", "Admin", "Sales", "Warehouse", "Accounting", "Viewer"]);
    expect(roleLabel("OWNER_ADMIN")).toBe("Super Admin");
    expect(roleLabel("FINANCE")).toBe("Accounting");
  });

  it("filters by search, role and status", () => {
    const rows = [
      person({ id: "a" }),
      person({ id: "b", display_name: "Wendy", email: "w@example.test", roles: ["WAREHOUSE"], status: "INVITED" }),
      person({ id: "c", display_name: "Vic", email: "vic@example.test", roles: ["VIEWER"], status: "SUSPENDED", active: false }),
    ];
    expect(filterStaff(rows, { q: "", role: "ALL", status: "ALL" }).map((r) => r.id)).toEqual(["a", "b", "c"]);
    expect(filterStaff(rows, { q: "W@EX", role: "ALL", status: "ALL" }).map((r) => r.id)).toEqual(["b"]);
    expect(filterStaff(rows, { q: "", role: "VIEWER", status: "ALL" }).map((r) => r.id)).toEqual(["c"]);
    expect(filterStaff(rows, { q: "", role: "ALL", status: "INVITED" }).map((r) => r.id)).toEqual(["b"]);
    expect(filterStaff(rows, { q: "sam", role: "WAREHOUSE", status: "ALL" })).toEqual([]);
  });

  it("groups permissions by area in a fixed order", () => {
    expect(permissionArea("employee.manage")).toBe("Administration");
    expect(permissionArea("sales_order.read")).toBe("Orders & picking");
    expect(permissionArea("supplier_payment.record")).toBe("Payments & suppliers");
    expect(permissionArea("mystery.thing")).toBe("Other");
    const g = groupPermissions([{ code: "report.read" }, { code: "catalog.read" }, { code: "employee.manage" }]);
    expect(g.map((x) => x.area)).toEqual(["Administration", "Products & customers", "Reports"]);
  });

  it("reads the link token only from the URL fragment", () => {
    const t = "abcDEF123_-abcDEF123_-abcDEF";
    expect(tokenFromHash(`#token=${t}`)).toBe(t);
    expect(tokenFromHash("#token=short")).toBeNull();
    expect(tokenFromHash("#token=bad<script>")).toBeNull();
    expect(tokenFromHash("")).toBeNull();
  });

  it("describes events and devices in plain words", () => {
    expect(eventLabel({ action: "auth.login", success: true, reason: null })).toBe("Signed in");
    expect(eventLabel({ action: "auth.login", success: false, reason: "bad_password" })).toBe("Signed in — failed: wrong password");
    expect(eventLabel({ action: "auth.mfa_enabled", success: true, reason: null })).toBe("Turned two-step sign-in on");
    expect(deviceLabel("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/131.0 Safari/537.36")).toBe("Chrome on macOS");
    expect(deviceLabel("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit Version/17.0 Safari/604.1")).toBe("Safari on iOS");
    expect(deviceLabel(null)).toBe("Unknown device");
  });

  it("explains auth errors", () => {
    expect(staffMessage(new ApiError(422, "LAST_OWNER", "x"))).toMatch(/Super Admin/);
    expect(staffMessage(new ApiError(400, "TOKEN_INVALID", "x"))).toMatch(/expired/);
    expect(staffMessage(new ApiError(403, "FORBIDDEN", "x"))).toMatch(/Super Admin/);
  });
});
