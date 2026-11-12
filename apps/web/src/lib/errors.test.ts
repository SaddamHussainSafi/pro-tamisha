import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, apiRequest, recentFailure } from "../api";
import { catalogMessage } from "./catalogErrors";
import { customerMessage } from "./customers";
import { documentMessage } from "./documents";
import { describeError, errorText, GENERIC_CODES, isChunkError, isPlain, keepEdits, needsSignIn, shortRef } from "./errors";
import { gmailMessage } from "./gmail";
import { inventoryMessage } from "./inventory";
import { invoiceMessage } from "./invoices";
import { ocrMessage } from "./ocr";
import { ordersMessage } from "./orders";
import { paymentMessage } from "./payments";
import { pricingMessage } from "./pricingErrors";
import { receivingMessage } from "./receiving";
import { scanMessage } from "./scans";
import { staffMessage } from "./staff";
import { supplierMessage } from "./suppliers";
import { trashErrorText } from "./trash";

const RAW = /[A-Z]{3,}_[A-Z_]+|Traceback|Exception|undefined|\bnull\b|Something went wrong\.?$/;

describe("describeError: every shared code", () => {
  it.each(GENERIC_CODES)("%s has a title, plain message, next step and action", (code) => {
    const i = describeError(new ApiError(400, code, "server text", {}, "POST"));
    expect(i.title).toBeTruthy();
    expect(i.message).toBeTruthy();
    expect(i.next).toBeTruthy();
    expect(["retry", "reload", "signin", "fix", "admin", "wait", "check"]).toContain(i.action);
    expect(errorText(new ApiError(400, code, "server text"))).not.toMatch(RAW);
  });

  it("offers the right next step", () => {
    const a = (code: string, status = 400) => describeError(new ApiError(status, code, "x")).action;
    expect(a("VERSION_CONFLICT", 409)).toBe("reload");
    expect(a("SESSION_INVALID", 401)).toBe("signin");
    expect(a("CSRF_FAILED", 403)).toBe("signin");
    expect(a("FORBIDDEN", 403)).toBe("admin");
    expect(a("VALIDATION", 422)).toBe("fix");
    expect(a("RATE_LIMITED", 429)).toBe("wait");
    expect(a("INTERNAL", 500)).toBe("retry");
    expect(a("DB_UNAVAILABLE", 503)).toBe("wait");
    expect(needsSignIn(new ApiError(401, "UNAUTHENTICATED", "x"))).toBe(true);
    expect(needsSignIn(new ApiError(403, "FORBIDDEN", "x"))).toBe(false);
  });

  it("replaces technical server text for shared codes", () => {
    expect(errorText(new ApiError(403, "CSRF_FAILED", "Missing or invalid CSRF token"))).not.toMatch(/CSRF/);
    expect(errorText(new ApiError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency-Key reused with different body"))).not.toMatch(/Idempotency/);
    expect(errorText(new ApiError(409, "VERSION_CONFLICT", "Product changed; reload and retry"))).toMatch(/Someone else changed this/);
  });
});

describe("describeError: whether anything was saved", () => {
  it("a refused write says nothing was saved", () => {
    expect(errorText(new ApiError(409, "VERSION_CONFLICT", "x", {}, "PATCH"))).toMatch(/Nothing was saved/);
    expect(errorText(new ApiError(403, "FORBIDDEN", "x", {}, "POST"))).toMatch(/Nothing was saved.*Ask an owner or admin/);
    expect(errorText(new ApiError(503, "DB_UNAVAILABLE", "x", {}, "POST"))).toMatch(/Nothing was saved/);
  });
  it("a write with no answer says to check before trying again", () => {
    expect(errorText(new ApiError(0, "NETWORK", "x", {}, "POST"))).toMatch(/check the list before trying again/);
    expect(errorText(new ApiError(0, "TIMEOUT", "x", {}, "POST"))).toMatch(/couldn't confirm whether it was saved/);
    expect(errorText(new ApiError(500, "INTERNAL", "x", {}, "POST"))).toMatch(/couldn't confirm/);
  });
  it("loading a page doesn't talk about saving", () => {
    expect(describeError(new ApiError(500, "INTERNAL", "x", {}, "GET")).saved).toBeNull();
    expect(errorText(new ApiError(404, "NOT_FOUND", "x", {}, "GET"))).not.toMatch(/saved/);
  });
});

describe("describeError: feature and unknown codes", () => {
  it("keeps a plain feature message and adds the saved state", () => {
    const e = new ApiError(409, "LOT_NOT_SALEABLE", "Lot L-1 is not saleable", {}, "POST");
    expect(errorText(e)).toBe("Lot L-1 is not saleable. Nothing was saved.");
    expect(errorText(new ApiError(409, "LOT_NOT_SALEABLE", "Lot L-1 is not saleable"))).toBe("Lot L-1 is not saleable");
  });
  it("unknown codes with technical or empty text fall back to safe words by status", () => {
    expect(errorText(new ApiError(418, "WHATEVER", ""))).toMatch(/couldn't complete this/);
    expect(errorText(new ApiError(409, "NEW_CODE", "Conflict"))).toMatch(/can't be done in its current state/);
    expect(errorText(new ApiError(422, "NEW_CODE", "value is not a valid UUID: undefined"))).toMatch(/missing or not valid/);
    expect(errorText(new ApiError(502, "NEW_CODE", "Bad Gateway"))).toMatch(/server is down or restarting/);
    expect(errorText(new ApiError(500, "NEW_CODE", "Traceback (most recent call last)"))).not.toMatch(/Traceback/);
  });
  it("server problems carry a short support reference", () => {
    const e = new ApiError(500, "INTERNAL", "x", { request_id: "1a2b3c4d-5e6f-7a8b-9c0d" }, "GET");
    expect(describeError(e).requestId).toBe("1a2b3c4d-5e6f-7a8b-9c0d");
    expect(errorText(e)).toMatch(/Reference: 1a2b3c4d\./);
    expect(shortRef(null)).toBe("");
    expect(errorText(new ApiError(422, "VALIDATION", "x", { request_id: "r" }))).not.toMatch(/Reference/);
  });
  it("names the fields to highlight", () => {
    const e = new ApiError(422, "VALIDATION", "x", { fields: [{ loc: ["body", "password"], field: "password", msg: "Field required" }, { loc: ["body", "email"], msg: "Value error, bad email" }] });
    expect(describeError(e).fields).toEqual({ password: "Field required", email: "bad email" });
  });
  it("non-API failures: offline, network, screen bugs", () => {
    expect(describeError(new TypeError("Failed to fetch")).code).toBe("NETWORK");
    expect(describeError(new Error("x is undefined")).code).toBe("CLIENT");
    expect(errorText("weird")).toMatch(/Something unexpected/);
    vi.stubGlobal("navigator", { onLine: false });
    expect(describeError(new TypeError("Failed to fetch")).title).toBe("You're offline");
    vi.unstubAllGlobals();
  });
  it("isPlain and chunk errors", () => {
    expect(isPlain("Choose a new password")).toBe(true);
    expect(isPlain("NOT_FOUND")).toBe(false);
    expect(isPlain("Not Found")).toBe(false);
    expect(isPlain("")).toBe(false);
    expect(isChunkError(new TypeError("Failed to fetch dynamically imported module: /src/pages/Orders.tsx"))).toBe(true);
    expect(isChunkError(new Error("boom"))).toBe(false);
  });
});

describe("every feature helper falls back safely", () => {
  const helpers: [string, (e: unknown) => string][] = [
    ["catalog", catalogMessage],
    ["customers", customerMessage],
    ["documents", documentMessage],
    ["gmail", gmailMessage],
    ["inventory", inventoryMessage],
    ["invoices", invoiceMessage],
    ["ocr", ocrMessage],
    ["orders", ordersMessage],
    ["payments", paymentMessage],
    ["pricing", pricingMessage],
    ["receiving", receivingMessage],
    ["scans", scanMessage],
    ["staff", staffMessage],
    ["suppliers", supplierMessage],
    ["trash", trashErrorText],
  ];
  it.each(helpers)("%s: unknown code, technical text, network, crash", (_n, fn) => {
    for (const e of [
      new ApiError(500, "INTERNAL", "Something went wrong", { request_id: "abcdef12-0000" }, "POST"),
      new ApiError(502, "SERVER_UNREACHABLE", "Bad Gateway", {}, "GET"),
      new ApiError(0, "NETWORK", "No answer", {}, "POST"),
      new ApiError(409, "BRAND_NEW_CODE", "Conflict", {}, "POST"),
      new Error("boom"),
    ]) {
      const t = fn(e);
      expect(t.length).toBeGreaterThan(10);
      expect(t).not.toMatch(RAW);
    }
    expect(fn(new ApiError(409, "BRAND_NEW_CODE", "This thing is closed", {}, "POST"))).toMatch(/This thing is closed/);
  });
});

describe("api request wrapper", () => {
  afterEach(() => vi.unstubAllGlobals());
  const respond = (status: number, body: unknown, headers: Record<string, string> = {}) =>
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body === undefined ? "" : typeof body === "string" ? body : JSON.stringify(body), { status, headers })));

  it("turns the envelope into an ApiError with method and request id", async () => {
    respond(409, { error: { code: "VERSION_CONFLICT", message: "changed", request_id: "rid-1" } });
    const e = (await apiRequest("GET", "/x").catch((x: unknown) => x)) as ApiError;
    expect(e).toBeInstanceOf(ApiError);
    expect([e.status, e.code, e.method, e.requestId]).toEqual([409, "VERSION_CONFLICT", "GET", "rid-1"]);
    expect(recentFailure()).toBe(e);
  });
  it("a 5xx with no envelope means the server isn't answering", async () => {
    respond(502, "<html>proxy error</html>", { "X-Request-Id": "rid-h" });
    const e = (await apiRequest("GET", "/x").catch((x: unknown) => x)) as ApiError;
    expect(e.code).toBe("SERVER_UNREACHABLE");
    expect(e.requestId).toBe("rid-h");
  });
  it("no answer at all becomes NETWORK, OFFLINE or TIMEOUT", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    expect(((await apiRequest("GET", "/x").catch((x: unknown) => x)) as ApiError).code).toBe("NETWORK");
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(Object.assign(new Error("t"), { name: "TimeoutError" }))));
    expect(((await apiRequest("GET", "/x").catch((x: unknown) => x)) as ApiError).code).toBe("TIMEOUT");
    vi.stubGlobal("navigator", { onLine: false });
    vi.stubGlobal("fetch", vi.fn(async () => Promise.reject(new TypeError("Failed to fetch"))));
    expect(((await apiRequest("GET", "/x").catch((x: unknown) => x)) as ApiError).code).toBe("OFFLINE");
  });
});

describe("keepEdits after a version conflict", () => {
  it("shows the other person's change and keeps what was typed", () => {
    const original = { phone: "", terms: "14", email: "a@x.test" };
    const edited = { phone: "416-555-0101", terms: "14", email: "a@x.test" };
    const latest = { phone: "", terms: "30", email: "a@x.test" };
    expect(keepEdits(original, edited, latest)).toEqual({ phone: "416-555-0101", terms: "30", email: "a@x.test" });
  });
});
