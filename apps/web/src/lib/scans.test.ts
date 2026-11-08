import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { BridgeError, bridgeScan, bridgeStatus, checkFile, failHint, fileSize, isOpen, scanMessage } from "./scans";

const job = { id: "11111111-1111-1111-1111-111111111111", capability: "c".repeat(40) };
const reply = (status: number, body: unknown) => (async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

describe("fileSize", () => {
  it("formats bytes without floats in output", () => {
    expect(fileSize(null)).toBe("—");
    expect(fileSize(512)).toBe("512 B");
    expect(fileSize(1024)).toBe("1.0 KB");
    expect(fileSize(1536)).toBe("1.5 KB");
    expect(fileSize(20 * 1024 * 1024)).toBe("20.0 MB");
  });
});

describe("checkFile", () => {
  it("accepts pdf and images", () => {
    expect(checkFile({ name: "a.pdf", size: 10, type: "application/pdf" })).toBeNull();
    expect(checkFile({ name: "a.jpg", size: 10, type: "image/jpeg" })).toBeNull();
    expect(checkFile({ name: "a", size: 10, type: "" })).toBeNull(); // server sniffs
  });
  it("rejects empty, huge and wrong types", () => {
    expect(checkFile({ name: "a.pdf", size: 0, type: "application/pdf" })).toMatch(/empty/);
    expect(checkFile({ name: "a.pdf", size: 21 * 1024 * 1024, type: "application/pdf" })).toMatch(/limit/);
    expect(checkFile({ name: "a.docx", size: 10, type: "application/msword" })).toMatch(/Only PDF/);
  });
});

describe("status helpers", () => {
  it("treats requested and scanning as open", () => {
    expect(isOpen({ status: "REQUESTED" })).toBe(true);
    expect(isOpen({ status: "SCANNING" })).toBe(true);
    expect(isOpen({ status: "UPLOADED" })).toBe(false);
  });
  it("gives a recovery hint for every failure code", () => {
    expect(failHint("SCANNER_NOT_FOUND")).toMatch(/unplugged/);
    expect(failHint("WHATEVER")).toMatch(/upload/);
    expect(failHint(null)).toMatch(/upload/);
  });
});

describe("scanMessage", () => {
  it("maps API errors", () => {
    expect(scanMessage(new ApiError(422, "FINGERPRINT_MISMATCH", "x"))).toMatch(/doesn't match/);
    expect(scanMessage(new ApiError(413, "FILE_TOO_LARGE", "x", { max_bytes: 1024 }))).toMatch(/1.0 KB/);
    expect(scanMessage(new ApiError(403, "FORBIDDEN", "x"))).toMatch(/permission/);
  });
  it("maps bridge errors", () => {
    expect(scanMessage(new BridgeError(0, "UNREACHABLE", ""))).toMatch(/upload the file instead/);
    expect(scanMessage(new BridgeError(403, "ORIGIN_REJECTED", ""))).toMatch(/PT_ALLOWED_ORIGINS/);
  });
});

describe("bridgeScan", () => {
  it("posts job id + capability and returns the job", async () => {
    let sent: RequestInit | undefined;
    let url = "";
    const f = (async (u: string, init: RequestInit) => {
      url = u;
      sent = init;
      return new Response(JSON.stringify({ id: job.id, status: "UPLOADED" }), { status: 200 });
    }) as unknown as typeof fetch;
    const r = await bridgeScan(job, f, "http://127.0.0.1:47615");
    expect(url).toBe("http://127.0.0.1:47615/v1/scan");
    expect(JSON.parse(String(sent?.body))).toEqual({ job_id: job.id, capability: job.capability });
    expect(r.status).toBe("UPLOADED");
  });
  it("raises bridge codes", async () => {
    await expect(bridgeScan(job, reply(409, { code: "BUSY", message: "busy" }))).rejects.toMatchObject({ code: "BUSY", status: 409 });
  });
  it("network failure is UNREACHABLE", async () => {
    const f = (async () => {
      throw new TypeError("Failed to fetch");
    }) as unknown as typeof fetch;
    await expect(bridgeScan(job, f)).rejects.toMatchObject({ code: "UNREACHABLE" });
    expect(await bridgeStatus(f)).toBeNull();
  });
});
