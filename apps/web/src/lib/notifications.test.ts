import { describe, expect, it } from "vitest";
import { badge, dayKey, desktopState, groupByDay, merge, relativeTime, safeLink, shouldShowDesktop, stack } from "./notifications";
import type { AppNotification } from "./notificationsApi";

const NOW = new Date(2026, 10, 9, 14, 0, 0); // 9 Nov 2026, 14:00 local

function n(id: string, at: Date, source = "Orders", seq = 0): AppNotification {
  return {
    id,
    seq,
    kind: "test",
    category: "sales_order",
    category_label: "Sales & warehouse",
    severity: "info",
    title: id,
    body: "",
    source_module: source,
    link: null,
    target_type: null,
    target_id: null,
    created_at: at.toISOString(),
    read: false,
    read_at: null,
    archived: false,
  };
}

describe("day groups", () => {
  it("splits Today / Yesterday / Earlier on local midnight", () => {
    expect(dayKey(new Date(2026, 10, 9, 0, 0, 1).toISOString(), NOW)).toBe("today");
    expect(dayKey(new Date(2026, 10, 8, 23, 59).toISOString(), NOW)).toBe("yesterday");
    expect(dayKey(new Date(2026, 10, 8, 0, 0).toISOString(), NOW)).toBe("yesterday");
    expect(dayKey(new Date(2026, 10, 7, 23, 59).toISOString(), NOW)).toBe("earlier");
  });

  it("keeps newest-first order inside groups and drops empty groups", () => {
    const items = [n("a", new Date(2026, 10, 9, 13)), n("b", new Date(2026, 10, 9, 9)), n("c", new Date(2026, 10, 1))];
    const g = groupByDay(items, NOW);
    expect(g.map((x) => x.label)).toEqual(["Today", "Earlier"]);
    expect(g[0]?.items.map((x) => x.id)).toEqual(["a", "b"]);
  });
});

describe("stacks", () => {
  const at = new Date(2026, 10, 9, 12);
  const items = [n("1", at, "Receiving"), n("2", at, "Receiving"), n("3", at, "Receiving"), n("4", at, "Orders"), n("5", at, "Receiving")];

  it("collapses 3+ consecutive items from one source when grouping is on", () => {
    const e = stack(items, true);
    expect(e.map((x) => x.type)).toEqual(["stack", "single", "single"]);
    const first = e[0];
    expect(first?.type === "stack" ? first.items.map((x) => x.id) : null).toEqual(["1", "2", "3"]);
  });

  it("never stacks when grouping is off", () => {
    expect(stack(items, false).every((x) => x.type === "single")).toBe(true);
  });
});

describe("relative time", () => {
  it("reads like a notification centre", () => {
    expect(relativeTime(new Date(NOW.getTime() - 20_000).toISOString(), NOW)).toBe("now");
    expect(relativeTime(new Date(NOW.getTime() - 5 * 60_000).toISOString(), NOW)).toBe("5m ago");
    expect(relativeTime(new Date(NOW.getTime() - 3 * 3600_000).toISOString(), NOW)).toBe("3h ago");
    expect(relativeTime(new Date(2026, 10, 8, 9, 5).toISOString(), NOW)).toMatch(/^Yesterday /);
    expect(relativeTime(new Date(2026, 10, 2, 9, 5).toISOString(), NOW)).toMatch(/Nov/);
  });
});

describe("misc", () => {
  it("caps the badge", () => {
    expect(badge(7)).toBe("7");
    expect(badge(120)).toBe("99+");
  });

  it("follows in-app links only", () => {
    expect(safeLink("/orders/1")).toBe("/orders/1");
    expect(safeLink("//evil.example")).toBeNull();
    expect(safeLink("https://evil.example")).toBeNull();
    expect(safeLink("javascript:alert(1)")).toBeNull();
    expect(safeLink(null)).toBeNull();
  });

  it("merges live items without duplicates, newest first", () => {
    const a = { ...n("a", NOW), seq: 1 };
    const b = { ...n("b", NOW), seq: 2 };
    expect(merge([a], [b, a]).map((x) => x.id)).toEqual(["b", "a"]);
  });
});

describe("browser permission", () => {
  it("detects support and the current permission", () => {
    expect(desktopState({})).toBe("unsupported");
    expect(desktopState({ Notification: { permission: "default" } })).toBe("default");
    expect(desktopState({ Notification: { permission: "denied" } })).toBe("denied");
  });

  it("shows a desktop notification only when every condition holds", () => {
    const ok = { prefOn: true, state: "granted" as const, alert: true, visible: false };
    expect(shouldShowDesktop(ok)).toBe(true);
    expect(shouldShowDesktop({ ...ok, prefOn: false })).toBe(false);
    expect(shouldShowDesktop({ ...ok, state: "default" })).toBe(false);
    expect(shouldShowDesktop({ ...ok, state: "denied" })).toBe(false);
    expect(shouldShowDesktop({ ...ok, alert: false })).toBe(false); // quiet hours / category off
    expect(shouldShowDesktop({ ...ok, visible: true })).toBe(false); // the app is in front
  });
});
