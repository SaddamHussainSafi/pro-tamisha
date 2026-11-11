import { describe, expect, it } from "vitest";
import { ApiError } from "../api";
import { ago, bodyDocument, connection, gmailMessage, listQuery, NO_FILTERS, pickable, previousOf, senderOf, whenShort, type GmailAttachment, type GmailStatus } from "./gmail";

const status = (over: Partial<GmailStatus> = {}): GmailStatus => ({
  configured: true,
  push_configured: false,
  poll_seconds: 60,
  scope: "https://www.googleapis.com/auth/gmail.readonly",
  redirect_uri: "",
  account: null,
  message_count: 0,
  unread_count: 0,
  can_manage: false,
  can_read_body: true,
  can_preview: true,
  can_download: true,
  can_process: true,
  can_reprocess: false,
  ...over,
});
const account = (over: Partial<NonNullable<GmailStatus["account"]>> = {}) => ({
  id: "a",
  email_address: "scans@example.test",
  status: "CONNECTED" as const,
  connected_at: "2026-11-01T10:00:00Z",
  connected_by_name: "Olivia",
  last_sync_at: "2026-11-11T10:00:00Z",
  last_error_code: null,
  last_error: null,
  last_error_at: null,
  watch_expires_at: null,
  last_push_at: null,
  backfill_done: true,
  version: 1,
  ...over,
});

describe("gmail list query", () => {
  it("sends only what the filter row asks for", () => {
    expect(listQuery(NO_FILTERS)).toBe("limit=30");
    expect(listQuery({ ...NO_FILTERS, q: "  yam  ", range: "7d", show: "pdf" }, "30")).toBe("q=yam&range=7d&pdf=true&cursor=30&limit=30");
    expect(listQuery({ ...NO_FILTERS, show: "not_processed" })).toBe("processed=false&limit=30");
    expect(listQuery({ ...NO_FILTERS, show: "processed" })).toBe("processed=true&limit=30");
    expect(listQuery({ ...NO_FILTERS, show: "attachments" })).toBe("has_attachments=true&limit=30");
  });
  it("sends custom dates only for the custom range", () => {
    expect(listQuery({ ...NO_FILTERS, range: "custom", from: "2026-11-01", to: "2026-11-05" })).toBe("range=custom&date_from=2026-11-01&date_to=2026-11-05&limit=30");
    expect(listQuery({ ...NO_FILTERS, range: "today", from: "2026-11-01", to: "2026-11-05" })).toBe("range=today&limit=30");
  });
});

describe("gmail connection wording", () => {
  it("never calls an unconfigured or unconnected mailbox connected", () => {
    expect(connection(status({ configured: false })).label).toBe("Not set up");
    expect(connection(status()).label).toBe("Not connected");
    expect(connection(null).label).toBe("Checking");
  });
  it("says how new mail arrives", () => {
    expect(connection(status({ account: account() }))).toMatchObject({ tone: "success", label: "Connected", detail: "Checked every minute." });
    expect(connection(status({ poll_seconds: 90, account: account() })).detail).toBe("Checked every 90 seconds.");
    expect(connection(status({ push_configured: true, account: account({ watch_expires_at: "2026-11-18T00:00:00Z" }) })).detail).toBe("New mail arrives live.");
  });
  it("shows sync trouble and a revoked sign-in", () => {
    expect(connection(status({ account: account({ status: "ERROR", last_error: "Google answered 429 after 3 tries" }) }))).toMatchObject({ tone: "warning", label: "Not syncing" });
    expect(connection(status({ account: account({ status: "REAUTH_REQUIRED" }) }))).toMatchObject({ tone: "danger", label: "Disconnected" });
  });
});

describe("gmail dates", () => {
  const now = new Date(2026, 10, 11, 15, 0);
  it("shows the time today, then the day", () => {
    expect(whenShort(new Date(2026, 10, 11, 9, 5).toISOString(), now)).toMatch(/9:05/);
    expect(whenShort(new Date(2026, 10, 10, 23, 59).toISOString(), now)).toBe("Yesterday");
    expect(whenShort(new Date(2026, 10, 3, 12).toISOString(), now)).toMatch(/Nov 3/);
    expect(whenShort(new Date(2025, 4, 3, 12).toISOString(), now)).toMatch(/2025/);
  });
  it("says how long ago the mailbox was checked", () => {
    const t = now.getTime();
    expect(ago(null, t)).toBe("never");
    expect(ago(new Date(t - 20_000).toISOString(), t)).toBe("just now");
    expect(ago(new Date(t - 5 * 60_000).toISOString(), t)).toBe("5 min ago");
    expect(ago(new Date(t - 3 * 3_600_000).toISOString(), t)).toBe("3 h ago");
  });
});

describe("gmail helpers", () => {
  const att = (over: Partial<GmailAttachment>): GmailAttachment => ({ id: "1", message_id: "m", filename: "a.pdf", mime_type: "application/pdf", byte_size: 1, inline: false, eligible: true, unsupported_reason: null, page_count: null, sha256: null, state: "NOT_PROCESSED", ...over });
  it("lists real attachments before pictures inside the message", () => {
    const files = pickable({ attachments: [att({ id: "logo", inline: true }), att({ id: "sheet" })] });
    expect(files.map((a) => a.id)).toEqual(["sheet", "logo"]);
  });
  it("names the sender", () => {
    expect(senderOf({ from_name: "Maria", from_email: "m@example.test" })).toBe("Maria");
    expect(senderOf({ from_name: "", from_email: "m@example.test" })).toBe("m@example.test");
    expect(senderOf({ from_name: "", from_email: "" })).toBe("Unknown sender");
  });
  it("puts server refusals in plain words and keeps the earlier uses", () => {
    expect(gmailMessage(new ApiError(415, "UNSUPPORTED_FILE", "x"))).toMatch(/PDF, PNG, JPEG or TIFF/);
    expect(gmailMessage(new ApiError(409, "SOMETHING_NEW", "Server said this"))).toBe("Server said this");
    const prev = [{ scan_job_id: "s1", scan_job_no: "SCN-1" }];
    expect(previousOf(new ApiError(409, "ALREADY_PROCESSED", "x", { previous: prev }))).toEqual(prev);
    expect(previousOf(new ApiError(409, "ALREADY_PROCESSED", "x"))).toEqual([]);
    expect(previousOf(new Error("x"))).toEqual([]);
  });
  it("wraps an email body in a document that can't load or run anything", () => {
    const doc = bodyDocument("<p>Hello</p>");
    expect(doc).toContain(`content="default-src 'none'; style-src 'unsafe-inline'"`);
    expect(doc).toContain("<p>Hello</p>");
    expect(doc).not.toContain("<script");
  });
});
