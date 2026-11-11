import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// Fresh company: Kingston Kitchen has a VERIFIED sheet; CFZ is 29.00 a box (standard) with stock.
const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
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
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}

async function fillDirect(page: Page) {
  await page.getByLabel("Customer", { exact: true }).selectOption({ label: "Island Grocers (synthetic) (C-1001)" });
  // Prefilled from the customer record (D-12): salesperson default and the bill-to strip.
  await expect(page.getByLabel("Salesperson")).toHaveValue("Sam Synthetic");
  await expect(page.getByLabel("From the customer record")).toContainText("Net");
  const item = page.getByLabel("Item, line 1", { exact: true });
  await item.fill("Cassava frozen");
  await item.press("Enter");
  await expect(page.getByLabel("Quantity, line 1", { exact: true })).toBeFocused();
  await page.keyboard.type("2");
  await expect(page.getByLabel("Unit, line 1", { exact: true })).toHaveValue("BOX");
  await page.getByRole("button", { name: "Check prices and stock" }).click();
  await expect(page.getByTestId("net-total")).toHaveText("CAD 58.00");
  await expect(page.getByText("Checked against today's prices and stock.")).toBeVisible();
}

test("owner creates invoices from a sheet or directly", async ({ page }) => {
  await signIn(page, fx.users.owner);
  // Two-step sign-in is optional (2026-11-09): the owner lands on Overview.
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

  await page.getByRole("link", { name: "Invoices", exact: true }).click();
  await page.getByRole("link", { name: "Create invoice" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Create invoice" })).toBeVisible();
  const sheets = page.getByRole("table", { name: "Picking sheets ready to invoice" });
  await expect(sheets.getByRole("row", { name: /Kingston Kitchen/ }).getByRole("link", { name: /^Prepare invoice for/ })).toBeVisible();
  await axe(page);

  await page.getByRole("navigation", { name: "How to invoice" }).getByRole("link", { name: /Direct invoice/ }).click();
  await fillDirect(page);
  await expect(page.getByRole("button", { name: "Issue invoice" })).toBeDisabled(); // hand-over not ticked
  await page.getByLabel("Goods handed over to the customer").check();
  await axe(page);
  await page.getByRole("button", { name: "Issue invoice" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Issue invoice" }).click();
  await expect(page.getByRole("heading", { name: /^Invoice TT\d{4}-\d{6}$/ })).toBeVisible();
  await expect(page.getByTestId("invoice-total")).toHaveText("CAD 58.00");
});

test("sales can check a direct invoice but not issue it", async ({ page }) => {
  await signIn(page, fx.users.sales);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.goto("/invoices/new?from=direct");
  await fillDirect(page);
  await expect(page.getByRole("button", { name: "Issue invoice" })).toHaveCount(0);
  await expect(page.getByText("A manager issues the invoice.")).toBeVisible();
  await axe(page);
});
