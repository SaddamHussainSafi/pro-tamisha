import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// The Create sales order screen (2026-11-11): the Create-invoice look on orders. Product
// thumbnails, 20 open rows plus "Add 10 more rows", Save draft / Confirm order at the top as well
// as in the sticky totals bar. Order rules are untouched: a new order is saved as a DRAFT, and
// confirming fixes today's prices. Fresh synthetic company (C-1001, CFZ BOX 29.00).
type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };
const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);
const SHOTS = process.env.ORDER_UI_SHOTS ?? "screenshots/orders";
mkdirSync(SHOTS, { recursive: true });

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.users.sales);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}
async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}
const cell = (page: Page, name: string) => page.getByLabel(name, { exact: true });
async function chooseCustomer(page: Page) {
  const opt = page.locator("#so-cust option", { hasText: "Island Grocers" }).first();
  await opt.waitFor({ state: "attached" });
  await page.locator("#so-cust").selectOption((await opt.getAttribute("value")) ?? "");
}

test("keyboard entry, thumbnails, more rows, and only filled rows are saved", async ({ page }) => {
  await signIn(page);
  await page.goto("/orders/new");
  await expect(page.getByRole("heading", { name: "Create sales order", level: 1 })).toBeVisible();
  const form = page.getByRole("form", { name: "New sales order" });
  await chooseCustomer(page);

  // 20 rows are open from the start; nothing has to be added before typing
  await expect(cell(page, "Item, line 20")).toBeVisible();
  await expect(cell(page, "Item, line 21")).toHaveCount(0);
  await expect(form.locator("tbody img")).toHaveCount(0); // empty rows carry no picture

  // Enter: Item -> Qty -> next row's Item; arrows move between rows
  await cell(page, "Item, line 1").click();
  await page.keyboard.type("CFZ");
  await page.keyboard.press("Enter");
  await expect(cell(page, "Item, line 1")).toHaveValue("Cassava frozen");
  await expect(cell(page, "Quantity, line 1")).toBeFocused();
  await page.keyboard.type("15");
  await page.keyboard.press("Enter");
  await expect(cell(page, "Item, line 2")).toBeFocused();
  await page.keyboard.type("PLG");
  await page.keyboard.press("Enter");
  await page.keyboard.type("40");
  await page.keyboard.press("ArrowUp");
  await expect(cell(page, "Quantity, line 1")).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Tab");
  await expect(cell(page, "Unit, line 2")).toBeFocused();
  await cell(page, "Unit, line 1").selectOption("BOX");

  // a small square picture per chosen product (36-44 px), shown whole, or the neutral placeholder
  const thumb = page.getByTestId("grid-row-1").locator("td").nth(1).locator("span[aria-hidden]").first();
  await expect(thumb).toBeVisible();
  const box = await thumb.boundingBox();
  for (const side of [box?.width ?? 0, box?.height ?? 0]) {
    expect(side).toBeGreaterThanOrEqual(36);
    expect(side).toBeLessThanOrEqual(44);
  }
  expect(box?.width).toBe(box?.height);
  const img = thumb.locator("img");
  if (await img.count()) {
    expect(await img.getAttribute("src")).toBe("/products/CFZ.webp");
    expect(await img.evaluate((el) => getComputedStyle(el).objectFit)).toBe("contain");
  } else await expect(thumb.locator("svg")).toBeVisible();

  // totals come from the server's prices: 15 boxes x 29.00
  await expect(page.getByTestId("grid-row-1")).toContainText("29.00");
  await expect(page.getByTestId("grid-row-1")).toContainText("435.00");
  // the totals bar stays at the bottom of the window
  const bar = page.getByTestId("net-total").locator("xpath=ancestor::div[contains(@class,'sticky')]").first();
  expect(await bar.evaluate((el) => getComputedStyle(el).position)).toBe("sticky");

  await page.getByRole("button", { name: "Add 10 more rows" }).click();
  await expect(cell(page, "Item, line 30")).toBeVisible();
  await page.getByRole("button", { name: "Add 10 more rows" }).click();
  await expect(cell(page, "Item, line 40")).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: `${SHOTS}/create-sales-order-1440.png`, animations: "disabled" });
  await axe(page);

  // a line without a quantity is refused before anything is saved
  await cell(page, "Item, line 3").fill("RJS");
  await cell(page, "Item, line 3").press("Tab");
  await page.getByRole("button", { name: "Save draft" }).first().click(); // the header's button
  await expect(page.getByRole("alert")).toContainText("Line 3");
  await expect(page).toHaveURL(/\/orders\/new$/);
  await page.getByTestId("grid-row-3").locator("td").first().hover(); // the row's clear button shows on hover
  await page.getByRole("button", { name: "Clear line 3" }).click();
  await expect(cell(page, "Item, line 3")).toHaveValue("");

  // Save draft at the top: a DRAFT with exactly the two filled lines (38 empty rows are not lines)
  await page.getByRole("button", { name: "Save draft" }).first().click();
  const draft = page.getByRole("form", { name: /^Order SO-/ });
  await expect(draft).toBeVisible();
  await expect(page.getByText("Draft", { exact: true }).first()).toBeVisible();
  await expect(cell(page, "Item, line 1")).toHaveValue("Cassava frozen");
  await expect(cell(page, "Item, line 2")).toHaveValue("Plantain green");
  await expect(cell(page, "Item, line 3")).toHaveValue("");
  const id = page.url().split("/").pop();
  const saved = await (await page.request.get(`/api/v1/sales-orders/${id}`)).json();
  expect(saved.status).toBe("DRAFT");
  expect(saved.lines.map((l: { sku: string; ordered_qty: string }) => [l.sku, l.ordered_qty])).toEqual([
    ["CFZ", "15.0000"],
    ["PLG", "40.0000"],
  ]);
  expect(saved.lines.every((l: { rate: string | null }) => l.rate === null)).toBe(true); // no price is fixed on a draft

  // on the draft, both actions are also at the top; Confirm order fixes today's prices
  await expect(page.getByRole("button", { name: "Save draft" })).toHaveCount(2); // header + totals bar
  await expect(page.getByRole("button", { name: "Confirm order" })).toHaveCount(2);
  await expect(draft.getByRole("button", { name: "Confirm order" })).toHaveCount(1);
  await page.getByRole("button", { name: "Confirm order" }).first().click(); // the header's
  await expect(page.getByText("Confirmed", { exact: true }).first()).toBeVisible();
  const done = await (await page.request.get(`/api/v1/sales-orders/${id}`)).json();
  expect(done.status).toBe("CONFIRMED");
  expect(done.lines[0].rate).toBe("29.0000");
});

test("at 390 px the order form fits and the product stays readable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);
  await page.goto("/orders/new");
  await chooseCustomer(page);
  await cell(page, "Item, line 1").fill("CFZ");
  await cell(page, "Item, line 1").press("Enter");
  await expect(cell(page, "Item, line 1")).toHaveValue("Cassava frozen");
  const extra = await page.evaluate(() => {
    const main = document.getElementById("main");
    const doc = document.scrollingElement as HTMLElement;
    return Math.max(doc.scrollWidth - doc.clientWidth, main ? main.scrollWidth - main.clientWidth : 0);
  });
  expect(extra, "the page scrolls sideways").toBeLessThanOrEqual(1);
  const item = await cell(page, "Item, line 1").boundingBox();
  expect(item?.width ?? 0).toBeGreaterThan(180); // the grid scrolls inside its own frame instead
  await page.screenshot({ path: `${SHOTS}/create-sales-order-390.png`, animations: "disabled" });
});
