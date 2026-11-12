import { describe, expect, it } from "vitest";
import { highlightParts, mailboxState, mailDocument, parseAddrs, quoteOriginal, replyRecipients, replySubject, validEmail, words } from "./mail";

const msg = {
  from_name: "Maria Lopez",
  from_email: "maria@greenleaf.example",
  reply_to: [],
  to: [{ name: "", email: "andy@tamishatrading.com" }, { name: "Bob", email: "bob@x.example" }],
  cc: [{ name: "", email: "ANDY@tamishatrading.com" }, { name: "", email: "c@x.example" }],
  received_at: "2026-11-10T10:00:00Z",
  subject: "Re: RE: Order",
  body_html: "<p>Hi <b>Andy</b></p>",
  body_text: "Hi Andy",
};

describe("mail helpers", () => {
  it("never calls a mailbox connected unless the API says CONNECTED", () => {
    expect(mailboxState({ status: "CONNECTED", last_sync_at: null }).label).toBe("Connected");
    for (const s of ["PAUSED", "ERROR", "AUTH_FAILED", "NOT_CONFIGURED", "SOMETHING_NEW"]) expect(mailboxState({ status: s, last_sync_at: null }).label).not.toBe("Connected");
    expect(mailboxState({ status: "AUTH_FAILED", last_sync_at: null }).label).toBe("Password rejected");
    expect(mailboxState({ status: "NOT_CONFIGURED", last_sync_at: null }).detail).toBe("Not connected yet — a Super Admin enters the Lark app password in Settings > Email Services.");
  });

  it("parses and validates addresses", () => {
    expect(parseAddrs('Maria <m@x.com>, b@y.org; "Z, Q" <z@q.io>')).toEqual([
      { name: "Maria", email: "m@x.com" },
      { name: "", email: "b@y.org" },
      { name: "", email: '"Z' },
      { name: "Q", email: "z@q.io" },
    ]);
    expect(validEmail("m@x.com")).toBe(true);
    expect(validEmail("not-an-address")).toBe(false);
    expect(validEmail("a@b")).toBe(false);
  });

  it("builds reply subjects and recipients without me or duplicates", () => {
    expect(replySubject(msg.subject, "reply")).toBe("Re: Order");
    expect(replySubject("Fwd: x", "forward")).toBe("Fwd: x");
    expect(replyRecipients(msg, "reply", "andy@tamishatrading.com")).toEqual({ to: [{ name: "Maria Lopez", email: "maria@greenleaf.example" }], cc: [] });
    expect(replyRecipients(msg, "reply_all", "andy@tamishatrading.com")).toEqual({
      to: [{ name: "Maria Lopez", email: "maria@greenleaf.example" }, { name: "Bob", email: "bob@x.example" }],
      cc: [{ name: "", email: "c@x.example" }],
    });
    expect(replyRecipients({ ...msg, reply_to: [{ name: "", email: "orders@greenleaf.example" }] }, "reply", "x@y.z").to[0]?.email).toBe("orders@greenleaf.example");
  });

  it("quotes the original (escaped headers) under a reply or forward", () => {
    const q = quoteOriginal({ ...msg, from_name: "<script>" }, "reply");
    expect(q).toContain("&lt;script&gt;");
    expect(q).toContain("<blockquote><p>Hi <b>Andy</b></p></blockquote>");
    expect(quoteOriginal(msg, "forward")).toContain("Forwarded message");
  });

  it("highlights search words case-insensitively", () => {
    expect(words("Northside  market!")).toEqual(["northside", "market"]);
    expect(highlightParts("Delivery Tuesday — Northside Market", ["northside"])).toEqual([
      { text: "Delivery Tuesday — ", hit: false },
      { text: "Northside", hit: true },
      { text: " Market", hit: false },
    ]);
    expect(highlightParts("a.b", ["."])).toEqual([{ text: "a", hit: false }, { text: ".", hit: true }, { text: "b", hit: false }]);
  });

  it("keeps remote pictures blocked in the frame until asked for", () => {
    expect(mailDocument("<p>x</p>", false)).toContain("img-src data:\"");
    expect(mailDocument("<p>x</p>", true)).toContain("img-src data: https: http:");
    expect(mailDocument("<p>x</p>", false)).toContain("default-src 'none'");
  });
});
