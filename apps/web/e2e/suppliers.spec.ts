import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { totp } from "./totp";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// Fresh company with seeded suppliers (V-ACC Accra Roots Exports, USD).
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

test("owner records and voids a supplier payment; the ledger and balance follow", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.getByRole("button", { name: "Set up authenticator" }).click();
  const secret = (await page.getByTestId("mfa-secret").textContent()) ?? "";
  await page.getByLabel("6-digit code").fill(totp(secret));
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

  await page.getByRole("link", { name: "Suppliers", exact: true }).click();
  await page.getByRole("table", { name: "Suppliers" }).getByRole("link", { name: "Accra Roots Exports (synthetic)" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Accra Roots Exports (synthetic)" })).toBeVisible();
  await expect(page.getByText("Nothing on the ledger yet")).toBeVisible();
  await axe(page);

  await page.getByRole("button", { name: "Record payment" }).click();
  await page.getByLabel("Amount paid").fill("500");
  await expect(page.getByLabel("Currency")).toHaveValue("USD");
  await page.getByLabel("Bank or cheque reference").fill("WIRE-E2E-1");
  await page.getByRole("button", { name: "Record payment" }).click();
  await expect(page.getByRole("status")).toContainText(/SPAY-\d{4}-\d{6} recorded: USD 500\.00/);
  await expect(page.getByText("Paid USD 500.00 ahead")).toBeVisible();
  const ledger = page.getByRole("table", { name: "Supplier ledger" });
  await expect(ledger.getByRole("row", { name: /Payment SPAY-/ })).toContainText("−USD 500.00");

  // Same bank reference twice is refused.
  await page.getByRole("button", { name: "Record payment" }).click();
  await page.getByLabel("Amount paid").fill("500");
  await page.getByLabel("Bank or cheque reference").fill("wire-e2e-1");
  await page.getByRole("button", { name: "Record payment" }).click();
  await expect(page.getByRole("alert")).toContainText("same payment twice");
  await page.getByRole("button", { name: "Cancel" }).click();

  // Void it from the Payments tab.
  await page.getByRole("navigation", { name: "Supplier sections" }).getByRole("link", { name: /^Payments/ }).click();
  await page.getByRole("button", { name: /^Void SPAY-/ }).click();
  await page.getByLabel("Why? (required)").fill("Entered twice by mistake");
  await page.getByRole("dialog").getByRole("button", { name: "Void payment" }).click();
  await expect(page.getByRole("status")).toContainText("voided");
  await expect(page.getByText("Voided: Entered twice by mistake")).toBeVisible();
  await expect(page.getByText("Nothing owed")).toBeVisible();
  await axe(page);

  // Suppliers › Supplier payments lists it.
  await page.getByRole("link", { name: "Supplier payments" }).click(); // sub-menu is open inside Suppliers
  await expect(page.getByRole("heading", { level: 1, name: "Supplier payments" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Supplier payments" }).getByRole("row", { name: /Accra Roots Exports/ })).toContainText("Voided");
  await axe(page);
});

test("sales cannot see supplier accounts", async ({ page }) => {
  await signIn(page, fx.users.sales);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.goto("/suppliers/payments");
  await expect(page.getByText("You don't have access to supplier accounts")).toBeVisible();
  await axe(page);
});
