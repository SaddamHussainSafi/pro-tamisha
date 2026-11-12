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
