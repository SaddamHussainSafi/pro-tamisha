import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { totp } from "./totp";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// Fresh company: Kingston Kitchen (C-1002) has a VERIFIED sheet for 7 boxes of CAS-FZ.
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

/** "CAD 1,234.50" <-> integer cents (no floats for money). */
const cents = (s: string) => {
  const [i = "0", f = "00"] = s.replace(/[^\d.]/g, "").split(".");
  return Number(i) * 100 + Number(f.padEnd(2, "0").slice(0, 2));
};
const cad = (c: number) => `CAD ${Math.floor(c / 100).toLocaleString("en-US")}.${String(c % 100).padStart(2, "0")}`;

let invoiceNo = "";
let total = 0;
const bankRef = `E2E-${Date.now()}`;

async function recordPayment(page: Page, amount: string) {
  await page.goto("/payments");
  await page.getByRole("button", { name: "Record payment" }).click();
  const customer = page.getByRole("combobox", { name: /^Customer/ });
  await customer.selectOption((await customer.locator("option", { hasText: "Kingston Kitchen" }).getAttribute("value")) ?? "");
  await expect(page.getByRole("combobox", { name: /^For invoice/ }).locator("option", { hasText: invoiceNo })).toHaveCount(1);
  await page.getByRole("combobox", { name: /^For invoice/ }).selectOption({ index: 1 });
  await page.getByLabel("Amount (CAD)").fill(amount);
  await page.getByLabel("What the customer quoted (optional)").fill("e-Transfer KK-77 (synthetic)");
  await page.getByRole("button", { name: "Save as reported" }).click();
  await expect(page.getByRole("heading", { name: /^Payment PAY-\d{4}-\d{6}$/ })).toBeVisible();
}

test("reported payment is confirmed and applied; invoice and receivables show what is left", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.getByRole("button", { name: "Set up authenticator" }).click();
  const secret = (await page.getByTestId("mfa-secret").textContent()) ?? "";
  await page.getByLabel("6-digit code").fill(totp(secret));
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

  // Issue an invoice to pay.
  await page.goto("/picking");
  await page.getByRole("radio", { name: "Done" }).or(page.getByRole("button", { name: "Done" })).first().click();
  await page.getByRole("row", { name: /Kingston Kitchen/ }).getByRole("link").first().click();
  await page.getByRole("link", { name: "Prepare invoice" }).click();
  await page.getByLabel("The goods on this sheet have been dispatched to the customer").check();
  await page.getByRole("button", { name: "Issue invoice" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Issue invoice" }).click();
  const heading = page.getByRole("heading", { name: /^Invoice INV-\d{4}-\d{6}$/ });
  await expect(heading).toBeVisible();
  invoiceNo = ((await heading.textContent()) ?? "").replace("Invoice ", "");
  total = cents((await page.getByTestId("invoice-total").textContent()) ?? "");
  expect(total).toBeGreaterThan(10000);
  await expect(page.getByTestId("invoice-balance")).toHaveText(cad(total));

  // Customer says they sent 100.00: recorded as Reported, nothing paid yet.
  await page.getByRole("link", { name: /^Payments/ }).first().click();
  await expect(page.getByRole("heading", { name: "Payments" })).toBeVisible();
  await recordPayment(page, "100.00");
  await expect(page.getByText("Reported", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/This app never logs in to the bank/)).toBeVisible();
  await axe(page);
  await page.screenshot({ path: "screenshots/payment-confirm-1440.png", fullPage: true });

  // Confirm at the bank: the reported invoice is prefilled with 100.00.
  await expect(page.getByLabel(`Amount to apply to ${invoiceNo}`)).toHaveValue("100.00");
  await expect(page.getByTestId(`left-${invoiceNo}`)).toHaveText(cad(total - 10000));
  await page.getByLabel("Bank reference of the deposit").fill(bankRef);
  await page.getByRole("button", { name: "Confirm payment" }).click();
  await expect(page.getByRole("status")).toHaveText("Confirmed and applied CAD 100.00.");
  await expect(page.getByText("Applied", { exact: true }).first()).toBeVisible();
  const history = page.getByRole("region", { name: "Applied to invoices" });
  await expect(history.getByRole("row", { name: new RegExp(invoiceNo) })).toContainText("Partly paid");
  await axe(page);
  await page.screenshot({ path: "screenshots/payment-detail-1440.png", fullPage: true });

  // The invoice now shows the remaining balance.
  await history.getByRole("link", { name: invoiceNo }).click();
  await expect(page.getByTestId("invoice-balance")).toHaveText(cad(total - 10000));
  await expect(page.getByText("Partly paid").first()).toBeVisible();

  // Receivables: Kingston Kitchen owes the rest, not yet due.
  await page.goto("/receivables");
  await expect(page.getByRole("heading", { name: "Receivables" })).toBeVisible();
  const row = page.getByRole("row", { name: /Kingston Kitchen/ });
  await expect(row).toContainText(cad(total - 10000));
  await expect(page.getByText(/placeholders until the owner decides \(D-16\)/)).toBeVisible();
  await axe(page);
  await page.screenshot({ path: "screenshots/receivables-1440.png", fullPage: true });

  // Same e-Transfer reported twice: the bank reference cannot confirm it again; reject it instead.
  await recordPayment(page, "100.00");
  await page.getByLabel("Bank reference of the deposit").fill(bankRef);
  await page.getByRole("button", { name: "Confirm payment" }).click();
  await expect(page.getByRole("alert")).toContainText(/already confirmed payment PAY-\d{4}-\d{6}/);
  await page.getByRole("button", { name: "Not found at the bank…" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Why (kept on record)").fill("Same transfer reported twice");
  await dialog.getByRole("button", { name: "Reject payment" }).click();
  await expect(page.getByRole("status")).toHaveText("Payment marked as rejected. It stays on record.");
  await expect(page.getByText("Same transfer reported twice")).toBeVisible();
  await page.goto(`/invoices`);
  await expect(page.getByRole("row", { name: new RegExp(invoiceNo) })).toContainText(cad(total - 10000));
});

test("sales can record a payment but cannot confirm money", async ({ page }) => {
  await signIn(page, fx.users.sales);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await recordPayment(page, "25.00");
  await expect(page.getByText("Reported", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirm payment" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Confirm at the bank" })).toHaveCount(0);
  await axe(page);
});
