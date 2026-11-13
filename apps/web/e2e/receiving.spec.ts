import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// Fresh synthetic company with seeded vendors (V-ACC Accra Roots Exports …), catalog and prices, no stock.
const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/purchases_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

// A synthetic one-page "vendor invoice" image.
const invoice = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

test.describe.configure({ mode: "serial" });
let purchaseUrl = "";

async function signIn(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}

/** Select the first option whose text contains `text`. */
async function pick(select: Locator, text: string) {
  await expect(select.locator("option", { hasText: text }).first()).toBeAttached();
  const value = await select.locator("option", { hasText: text }).first().getAttribute("value");
  await select.selectOption(value ?? "");
}

// "Receiving" became "Purchases" (owner, 2026-11-12): one purchase, in stock the moment it is
// saved. There is no count and no approval step any more.
test("warehouse enters a purchase by hand: it is in stock at once, with no approval", async ({ page }) => {
  await signIn(page, fx.users.warehouse);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.goto("/purchases"); // by address: the sidebar has its own spec
  await expect(page.getByRole("heading", { name: "Orders", exact: true })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Purchases" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tab", { name: "Sales orders" })).toBeVisible(); // warehouse staff may read orders

  await page.getByRole("button", { name: "New" }).click();
  await expect(page.getByRole("menuitem")).toHaveText(["New purchase", "Quick receive"]); // no sales orders for the warehouse
  await page.getByRole("menuitem", { name: "New purchase" }).click();
  const form = page.getByRole("form", { name: "New purchase" });
  const d = new Date();
  const today = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  await expect(form.getByLabel("Invoice date")).toHaveValue(today);
  await pick(form.getByLabel("Supplier", { exact: true }), "V-ACC");
  await form.getByLabel("Invoice no.").fill("ARX-5521");
  // The keyboard path: product, Enter, quantity, Enter, cost, Enter → next line.
  await form.getByLabel("Product, line 1", { exact: true }).fill("CFZ");
  await form.getByLabel("Product, line 1", { exact: true }).press("Enter");
  await expect(form.getByLabel("Quantity, line 1", { exact: true })).toBeFocused();
  await form.getByLabel("Unit, line 1", { exact: true }).selectOption("BOX");
  await form.getByLabel("Quantity, line 1", { exact: true }).fill("2.5");
  await expect(form.getByRole("list", { name: "Lines to fix" })).toContainText("Line 1: Boxes and pieces are whole numbers.");
  await form.getByLabel("Quantity, line 1", { exact: true }).fill("25");
  await form.getByLabel("Quantity, line 1", { exact: true }).press("Enter");
  await expect(form.getByLabel("Cost per unit, line 1", { exact: true })).toBeFocused();
  await form.getByLabel("Cost per unit, line 1", { exact: true }).fill("18.50");
  await form.getByLabel("Cost per unit, line 1", { exact: true }).press("Enter");
  await expect(form.getByLabel("Product, line 2", { exact: true })).toBeFocused();
  // Cassava tracks expiry: the grid asks for its date (the column shows only when needed).
  await expect(form.getByRole("list", { name: "Lines to fix" })).toContainText("Line 1: This product needs its expiry date.");
  await form.getByLabel("Expiry date, line 1", { exact: true }).fill("2027-06-30");
  await form.getByLabel("Product, line 2", { exact: true }).fill("PLG");
  await form.getByLabel("Product, line 2", { exact: true }).press("Enter");
  await form.getByLabel("Unit, line 2", { exact: true }).selectOption("BOX");
  await form.getByLabel("Quantity, line 2", { exact: true }).fill("4"); // cost not known yet
  await expect(form.getByRole("status")).toContainText("2 lines · Invoice total USD 462.50 · 1 without a cost");
  await axe(page);
  await page.screenshot({ path: "screenshots/purchase-new-1440.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(form.getByLabel("Invoice no.")).toBeVisible();
  await axe(page);
  await page.screenshot({ path: "screenshots/purchase-new-390.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });

  await form.getByRole("button", { name: "Save & add to stock" }).click();
  await expect(page.getByRole("heading", { name: /^Purchase SHP-/ })).toBeVisible();
  purchaseUrl = page.url();
  await expect(page.getByRole("status").filter({ hasText: "Saved. Every line is in stock now." })).toBeVisible();
  await expect(page.getByText("In stock", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("line-CFZ")).toContainText("25 boxes");
  await expect(page.getByTestId("line-PLG")).toContainText("Cost to follow");
  await expect(page.getByRole("region", { name: "Invoice lines" })).toContainText("USD 462.50");
  await expect(page.getByTestId("stock-CFZ")).toContainText("ARE-"); // batch named after the supplier

  // The seller's invoice can be attached afterwards and is kept as received.
  const add = page.getByRole("group", { name: "Add a document" });
  await expect(add.getByRole("button", { name: "From Gmail" })).toHaveCount(0); // warehouse can't open Gmail files
  await add.getByLabel("Document type").selectOption("INVOICE");
  await add.getByLabel("Number on it").fill("ARX-5521");
  await page.getByTestId("doc-upload").setInputFiles({ name: "vendor-invoice.png", mimeType: "image/png", buffer: invoice });
  await expect(page.getByRole("status").filter({ hasText: "Supplier invoice added" })).toBeVisible();
  await expect(page.getByRole("img", { name: /^Original Supplier invoice/ })).toBeVisible();
  // A local purchase has no freight / duty section.
  await expect(page.getByText("Freight, duty and other costs")).toHaveCount(0);

  // Later: 2 boxes turn out crushed. A correction with a reason; the warehouse can't post it.
  await page.getByRole("button", { name: "Correct Cassava frozen" }).click();
  const dlg = page.getByRole("dialog", { name: "Correct Cassava frozen" });
  await dlg.getByLabel(/^Damaged/).check();
  await dlg.getByLabel("How many").fill("2");
  await dlg.getByRole("button", { name: "Send for approval" }).click();
  await expect(dlg.getByRole("alert")).toContainText("Give a reason");
  await dlg.getByLabel("Reason (required)").fill("Two cartons crushed in the cooler");
  await dlg.getByRole("button", { name: "Send for approval" }).click();
  await expect(page.getByRole("status").filter({ hasText: "A manager must approve it" })).toBeVisible();
  await expect(page.getByTestId("correction-DAMAGED")).toContainText("waiting for a manager's OK. Stock is unchanged.");
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);
  await axe(page);
  await page.screenshot({ path: "screenshots/purchase-detail-1440.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await axe(page);
  await page.screenshot({ path: "screenshots/purchase-detail-390.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });

  // The stock is there right away.
  await page.goto("/inventory");
  await page.getByRole("searchbox").first().fill("CFZ");
  await expect(page.getByRole("row").filter({ hasText: /CFZ-/ }).first()).toContainText("25");
});

test("manager approves the correction: damaged boxes are put aside, and old Receiving links still open", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

  // A bookmark from before the rename.
  await page.goto("/receiving");
  await expect(page).toHaveURL(/\/purchases$/);
  const row = page.getByRole("row").filter({ hasText: "ARX-5521" }).first();
  await expect(row).toContainText("1 correction to approve");
  await expect(row).toContainText("USD 462.50");
  await axe(page);
  await page.screenshot({ path: "screenshots/purchases-list-1440.png", fullPage: true });
  await page.goto(purchaseUrl.replace("/purchases/", "/receiving/"));
  await expect(page).toHaveURL(purchaseUrl);

  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Approved. Stock was corrected." })).toBeVisible();
  await expect(page.getByTestId("correction-DAMAGED")).toContainText("moved out of saleable stock");
  await expect(page.getByTestId("stock-CFZ")).toContainText("25 boxes");
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0); // once

  // Stock: 23 saleable boxes plus a separate damaged lot of 2.
  await page.goto("/inventory");
  await page.getByRole("searchbox").first().fill("CFZ");
  await expect(page.getByRole("row").filter({ hasText: /-DMG/ }).first()).toBeVisible();
});

test("suppliers list is managed by the owner", async ({ page }) => {
  await signIn(page, fx.users.warehouse);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.getByRole("link", { name: "Suppliers", exact: true }).click();
  await expect(page.getByRole("list", { name: "Suppliers" })).toContainText("V-ACC");
  await expect(page.getByRole("form", { name: "Add a supplier" })).toHaveCount(0);
  await axe(page);
  await page.screenshot({ path: "screenshots/suppliers-1440.png", fullPage: true });
});
