import { describe, expect, it } from "vitest";
import { readView, writeView } from "./viewMode";

function mem() {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), m };
}

describe("view mode", () => {
  it("remembers the choice per screen", () => {
    const s = mem();
    expect(readView("customers", "grid", s)).toBe("grid");
    writeView("customers", "list", s);
    expect(readView("customers", "grid", s)).toBe("list");
    expect(readView("products", "grid", s)).toBe("grid");
    expect(s.m.get("pt.view.customers")).toBe("list");
  });

  it("falls back to grid for unknown values or blocked storage", () => {
    const s = mem();
    s.setItem("pt.view.users", "kanban");
    expect(readView("users", "grid", s)).toBe("grid");
    const broken = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(readView("users", "grid", broken)).toBe("grid");
    expect(() => writeView("users", "list", broken)).not.toThrow();
    expect(readView("users", "list", null)).toBe("list");
  });
});
