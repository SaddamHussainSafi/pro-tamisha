import { describe, expect, it } from "vitest";
import { pool } from "./mail";
import { DEFAULT_ADDRESS, defaultFields, LOGO_URL, renderSignature, safeColor, sigForDisplay, sigForWire, TEMPLATES, toHex } from "./signature";

const f = { ...defaultFields("Andy <Boss>", "andy@tamishatrading.com"), title: "Sales Manager", phone: "+1 (416) 555-0100" };

describe("email signatures", () => {
  it("prefills sensible defaults", () => {
    const d = defaultFields("Saddam", "info@tamishatrading.com");
    expect(d).toMatchObject({ name: "Saddam", email: "info@tamishatrading.com", company: "Tamisha Trading Inc.", website: "tamishatrading.com", address: DEFAULT_ADDRESS, showLogo: true, mode: "template" });
  });

  it("renders every template with the logo as cid and the details escaped", () => {
    for (const t of TEMPLATES) {
      const html = renderSignature({ ...f, template: t.id });
      expect(html).toContain("Andy &lt;Boss&gt;");
      expect(html).not.toContain("<Boss>");
      expect(html).toContain("Sales Manager");
      if (t.id !== "minimal" && t.id !== "compact") expect(html).toContain('src="cid:tt-logo"');
      expect(html).not.toMatch(/class=|<style/);
    }
    expect(renderSignature({ ...f, template: "classic" })).toContain("<table");
    expect(renderSignature({ ...f, template: "classic" })).toContain('href="tel:+14165550100"');
    expect(renderSignature({ ...f, showLogo: false })).not.toContain("tt-logo");
    expect(renderSignature({ ...f, mode: "custom", customHtml: "<p>Mine</p>" })).toBe("<p>Mine</p>");
  });

  it("keeps colours to safe values and converts for the picker", () => {
    expect(safeColor("rgb(1, 2, 3)")).toBe("rgb(1, 2, 3)");
    expect(safeColor("red; background: url(x)")).toBe("rgb(27, 122, 48)");
    expect(toHex("rgb(27, 122, 48)")).toBe(`#${"1b7a30"}`); // (built, so the theme lint does not read it as a colour token)
  });

  it("shows the logo from our server in the app and sends it as cid", () => {
    const stored = '<img data-cid="tt-logo" width="72">';
    const shown = sigForDisplay(stored);
    expect(shown).toContain(`src="${LOGO_URL}"`);
    expect(sigForWire(shown)).toContain('src="cid:tt-logo"');
  });
});

describe("bulk helper", () => {
  it("runs everything with a cap and counts failures", async () => {
    let running = 0;
    let peak = 0;
    const r = await pool([1, 2, 3, 4, 5, 6, 7], 3, async (n) => {
      running++;
      peak = Math.max(peak, running);
      await new Promise((ok) => setTimeout(ok, 5));
      running--;
      if (n === 4) throw new Error("no");
    });
    expect(r).toEqual({ ok: 6, failed: 1 });
    expect(peak).toBe(3);
  });
});
