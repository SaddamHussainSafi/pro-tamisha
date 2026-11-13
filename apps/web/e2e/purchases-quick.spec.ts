import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";

type Sheet = { id: string; code: string };
type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string }; match: string; packing: Sheet; verified: Sheet };

// Fresh synthetic company with NOTHING in stock: an open packing sheet for 5 boxes of cassava,
// a verified sheet for 7 boxes waiting for its invoice, and a supplier invoice (Kingston
// Produce: 5 boxes cassava, 4 boxes rice) already scanned and read by the in-process test reader.
const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/purchases_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

test.describe.configure({ mode: "serial" });

async function signIn(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}

async function pick(select: Locator, text: string) {
  await expect(select.locator("option", { hasText: text }).first()).toBeAttached();
  await select.selectOption((await select.locator("option", { hasText: text }).first().getAttribute("value")) ?? "");
}

test("a short packing line: the goods are on the shelf, so the warehouse adds them on the spot", async ({ page }) => {
  await signIn(page, fx.users.warehouse);
  await page.goto(`/packing/${fx.packing.id}`);
  const row = page.getByTestId("pick-CFZ");
  // The system has no stock for this line; invoicing it would be refused.
  await expect(row).toContainText("The system shows only 0 BOX in stock.");
  await axe(page);
  await page.screenshot({ path: "screenshots/packing-short-line-1440.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(row.getByRole("button", { name: "Add 5 BOX found in the warehouse" })).toBeVisible();
  await page.screenshot({ path: "screenshots/packing-short-line-390.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });

  await row.getByRole("button", { name: "Add 5 BOX found in the warehouse" }).click();
  const dlg = page.getByRole("dialog", { name: "Quick receive" });
  await expect(dlg.getByLabel("Quantity found")).toHaveValue("5");
  await expect(dlg.getByLabel("Supplier, if you know it")).toHaveValue(""); // "Supplier invoice to follow"
  await dlg.getByLabel("Expiry date").fill("2027-06-30"); // cassava tracks expiry
  await dlg.getByRole("button", { name: "Add to stock" }).click();
  await expect(dlg.getByRole("alert")).toContainText("Say why you are adding it");
  await dlg.getByLabel(/Why are you adding it/).fill("Delivered yesterday, the purchase was never entered");
  await axe(page);
  await page.screenshot({ path: "screenshots/quick-receive-dialog-1440.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "screenshots/quick-receive-dialog-390.png" });
  await page.setViewportSize({ width: 1440, height: 900 });
  await dlg.getByRole("button", { name: "Add to stock" }).click();

  await expect(page.getByRole("status").filter({ hasText: "Added to stock: 5 boxes Cassava frozen." })).toBeVisible();
  await expect(row).not.toContainText("The system shows only"); // no longer short
  // Packing carries on.
  await row.getByLabel("Packed CFZ").fill("5");
  await page.getByRole("button", { name: "Save quantities" }).click();
  await expect(page.getByText("Quantities saved.")).toBeVisible();
});

test("the invoice screen offers the same when a line is short", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.goto(`/packing/${fx.verified.id}/invoice`);
  // 7 boxes were packed for this sheet; the system now shows the 5 that were just added.
  const line = page.getByTestId("stock-CFZ");
  await expect(line).toContainText("Short");
  await expect(line).toContainText("deduct 7 BOX, 5 available");
  await expect(page.getByRole("button", { name: "Issue invoice" })).toBeDisabled();
  await axe(page);
  await page.screenshot({ path: "screenshots/invoice-short-line-1440.png", fullPage: true });
  await line.getByRole("button", { name: "Add 2 BOX found in the warehouse" }).click();
  const dlg = page.getByRole("dialog", { name: "Quick receive" });
  await pick(dlg.getByLabel("Supplier, if you know it"), "Kingston Produce"); // the supplier is known this time
  await dlg.getByLabel("Expiry date").fill("2027-06-30");
  await dlg.getByLabel(/Why are you adding it/).fill("Two more boxes were behind the pallet");
  await dlg.getByRole("button", { name: "Add to stock" }).click();
  await expect(line).toContainText("In stock");
  await expect(line).toContainText("deduct 7 BOX, 7 available");
  // Negative stock is still refused: the invoice can only go out because the stock is there now.
  await page.getByLabel("The goods on this sheet have been dispatched to the customer").check();
  await expect(page.getByRole("button", { name: "Issue invoice" })).toBeEnabled();

  // Both finds wait on the "To complete" list until their supplier invoice is entered.
  await page.goto("/purchases?view=complete");
  await expect(page.getByRole("note")).toContainText("their supplier invoice is still missing");
  const rows = page.getByRole("row").filter({ hasText: "Details to complete" });
  await expect(rows).toHaveCount(2);
  await expect(rows.filter({ hasText: "Supplier invoice to follow" })).toContainText("5 boxes Cassava frozen");
  await expect(rows.filter({ hasText: "Kingston Produce" })).toContainText("Cost to follow");
  await axe(page);
  await page.screenshot({ path: "screenshots/purchases-to-complete-1440.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await axe(page);
  await page.screenshot({ path: "screenshots/purchases-to-complete-390.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });
});

test("the supplier's invoice is scanned later: the quick receive is matched, its stock is not added again", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.goto(`/scan/review/${fx.match}`);
  await page.getByRole("checkbox", { name: "Select all clear lines" }).check();
  await page.getByRole("button", { name: "Confirm selected (2)" }).click();
  await expect(page.getByText("2 lines confirmed.")).toBeVisible();

  const save = page.getByRole("region", { name: "Save & add to stock" });
  // Both quick receives are cassava: the reviewer says which one this invoice covers.
  const five = save.getByRole("group", { name: /Quick receive SHP-/ }).filter({ hasText: "5 boxes Cassava frozen" });
  const two = save.getByRole("group", { name: /Quick receive SHP-/ }).filter({ hasText: "2 boxes Cassava frozen" });
  await save.getByLabel("Cassava frozen").fill("2027-06-30");
  await save.getByLabel(/I checked every line/).check();
  await expect(save.getByRole("button", { name: "Save & add to stock" })).toBeDisabled(); // not answered yet
  await five.getByLabel("Same goods, on this invoice").check();
  await two.getByLabel("Different goods").check();
  await axe(page);
  await page.screenshot({ path: "screenshots/purchase-scan-match-quick-1440.png", fullPage: true });
  await save.getByRole("button", { name: "Save & add to stock" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Saved as a purchase." })).toBeVisible();
  await page.getByRole("link", { name: "Open the purchase" }).click();

  await expect(page.getByRole("heading", { name: /^Purchase SHP-/ })).toBeVisible();
  await expect(page.getByTestId("line-CFZ")).toContainText("5 boxes");
  await expect(page.getByRole("region", { name: "Invoice lines" })).toContainText("USD 140.00");
  // Only the rice is new stock; the cassava was already on the shelf.
  await expect(page.getByTestId("stock-RJS")).toContainText("4 boxes");
  await expect(page.getByTestId("stock-CFZ")).toHaveCount(0);
  const shelf = page.getByRole("region", { name: "Already on the shelf" });
  await expect(shelf).toContainText("5 boxes Cassava frozen");
  await expect(shelf).toContainText("Delivered yesterday, the purchase was never entered");
  await axe(page);
  await page.screenshot({ path: "screenshots/purchase-matched-1440.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await axe(page);
  await page.screenshot({ path: "screenshots/purchase-matched-390.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });

  // The quick receive itself now points at the invoice and has left the "To complete" list.
  await shelf.getByRole("link", { name: /^SHP-/ }).click();
  await expect(page.getByText(/Matched to the supplier's invoice/)).toBeVisible();
  await page.goto("/purchases?view=complete");
  await expect(page.getByRole("row").filter({ hasText: "Details to complete" })).toHaveCount(1);

  // The other one is completed by hand: supplier, invoice number and cost. No stock is added.
  await page.getByRole("row").filter({ hasText: "Details to complete" }).getByRole("link").first().click();
  await page.getByRole("button", { name: "Complete details" }).click();
  const form = page.getByRole("form", { name: "Complete purchase details" });
  await expect(form.getByLabel("Product, line 1", { exact: true })).toHaveValue("Cassava frozen");
  await expect(form.getByLabel("Quantity, line 1", { exact: true })).toHaveValue("2");
  await form.getByLabel("Invoice no.").fill("KPC-881");
  await form.getByLabel("Cost per unit, line 1", { exact: true }).fill("12.00");
  await axe(page);
  await page.screenshot({ path: "screenshots/purchase-complete-details-1440.png", fullPage: true });
  await form.getByRole("button", { name: "Save details" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Details saved. The stock was not added again." })).toBeVisible();
  await expect(page.getByRole("region", { name: "Invoice lines" })).toContainText("USD 24.00");
  await expect(page.getByText("Details to complete")).toHaveCount(0);

  // 5 + 2 boxes of cassava in all: nothing was posted twice.
  await page.goto(`/packing/${fx.verified.id}/invoice`);
  await expect(page.getByTestId("stock-CFZ")).toContainText("deduct 7 BOX, 7 available");
});
