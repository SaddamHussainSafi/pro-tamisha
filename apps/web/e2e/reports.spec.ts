import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// Fresh company: Kingston Kitchen (C-1002) has a VERIFIED sheet for 7 boxes of CFZ.
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

test("reports reconcile with an issued invoice, filter, and export CSV", async ({ page }) => {
  await signIn(page, fx.users.owner);
  // Two-step sign-in is optional (2026-11-09): the owner lands on Overview.
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

  // The sidebar entry is live (no longer "Soon").
  await page.getByRole("link", { name: "Dashboard", exact: true }).click();
  await expect(page).toHaveURL(/\/reports$/); // the address did not change with the name
  await expect(page.getByRole("heading", { level: 1, name: "Dashboard" })).toBeVisible();
  await expect(page.getByText("No invoices in this period")).toBeVisible();
  // Seeded opening balances show up in the stock movement table.
  const stock = page.getByRole("table", { name: /^Stock movement/ });
  await expect(stock.getByRole("row", { name: /CFZ/ }).first()).toBeVisible();
  await axe(page);

  // Issue Kingston Kitchen's invoice.
  await page.goto("/packing");
  await page.getByRole("radio", { name: "Done" }).or(page.getByRole("button", { name: "Done" })).first().click();
  await page.getByRole("row", { name: /Kingston Kitchen/ }).getByRole("link").first().click();
  await page.getByRole("link", { name: "Prepare invoice" }).click();
  await page.getByLabel("The goods on this sheet have been dispatched to the customer").check();
  await page.getByRole("button", { name: "Issue invoice" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Issue invoice" }).click();
  await expect(page.getByRole("heading", { name: /^Invoice TT\d{4}-\d{6}$/ })).toBeVisible();
  const total = ((await page.getByTestId("invoice-total").textContent()) ?? "").trim();
  expect(total).toMatch(/^CAD [\d,]+\.\d{2}$/);

  // Reports now count it: the customer's "with tax" figure equals the invoice total.
  await page.goto("/reports");
  const customers = page.getByRole("table", { name: /^Customers/ });
  const row = customers.getByRole("row", { name: /Kingston Kitchen/ });
  await expect(row).toBeVisible();
  await expect(row.getByRole("cell").nth(4)).toHaveText(total);
  await expect(row.getByRole("cell").nth(1)).toContainText("7.00 boxes");
  await expect(page.getByRole("table", { name: /^Products/ }).getByRole("row", { name: /CFZ/ })).toBeVisible();
  await expect(stock.getByRole("row", { name: /CFZ/ }).first()).toContainText("−7.00");
  await axe(page);

  // CSV export of the rows on screen.
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Download customers as CSV" }).click()]);
  expect(download.suggestedFilename()).toMatch(/^pro-trading-customers-\d{4}-\d{2}-\d{2}-to-\d{4}-\d{2}-\d{2}\.csv$/);
  const csv = readFileSync((await download.path()) ?? "", "utf8");
  expect(csv.split("\r\n")[0]).toBe('Customer,Customer ref,Invoices,Quantities,"Net sales (CAD, before tax)",With tax (CAD),Last invoice');
  expect(csv).toContain("Kingston Kitchen (synthetic),C-1002,1,");

  // A customer filter with no sales empties the sales tables but keeps stock.
  await page.getByLabel("Customer (sales only)").selectOption({ label: "Island Grocers (synthetic) (C-1001)" });
  await expect(page.getByText("No invoices in this period")).toBeVisible();
  await expect(stock.getByRole("row", { name: /CFZ/ }).first()).toBeVisible();
  await page.getByLabel("Customer (sales only)").selectOption({ label: "All customers" });
  await expect(row).toBeVisible();

  // Bad custom range is caught before any request.
  await page.getByLabel("From").fill("2099-01-01");
  await expect(page.getByRole("button", { name: "Custom", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("alert")).toHaveText("The start date is after the end date.");
  await page.getByRole("button", { name: "Last 30 days", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(row).toBeVisible();
});

test("sales role cannot open reports", async ({ page }) => {
  await signIn(page, fx.users.sales);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.goto("/reports");
  await expect(page.getByText("You don't have access to reports")).toBeVisible();
  await axe(page);
});
