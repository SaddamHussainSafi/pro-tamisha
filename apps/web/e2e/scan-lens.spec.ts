import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Scan review magnifier (2026-11-11, owner's request): hovering a recognized line shows that
// line's area of the original scan, zoomed (Auto, 2x, 4x, 6x), in a small window beside the
// row, so nobody has to scroll to the scan. Synthetic one-page sheet read by the built-in fake
// reader (scripts/lens_fixture.py); no outside OCR service is called.
const fx = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/lens_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
) as { user: string; password: string; reading: string; status: string };
const SHOTS = process.env.LENS_SHOTS ?? "screenshots/scan-lens";
mkdirSync(SHOTS, { recursive: true });

async function open(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.user);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.goto(`/scan/review/${fx.reading}`);
  await expect(page.getByTestId("ocr-line-2")).toBeVisible();
}
const lens = (page: Page) => page.getByTestId("scan-lens");

test("hovering a line shows its part of the original, zoomed, and it follows the mouse", async ({ page }) => {
  expect(fx.status).toBe("NEEDS_REVIEW");
  await open(page);
  await expect(lens(page)).toHaveCount(0);

  // the full-page scan is no longer beside the lines
  await expect(page.getByRole("img", { name: /^Original scan .*page 1$/ })).toHaveCount(0);
  await page.getByTestId("ocr-line-2").hover();
  await expect(lens(page)).toBeVisible();
  await expect(lens(page)).toHaveAccessibleName(/line 2, PLG Plantain green/);
  // spotlight: the rest of the page is dimmed around the line
  await expect(page.getByTestId("scan-lens-spot")).toHaveCSS("box-shadow", /rgba\(24, 24, 27, 0\.55\)/);
  // Auto: the line's box (70% of the page wide) is framed whole -> 2x, the lowest zoom
  await expect(lens(page)).toHaveAttribute("data-zoom", "2");
  const img = lens(page).getByRole("img");
  await expect(img).toHaveAttribute("src", /\/pages\/1\?dpi=220$/);
  await expect.poll(() => img.evaluate((el: HTMLImageElement) => el.complete && el.naturalWidth > 0)).toBe(true);
  // the page is drawn so the line's centre is the window's centre
  // the item name sits at the window's left, the line in the middle
  const centred = await lens(page).evaluate((el) => {
    const frame = (el.firstElementChild as HTMLElement).getBoundingClientRect();
    const outline = (el.querySelector("[data-testid=scan-lens-spot]") as HTMLElement).getBoundingClientRect();
    return Math.abs(outline.top + outline.height / 2 - (frame.top + frame.height / 2)) < 2;
  });
  expect(centred).toBe(true);
  // it sits beside the row, inside the window
  const row = (await page.getByTestId("ocr-line-2").boundingBox()) ?? { x: 0, y: 0, width: 0, height: 0 };
  const box = (await lens(page).boundingBox()) ?? { x: -1, y: 0, width: 0, height: 0 };
  expect(box.y >= row.y + row.height - 1 || box.y + box.height <= row.y + 1).toBe(true);
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize()?.width ?? 0);
  await page.screenshot({ path: `${SHOTS}/lens-auto-1440.png`, animations: "disabled" });
  await new AxeBuilder({ page }).include("[data-testid=scan-lens]").analyze().then((r) => expect(r.violations.map((v) => v.id)).toEqual([]));

  // another line: the window moves to it
  await page.getByTestId("ocr-line-1").hover();
  await expect(lens(page)).toHaveAccessibleName(/line 1, CFZ Cassava frozen/);
});

test("the zoom can be chosen, is kept while the pointer is on the window, and is remembered", async ({ page }) => {
  await open(page);
  await page.getByTestId("ocr-line-2").hover();
  await expect(lens(page)).toBeVisible();
  // move onto the window: it stays open
  await lens(page).hover();
  await page.waitForTimeout(400);
  await expect(lens(page)).toBeVisible();
  await lens(page).getByRole("button", { name: "3×" }).click();
  await expect(lens(page)).toHaveAttribute("data-zoom", "3");
  await expect(lens(page).getByRole("img")).toHaveAttribute("src", /dpi=300$/);
  await lens(page).getByRole("button", { name: "4×" }).click();
  await expect(lens(page)).toHaveAttribute("data-zoom", "4");
  await expect(lens(page).getByRole("button", { name: "4×" })).toHaveAttribute("aria-pressed", "true");
  await expect(lens(page).getByRole("button", { name: "6×" })).toHaveCount(0); // never so close that columns are lost
  // the wheel zooms out (and does not scroll the page)
  const before = await page.evaluate(() => (document.getElementById("main") as HTMLElement).scrollTop);
  await page.mouse.wheel(0, 120);
  await expect(lens(page)).toHaveAttribute("data-zoom", "3");
  expect(await page.evaluate(() => (document.getElementById("main") as HTMLElement).scrollTop)).toBe(before);
  await page.screenshot({ path: `${SHOTS}/lens-3x-1440.png`, animations: "disabled" });

  // leaving closes it; the next hover opens it at the chosen zoom
  await page.mouse.move(5, 5);
  await expect(lens(page)).toHaveCount(0);
  await page.getByTestId("ocr-line-1").hover();
  await expect(lens(page)).toHaveAttribute("data-zoom", "3");
  await page.reload();
  await page.getByTestId("ocr-line-1").hover();
  await expect(lens(page)).toHaveAttribute("data-zoom", "3");
  await lens(page).hover();
  await lens(page).getByRole("button", { name: "Auto" }).click();
});

test("keyboard: tabbing into a line shows it, Escape closes it", async ({ page }) => {
  await open(page);
  await page.getByTestId("ocr-line-2").getByRole("button").first().focus();
  await expect(lens(page)).toBeVisible();
  await expect(lens(page)).toHaveAccessibleName(/line 2/);
  await page.keyboard.press("Escape");
  await expect(lens(page)).toHaveCount(0);
});

test("a sharper page is served only at the allowed resolutions", async ({ page }) => {
  await open(page);
  const reading = await (await page.request.get(`/api/v1/ocr/extractions/${fx.reading}`)).json();
  const base = `/api/v1/scans/documents/${reading.document_id}/pages/1`;
  for (const dpi of [110, 220, 300]) expect((await page.request.get(`${base}?dpi=${dpi}`)).status()).toBe(200);
  expect((await page.request.get(`${base}?dpi=1200`)).status()).toBe(422);
});
