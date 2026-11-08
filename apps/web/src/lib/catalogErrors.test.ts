import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { catalogMessage } from "./catalogErrors";

describe("catalogMessage", () => {
  it("explains version conflicts with a reload hint", () => {
    expect(catalogMessage(new ApiError(409, "VERSION_CONFLICT", "stale", { current_version: 3 }))).toMatch(/latest version has been loaded/);
  });
  it("names the failing weight group (1-based)", () => {
    expect(catalogMessage(new ApiError(422, "UNKNOWN_CONVERSION", "x", { group: 1 }))).toMatch(/group 2/);
    expect(catalogMessage(new ApiError(422, "INVALID_QUANTITY", "x", { group: 0 }))).toMatch(/Group 1/);
  });
  it("falls back for unknown errors", () => {
    expect(catalogMessage(new Error("boom"))).toBe("Something went wrong. Try again.");
  });
});
