import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { passwordProblem, roleLabel, staffMessage, tempPassword } from "./staff";

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
