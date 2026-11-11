import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Owner, 2026-11-11: "there is no box price, all products go as per pound", and the draft order
// should look like Create invoice. Fresh synthetic company: PLG (plantain) has a per-lb price
// (0.95) and a 50 lb case, no box price for Island Grocers.
const fx = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
) as { password: string; users: { sales: string } };

test.describe.configure({ mode: "serial" });

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.users.sales);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

async function newDraft(page: Page, sku: string, qty: string) {
  await page.goto("/orders/new");
  const form = page.getByRole("form", { name: "New sales order" });
  const opt = page.locator("#so-cust option", { hasText: "Island Grocers" }).first();
  await opt.waitFor({ state: "attached" });
  await form.getByLabel("Customer", { exact: true }).selectOption((await opt.getAttribute("value")) ?? "");
  await form.getByLabel("Item, line 1", { exact: true }).fill(sku);
  await form.getByLabel("Item, line 1", { exact: true }).press("Enter");
  await form.getByLabel("Unit, line 1", { exact: true }).selectOption("BOX");
  await form.getByLabel("Quantity, line 1", { exact: true }).fill(qty);
  return form;
}

test("boxes are priced per lb by their weight; the draft is checked and confirmed on one form", async ({ page }) => {
  await signIn(page);
  const form = await newDraft(page, "PLG", "2");
  const lb = form.getByLabel("Weight in pounds, line 1", { exact: true });
  await expect(lb).toHaveAttribute("placeholder", "100.00"); // 2 × the 50 lb case, until weighed
  await expect(form.getByTestId("grid-row-1")).toContainText("0.95/lb");
  await expect(page.getByTestId("net-total")).toHaveText("CAD 95.00");
  await form.getByRole("button", { name: "Save draft" }).click();

  // The draft: same fields and grid as Create invoice, no picking/backorder columns
  const draft = page.getByRole("form", { name: /^Order SO-/ });
  await expect(draft).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Pending" })).toHaveCount(0);
  await draft.getByLabel("Weight in pounds, line 1", { exact: true }).fill("90"); // weighed: 90 lb
  await expect(page.getByTestId("net-total")).toHaveText("CAD 85.50");
  await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze().then((r) => expect(r.violations.map((v) => v.id)).toEqual([]));
  await page.screenshot({ path: "screenshots/order-draft-1440.png", fullPage: true });
  await draft.getByRole("button", { name: "Confirm order" }).click();

  await expect(page.getByText("Confirmed", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("table", { name: "Order lines" })).toContainText("42.75"); // 0.95 × 45 lb a box
});

test("a draft is cancelled without asking for a reason", async ({ page }) => {
  await signIn(page);
  const form = await newDraft(page, "CFZ", "1");
  await form.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByRole("form", { name: /^Order SO-/ })).toBeVisible();
  await page.getByRole("button", { name: "Cancel draft" }).click();
  const d = page.getByRole("dialog");
  await expect(d.getByLabel(/Reason/)).toHaveCount(0);
  await d.getByRole("button", { name: "Cancel draft" }).click();
  await expect(page.getByText("Cancelled", { exact: true }).first()).toBeVisible();
});
