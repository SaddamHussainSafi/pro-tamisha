import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// Fresh company with seeded customers Island Grocers (C-1001) and Kingston Kitchen (C-1002).
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

test("owner adds, edits and deactivates a customer; stale edits are refused", async ({ page }) => {
  await signIn(page, fx.users.owner);
  // Two-step sign-in is optional (2026-11-09): the owner lands on Overview.
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

  // Sidebar entry is live (no longer "Soon"); seeded customers are listed and searchable.
  await page.getByRole("link", { name: "Customers", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Customers" })).toBeVisible();
  const grid = page.getByRole("list", { name: /^Customers/ }); // cards by default
  await expect(grid.getByRole("link", { name: /Island Grocers/ })).toBeVisible();
  await page.getByLabel("Search name or reference").fill("C-1002");
  await expect(grid.getByRole("link", { name: /Kingston Kitchen/ })).toBeVisible();
  await expect(grid.getByRole("link", { name: /Island Grocers/ })).toHaveCount(0);
  await page.getByLabel("Search name or reference").fill("");
  await axe(page);

  // Add a group, then a customer in it (bad email first).
  await page.getByLabel("Group code").fill("e2e");
  await page.getByLabel("Group name").fill("E2E caterers");
  await page.getByRole("button", { name: "Add group" }).click();
  await expect(page.getByRole("status")).toHaveText("Group E2E caterers added.");
  await page.getByRole("button", { name: "New customer" }).click();
  await page.getByLabel("Name", { exact: true }).fill("Spice Route Market");
  await page.getByLabel("Customer reference (optional)").fill("C-2001");
  await page.getByLabel("Email for invoices and reminders").fill("not-an-email");
  await page.getByLabel("Customer group", { exact: true }).selectOption({ label: "E2E caterers (E2E)" });
  await page.getByLabel("Payment terms (days)").fill("14");
  await page.getByRole("button", { name: "Add customer" }).click();
  await expect(page.getByText("Enter an email like accounts@example.com, or leave it empty.")).toBeVisible();
  await expect(page.getByLabel("Email for invoices and reminders")).toHaveAttribute("aria-invalid", "true");
  await page.getByLabel("Email for invoices and reminders").fill("ap@spice.example.test");
  await page.getByRole("button", { name: "Add customer" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Spice Route Market" })).toBeVisible();
  await expect(page.getByRole("status")).toHaveText("Customer added.");
  await expect(page.getByText("Net 14 days")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save changes" })).toBeDisabled();
  await axe(page);

  // Someone else changes the customer meanwhile (same API the screen uses).
  const id = page.url().split("/").pop() ?? "";
  const csrf = (await page.evaluate(() => sessionStorage.getItem("pt.csrf"))) ?? "";
  const bg = await page.request.patch(`/api/v1/customers/${id}`, {
    headers: { "X-CSRF-Token": csrf, "Idempotency-Key": `e2e-${Date.now()}-bg-edit` },
    data: { version: 1, payment_terms_days: 30 },
  });
  expect(bg.status()).toBe(200);

  // This screen still holds version 1: its save is refused and the latest details load.
  await page.getByLabel("Phone").fill("416-555-0101");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("alert")).toContainText("Someone else changed this customer");
  await expect(page.getByLabel("Payment terms (days)")).toHaveValue("30");
  await expect(page.getByLabel("Phone")).toHaveValue("");

  // Clear the email, deactivate.
  await page.getByLabel("Email for invoices and reminders").fill("");
  await page.getByLabel("Active customer").uncheck();
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("status")).toHaveText("Customer saved.");
  await expect(page.getByText("Net 30 days")).toBeVisible();
  await expect(page.getByText("Inactive", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Email for invoices and reminders")).toHaveValue("");

  // Inactive customers leave the default list but stay findable.
  await page.getByRole("link", { name: "All customers" }).click();
  await expect(grid.getByRole("link", { name: /Island Grocers/ })).toBeVisible();
  await expect(grid.getByRole("link", { name: "Spice Route Market" })).toHaveCount(0);
  await page.getByRole("button", { name: "Inactive", exact: true }).click();
  await expect(grid.getByRole("link", { name: "Spice Route Market" })).toBeVisible();
});

test("sales can look customers up but not change them", async ({ page }) => {
  await signIn(page, fx.users.sales);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.goto("/customers");
  await expect(page.getByRole("heading", { level: 1, name: "Customers" })).toBeVisible();
  await expect(page.getByRole("button", { name: "New customer" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Add group" })).toHaveCount(0);
  await page.getByRole("list", { name: /^Customers/ }).getByRole("link", { name: /Kingston Kitchen/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: /Kingston Kitchen/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Save changes" })).toHaveCount(0);
  await expect(page.getByText("Net 14 days").first()).toBeVisible();
  await axe(page);
  // Invoices and orders each have their own tab.
  await page.getByRole("navigation", { name: "Customer sections" }).getByRole("link", { name: "Orders" }).click();
  await expect(page).toHaveURL(/\?tab=orders$/);
  await expect(page.getByRole("table", { name: "Customer orders" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Customer orders" }).getByRole("link").first()).toHaveText(/^SO-/);
  await page.getByRole("navigation", { name: "Customer sections" }).getByRole("link", { name: "Invoices" }).click();
  await expect(page.getByRole("heading", { name: "Invoices", exact: true })).toBeVisible();
  await expect(page.getByText(/^\d+ invoices?/)).toBeVisible();
  await axe(page);
});
