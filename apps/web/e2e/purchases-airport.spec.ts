import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// Fresh synthetic company with catalog, prices and suppliers, and nothing in stock.
const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/purchases_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

test.describe.configure({ mode: "serial" });
let purchaseUrl = "";
let invoiceNo = "";

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

test("an airport shipment is entered before it arrives and is in stock at once", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.goto("/orders"); // by address: the sidebar has its own spec
  await expect(page.getByRole("tab", { name: "Sales orders" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("button", { name: "New" }).click();
  await expect(page.getByRole("menuitem")).toHaveText(["New sales order", "New purchase", "Quick receive"]);
  await page.getByRole("menuitem", { name: "New purchase" }).click();

  const form = page.getByRole("form", { name: "New purchase" });
  await pick(form.getByLabel("Supplier", { exact: true }), "V-KIN");
  await form.getByLabel("Invoice no.").fill("KPC-1207");
  await expect(form.getByLabel("Airway bill no.")).toHaveCount(0); // only for airport shipments
  await form.getByLabel("Airport shipment, verify pieces on arrival").check();
  await form.getByLabel("Airway bill no.").fill("176-5551 2345");
  await form.getByLabel("Pieces on the airway bill").fill("10");
  await form.getByLabel("Product, line 1", { exact: true }).fill("Cassava frozen");
  await form.getByLabel("Product, line 1", { exact: true }).press("Enter");
  await form.getByLabel("Quantity, line 1", { exact: true }).fill("10");
  await form.getByLabel("Cost per unit, line 1", { exact: true }).fill("12.50");
  await form.getByLabel("Expiry date, line 1", { exact: true }).fill("2027-06-30");
  await axe(page);
  await page.screenshot({ path: "screenshots/purchase-airport-new-1440.png", fullPage: true });
  await form.getByRole("button", { name: "Save & add to stock" }).click();

  await expect(page.getByRole("heading", { name: /^Purchase SHP-/ })).toBeVisible();
  purchaseUrl = page.url();
  await expect(page.getByText("Airport · to verify").first()).toBeVisible();
  await expect(page.getByRole("note").filter({ hasText: "Airport shipment · to verify." })).toContainText("it can be invoiced as soon as it arrives");
  // CFIA papers and freight / duty belong to an airport shipment.
  await expect(page.getByRole("group", { name: "Add a document" }).getByLabel("Document type").locator("option", { hasText: "CFIA document" })).toBeAttached();
  await page.getByText("Freight, duty and other costs").click();
  const cost = page.getByRole("form", { name: "Add a cost" });
  await cost.getByLabel("Amount").fill("180.00");
  await cost.getByRole("button", { name: "Add cost" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Freight recorded." })).toBeVisible();
  await axe(page);
  await page.screenshot({ path: "screenshots/purchase-airport-to-verify-1440.png", fullPage: true });

  // The goods have not been fetched yet, but the invoice can already be issued.
  await page.goto("/invoices/new?from=direct");
  await page.getByLabel("Customer", { exact: true }).selectOption({ label: "Island Grocers (synthetic) (C-1001)" });
  const item = page.getByLabel("Item, line 1", { exact: true });
  await item.fill("Cassava frozen");
  await item.press("Enter");
  await page.keyboard.type("9");
  await expect(page.getByLabel("Unit, line 1", { exact: true })).toHaveValue("BOX");
  await page.getByRole("button", { name: "Check prices and stock" }).click();
  await expect(page.getByText("Checked against today's prices and stock.")).toBeVisible();
  await page.getByLabel("Goods handed over to the customer").check();
  await page.getByRole("button", { name: "Issue invoice" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Issue invoice" }).click();
  const heading = page.getByRole("heading", { name: /^Invoice TT\d{4}-\d{6}$/ });
  await expect(heading).toBeVisible();
  invoiceNo = ((await heading.textContent()) ?? "").replace("Invoice ", "");
});

test("on arrival the warehouse counts 8 of 10 pieces: the shortage waits for a manager", async ({ page }) => {
  await signIn(page, fx.users.warehouse);
  await page.goto("/purchases"); // by address: the sidebar has its own spec
  await page.getByRole("button", { name: "To verify" }).click();
  const row = page.getByRole("row").filter({ hasText: "KPC-1207" });
  await expect(row).toContainText("Airport · to verify");
  await row.getByRole("link").first().click();
  await expect(page).toHaveURL(purchaseUrl);

  await page.getByRole("button", { name: "Verify pieces" }).click();
  const dlg = page.getByRole("dialog", { name: "Verify pieces" });
  await expect(dlg).toContainText("10 expected (airway bill).");
  await dlg.getByLabel("Pieces counted").fill("8");
  // Two pieces are missing: say which line.
  await dlg.getByLabel("Arrived CFZ").fill("8");
  await dlg.getByRole("button", { name: "Record the difference" }).click();
  await expect(dlg.getByRole("alert")).toContainText("Say why the count differs.");
  await dlg.getByLabel("What happened? (required)").fill("Two boxes missing at the airport");
  await axe(page);
  await page.screenshot({ path: "screenshots/purchase-verify-dialog-1440.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "screenshots/purchase-verify-dialog-390.png" });
  await page.setViewportSize({ width: 1440, height: 900 });
  await dlg.getByRole("button", { name: "Record the difference" }).click();

  await expect(page.getByRole("status").filter({ hasText: "A manager must approve the difference." })).toBeVisible();
  await expect(page.getByText("Airport · needs manager's OK").first()).toBeVisible();
  await expect(page.getByTestId("correction-SHORT")).toContainText("2 boxes Cassava frozen short — waiting for a manager's OK. Stock is unchanged.");
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0); // not the warehouse's call
});

test("the manager approves: stock already invoiced is not forced negative, the rest is flagged for review", async ({ page }) => {
  await signIn(page, fx.users.owner);
  // The owner was alerted.
  await page.goto(purchaseUrl);
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Approved. Stock was corrected." })).toBeVisible();
  // 10 entered, 9 invoiced: only 1 box was still there to take. The other one is flagged.
  const fix = page.getByTestId("correction-SHORT");
  await expect(fix).toContainText("1 box taken from stock; 1 box could not be taken because it was already invoiced or reserved.");
  await expect(fix.getByRole("alert")).toContainText(`invoice ${invoiceNo}`);
  await expect(page.getByText("Verified", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("To review").first()).toBeVisible();
  await axe(page);
  await page.screenshot({ path: "screenshots/purchase-verified-flagged-1440.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await axe(page);
  await page.screenshot({ path: "screenshots/purchase-verified-flagged-390.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });

  // The alert reached the owner's notifications.
  await page.getByRole("button", { name: /Notifications/ }).click();
  await expect(page.getByText(/Review: shortage on stock already invoiced/).first()).toBeVisible();
  await page.keyboard.press("Escape");

  // Stock is at zero, never below; the purchase list shows the flag.
  await page.goto("/inventory");
  await page.getByRole("searchbox").first().fill("CFZ");
  await expect(page.getByText(/-\d+ BOX/)).toHaveCount(0);
  await page.goto("/purchases");
  await expect(page.getByRole("row").filter({ hasText: "KPC-1207" })).toContainText("To review");
  await page.screenshot({ path: "screenshots/purchases-list-airport-1440.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await axe(page);
  await page.screenshot({ path: "screenshots/purchases-list-390.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });

  // The owner looks into it and clears the flag with a note.
  await page.goto(purchaseUrl);
  await page.getByRole("button", { name: "Mark reviewed" }).click();
  const dlg = page.getByRole("dialog", { name: "Mark as reviewed?" });
  await dlg.getByLabel("Reason (required)").fill("Customer credited one box");
  await dlg.getByRole("button", { name: "Mark reviewed" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Marked as reviewed." })).toBeVisible();
  await expect(page.getByText("“Customer credited one box”")).toBeVisible();
});
