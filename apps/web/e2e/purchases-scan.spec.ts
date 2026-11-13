import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string }; local: string };

// Fresh synthetic company with nothing in stock, and a local supplier's invoice (Accra Roots:
// 25 boxes cassava at 12.50, 10 boxes plantain at 30.00) already scanned and read, waiting for a
// person to check it. The reading was made by the in-process test reader, never a real engine.
const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/purchases_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

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

test("a scanned local invoice goes straight to stock once a person has checked it", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.goto(`/scan/review/${fx.local}`);
  const grid = page.getByRole("table", { name: "Recognized lines" });
  await expect(grid).toContainText("Cassava frozen");
  const save = page.getByRole("region", { name: "Save & add to stock" });
  // Nothing goes to stock from the reading alone: every line must be checked first.
  await expect(save.getByRole("button", { name: "Save & add to stock" })).toBeDisabled();
  await page.getByRole("checkbox", { name: "Select all clear lines" }).check();
  await page.getByRole("button", { name: "Confirm selected (2)" }).click();
  await expect(page.getByText("2 lines confirmed.")).toBeVisible();

  // Cassava tracks expiry: its date is asked for before it can be sold.
  await save.getByLabel("Cassava frozen").fill("2027-06-30");
  await expect(save.getByLabel("Airport shipment, verify pieces on arrival")).not.toBeChecked(); // a local supplier
  await save.getByLabel(/I checked every line/).check();
  await axe(page);
  await page.screenshot({ path: "screenshots/purchase-scan-review-1440.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await save.getByRole("button", { name: "Save & add to stock" }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: "screenshots/purchase-scan-review-390.png", fullPage: true });
  await page.setViewportSize({ width: 1440, height: 900 });
  await save.getByRole("button", { name: "Save & add to stock" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Saved as a purchase. Its goods are in stock now." })).toBeVisible();

  await page.getByRole("link", { name: "Open the purchase" }).click();
  await expect(page.getByRole("heading", { name: /^Purchase SHP-/ })).toBeVisible();
  await expect(page.getByText("Accra Roots Exports (synthetic) · Invoice ARX-5521")).toBeVisible();
  await expect(page.getByText("In stock", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("line-CFZ")).toContainText("25 boxes");
  await expect(page.getByTestId("line-CFZ")).toContainText("12.50");
  await expect(page.getByTestId("line-PLG")).toContainText("USD 300.00");
  await expect(page.getByRole("region", { name: "Invoice lines" })).toContainText("USD 612.50");
  // The scanned file is on the purchase, as received.
  await expect(page.getByRole("list", { name: "Documents" })).toContainText("Supplier invoice ARX-5521");
  // No verification and no approval: a local purchase is simply in stock.
  await expect(page.getByRole("button", { name: "Verify pieces" })).toHaveCount(0);
  await axe(page);
  await page.screenshot({ path: "screenshots/purchase-from-scan-1440.png", fullPage: true });

  // It is on the Orders page, next to the sales orders, and the supplier is owed the invoice total.
  await page.goto("/orders");
  await page.getByRole("tab", { name: "Purchases" }).click();
  const row = page.getByRole("row").filter({ hasText: "ARX-5521" });
  await expect(row).toContainText("Accra Roots Exports");
  await expect(row).toContainText("USD 612.50");
  await expect(row).toContainText("In stock");
  await page.goto("/inventory");
  await page.getByRole("searchbox").first().fill("PLG");
  await expect(page.getByRole("row").filter({ hasText: /PLG-/ }).first()).toContainText("10");
  // The same file can't be turned into stock twice.
  await page.goto(`/scan/review/${fx.local}`);
  await expect(page.getByRole("button", { name: "Save & add to stock" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Open the purchase" })).toBeVisible();
});
