import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { DOC_KIND, documentMessage, linkedHref, linkedTo, SYNC_STATUS, syncProblem } from "./documents";

const none = { invoice_no: null, customer_name: null, shipment_no: null, vendor_name: null, scan_job_no: null };

describe("status and kind labels", () => {
  it("covers every sync status and kind", () => {
    expect(Object.keys(SYNC_STATUS).sort()).toEqual(["FAILED", "LOCAL_ONLY", "PENDING", "SYNCED"]);
    expect(SYNC_STATUS.FAILED.tone).toBe("danger");
    expect(SYNC_STATUS.PENDING.label).toBe("Pending sync");
    expect(Object.keys(DOC_KIND)).toHaveLength(4);
  });
});

describe("linkedTo / linkedHref", () => {
  it("prefers invoice, then shipment, then scan", () => {
    expect(linkedTo({ ...none, invoice_no: "INV-1", customer_name: "Island Grocers" })).toBe("INV-1 · Island Grocers");
    expect(linkedTo({ ...none, shipment_no: "SH-1", vendor_name: "Accra Roots" })).toBe("SH-1 · Accra Roots");
    expect(linkedTo({ ...none, scan_job_no: "SC-1" })).toBe("SC-1");
    expect(linkedTo(none)).toBe("—");
    expect(linkedHref({ invoice_id: "a", shipment_id: null, scan_job_id: null })).toBe("/invoices/a");
    expect(linkedHref({ invoice_id: null, shipment_id: "b", scan_job_id: null })).toBe("/purchases/b");
    expect(linkedHref({ invoice_id: null, shipment_id: null, scan_job_id: "c" })).toBe("/scan/c");
    expect(linkedHref({ invoice_id: null, shipment_id: null, scan_job_id: null })).toBeNull();
  });
});

describe("syncProblem", () => {
  it("explains known error codes in plain language", () => {
    expect(syncProblem(null)).toBeNull();
    expect(syncProblem("DRIVE_TIMEOUT: Drive did not answer in time")).toMatch(/tried again automatically/);
    expect(syncProblem("DRIVE_DENIED: refused")).toMatch(/sharing settings/);
    expect(syncProblem("weird")).toMatch(/did not finish/);
  });
});

describe("documentMessage", () => {
  it("maps API codes", () => {
    expect(documentMessage(new ApiError(409, "VERSION_CONFLICT", "x"))).toMatch(/changed/);
    expect(documentMessage(new ApiError(409, "INVALID_STATE", "x"))).toMatch(/Only a failed copy/);
    expect(documentMessage(new ApiError(413, "FILE_TOO_LARGE", "x", { max_bytes: 1048576 }))).toBe("That file is too large. The limit is 1.0 MB.");
    expect(documentMessage(new ApiError(415, "FILE_TYPE_MISMATCH", "x"))).toMatch(/PDF, PNG/);
    expect(documentMessage(new Error("boom"))).toMatch(/Something unexpected.*Reload the page/);
  });
});
