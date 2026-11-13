import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

// Keyboard and small-screen navigation from the UX pass (2026-11-09).
type Fixture = { password: string; users: { warehouse: string } };

const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.users.warehouse);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

test("Ctrl K opens search (screens included), and / jumps to the screen's own search", async ({ page }) => {
  await signIn(page);
  await page.keyboard.press("ControlOrMeta+k");
  const go = page.getByRole("dialog", { name: "Search" }).getByRole("combobox");
  await expect(go).toBeFocused();
  await go.fill("pack");
  await expect(page.getByRole("group", { name: "Screens" }).getByRole("option", { name: /Packing/ })).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/packing$/);
  await expect(page.getByRole("heading", { name: "Packing" })).toBeVisible();

  await page.locator("body").click();
  await page.keyboard.press("/");
  await expect(page.getByPlaceholder("Sheet code (scan QR)")).toBeFocused();
});

test("at 390 px nothing scrolls sideways and the menu takes focus", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);
  for (const path of ["/", "/orders", "/packing", "/inventory", "/receiving", "/scan", "/products", "/customers", "/suppliers", "/settings"]) {
    await page.goto(path);
    await expect(page.getByRole("status", { name: "Loading" })).toHaveCount(0);
    const extra = await page.evaluate(() => {
      const main = document.getElementById("main");
      const doc = document.scrollingElement as HTMLElement;
      return Math.max(doc.scrollWidth - doc.clientWidth, main ? main.scrollWidth - main.clientWidth : 0);
    });
    expect(extra, `${path} scrolls sideways`).toBeLessThanOrEqual(1);
  }

  const open = page.getByRole("button", { name: "Open navigation" });
  await open.click();
  await expect(page.getByRole("button", { name: "Close navigation" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(open).toBeFocused();
});

test("signing in returns to the page that was opened", async ({ page }) => {
  await page.goto("/inventory?tab=movements");
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("Email").fill(fx.users.warehouse);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/inventory\?tab=movements$/);
  await expect(page.getByRole("heading", { name: "Inventory" })).toBeVisible();
});

test("sidebar auto-hides to an icon rail and slides open, pushing the page, when the pointer is on it", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page);
  const nav = page.getByRole("complementary", { name: "Sections" });
  // the sidebar's own width: 88 px rail, 276 px open; it pushes the page (never covers it)
  const width = async () => Math.round((await nav.boundingBox())?.width ?? 0);
  const pageLeft = async () => Math.round((await page.locator("#main").boundingBox())?.x ?? 0);
  await expect(nav.getByText("Packing", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Auto-hide sidebar" }).click();
  await page.mouse.move(800, 400);
  // not fully hidden: the icons stay
  await expect.poll(width).toBe(88);
  await expect.poll(pageLeft).toBe(88);
  await expect(nav.getByRole("link", { name: /Packing/ })).toBeVisible();
  await page.screenshot({ path: "screenshots/sidebar-rail-1440.png" });
  await page.mouse.move(40, 450);
  await expect.poll(width).toBe(276);
  await expect.poll(pageLeft).toBe(276); // pushed, not overlaid
  await expect(nav.getByText("Packing", { exact: true })).toBeVisible();
  await page.screenshot({ path: "screenshots/sidebar-peek-1440.png" });
  await nav.getByRole("link", { name: /Packing/ }).click();
  await expect(page).toHaveURL(/\/packing$/);
  await page.mouse.move(900, 400);
  await expect.poll(width).toBe(88);
  // remembered after a reload, and can be pinned open again
  await page.reload();
  await expect.poll(width).toBe(88);
  await page.mouse.move(40, 300);
  await nav.getByRole("button", { name: "Keep sidebar open" }).click();
  await page.mouse.move(900, 400);
  await expect(page.getByRole("button", { name: "Auto-hide sidebar" })).toBeVisible();
});
