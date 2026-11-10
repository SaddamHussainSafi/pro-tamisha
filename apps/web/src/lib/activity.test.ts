import { describe, expect, it } from "vitest";
import { activityIcon, appendPage, initials, sentence, whenParts, type ActivityItem } from "./activity";

const NOW = new Date(2026, 10, 9, 14, 0, 0);

function a(id: string, extra: Partial<ActivityItem> = {}): ActivityItem {
  return {
    id,
    at: NOW.toISOString(),
    action: "invoice.issue",
    actor: { id: "e1", name: "Sam Sales" },
    verb: "created an invoice",
    object: "INV-2026-000041",
    extra: null,
    link: null,
    tag: "Invoice Created",
    tone: "info",
    icon: "invoices",
    ...extra,
  };
}

describe("activity helpers", () => {
  it("labels days like the timeline", () => {
    expect(whenParts(new Date(2026, 10, 9, 10, 24).toISOString(), NOW)).toEqual({ day: "Today", time: "10:24 AM" });
    expect(whenParts(new Date(2026, 10, 8, 16, 37).toISOString(), NOW).day).toBe("Yesterday");
    expect(whenParts(new Date(2026, 9, 30, 11, 2).toISOString(), NOW).day).toBe("Oct 30");
    expect(whenParts(new Date(2025, 9, 30, 11, 2).toISOString(), NOW).day).toBe("Oct 30, 2025");
  });

  it("reads as one sentence", () => {
    expect(sentence(a("1"))).toBe("Sam Sales created an invoice — INV-2026-000041");
    expect(sentence(a("2", { extra: "CAD 125.00" }))).toBe("Sam Sales created an invoice — INV-2026-000041 (CAD 125.00)");
    expect(sentence(a("3", { object: null }))).toBe("Sam Sales created an invoice");
  });

  it("makes initials and falls back for unknown icons", () => {
    expect(initials("Olivia Emmanuel")).toBe("OE");
    expect(initials("Sonny")).toBe("S");
    expect(initials("")).toBe("?");
    expect(activityIcon("invoices")).toBe("invoices");
    expect(activityIcon("rocket")).toBe("clock");
  });

  it("appends older pages without repeats", () => {
    expect(appendPage([a("1"), a("2")], [a("2"), a("3")]).map((x) => x.id)).toEqual(["1", "2", "3"]);
  });
});
