import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Universal search (⌘K / Ctrl K palette and /search). Synthetic company from e2e_fixture.py:
// customer C-1001 "Island Grocers (synthetic)", lots CAS-FZ-SEED-1 / PLN-GR-SEED-1.
type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

const api = { cwd: resolve(import.meta.dirname, "../../api"), encoding: "utf8" as const, env: { ...process.env, PYTHONPATH: "src" } };
const fx: Fixture = JSON.parse(execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], api));
// Index the new company now instead of waiting for the worker's next run (exact numbers
// work without this; words and names need the search table).
execFileSync("uv", ["run", "python", "-m", "scripts.search_reindex", "drain"], api);

async function signIn(page: Page, who: keyof Fixture["users"] = "owner") {
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.users[who]);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

const box = (page: Page) => page.getByRole("dialog", { name: "Search" }).getByRole("combobox");
const options = (page: Page) => page.getByRole("dialog", { name: "Search" }).getByRole("option");

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).include('[role="dialog"]').withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}

test("Ctrl/⌘ K opens search; an exact customer code opens the customer", async ({ page }) => {
  await signIn(page);
  await page.keyboard.press("ControlOrMeta+k");
  await expect(box(page)).toBeFocused();
  // Empty box: quick actions the owner may use
  await expect(options(page).filter({ hasText: "New invoice" })).toBeVisible();
  await box(page).fill("C-1001");
  await expect(page.locator('[role="option"][data-type="customer"]').first()).toBeVisible();
  const first = options(page).first();
  await expect(first).toContainText("Island Grocers");
  await expect(first).toHaveAttribute("aria-selected", "true");
  await axe(page);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/customers\/[0-9a-f-]{36}$/);
  await expect(page.getByRole("dialog", { name: "Search" })).toHaveCount(0);

  // It is now a recent record, private to this user and clearable.
  await page.keyboard.press("ControlOrMeta+k");
  const recent = page.getByRole("group", { name: "Recent" });
  await expect(recent.getByRole("option").first()).toContainText("Island Grocers");
  await page.getByRole("button", { name: "Clear" }).click();
  await expect(page.getByRole("group", { name: "Recent" })).toHaveCount(0);
});

test("keyboard: arrows move, Enter opens the right screen, Esc clears then closes", async ({ page }) => {
  await signIn(page);
  await page.keyboard.press("ControlOrMeta+k");
  await box(page).fill("CAS-FZ-SEED-1");
  await expect(page.locator('[role="option"][data-type="lot"]')).toHaveCount(1);
  await expect(options(page).first()).toContainText("Cassava");
  await expect(options(page).first()).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowDown");
  await expect(options(page).nth(1)).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowUp");
  await expect(options(page).first()).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/inventory\/lots\/[0-9a-f-]{36}$/);

  await page.keyboard.press("ControlOrMeta+k");
  await box(page).fill("island");
  await expect(options(page).first()).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(box(page)).toHaveValue("");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Search" })).toHaveCount(0);

  // Tab stays inside the dialog (focus trap)
  await page.keyboard.press("ControlOrMeta+k");
  for (let i = 0; i < 25; i++) await page.keyboard.press("Tab");
  expect(await page.evaluate(() => !!document.activeElement?.closest('[role="dialog"]'))).toBe(true);
});

test("screens are still one jump away; See all opens /search", async ({ page }) => {
  await signIn(page);
  await page.keyboard.press("ControlOrMeta+k");
  await box(page).fill("pack");
  await expect(page.getByRole("group", { name: "Screens" }).getByRole("option", { name: /Packing/ })).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/packing$/);

  await page.keyboard.press("ControlOrMeta+k");
  await box(page).fill("island");
  await expect(page.locator('[role="option"][data-type="customer"]').first()).toBeVisible();
  await page.keyboard.press("ControlOrMeta+Enter");
  await expect(page).toHaveURL(/\/search\?q=island/);
  await expect(page.getByRole("heading", { name: "Search" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Results" }).getByRole("link").first()).toBeVisible();
  await page.getByRole("button", { name: "Customers", exact: true }).click();
  await expect(page).toHaveURL(/type=customer/);
  await expect(page.getByRole("region", { name: "Results" }).getByRole("link").first()).toContainText("Island Grocers");
});

test("warehouse staff only get the categories they may open", async ({ page }) => {
  await signIn(page, "warehouse");
  await page.keyboard.press("ControlOrMeta+k");
  const chips = page.getByRole("group", { name: "Only search in" });
  await expect(chips.getByRole("button", { name: "Batches" })).toBeVisible();
  await expect(chips.getByRole("button", { name: "Invoices" })).toHaveCount(0);
  await expect(chips.getByRole("button", { name: "Payments" })).toHaveCount(0);
  await expect(options(page).filter({ hasText: "New invoice" })).toHaveCount(0);
});

test("at 390 px the header button opens a full-screen search", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);
  await page.getByRole("button", { name: "Search", exact: true }).click();
  const dlg = page.getByRole("dialog", { name: "Search" });
  await expect(box(page)).toBeFocused();
  const r = await dlg.boundingBox();
  expect(r?.width).toBeGreaterThanOrEqual(389);
  await box(page).fill("C-1001");
  await expect(page.locator('[role="option"][data-type="customer"]').first()).toBeVisible();
  await expect(options(page).first()).toContainText("Island Grocers");
  const extra = await page.evaluate(() => (document.scrollingElement as HTMLElement).scrollWidth - (document.scrollingElement as HTMLElement).clientWidth);
  expect(extra).toBeLessThanOrEqual(1);
  await axe(page);
  await options(page).first().click();
  await expect(page).toHaveURL(/\/customers\//);
});
