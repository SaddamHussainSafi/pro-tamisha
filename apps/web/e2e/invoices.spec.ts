import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { totp } from "./totp";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// Fresh company: Kingston Kitchen (C-1002) has a VERIFIED sheet for 7 boxes of CAS-FZ (standard LB 1.50, BOX 29.00).
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

async function openSeededSheet(page: Page) {
  await page.goto("/picking");
  await page.getByRole("radio", { name: "Done" }).or(page.getByRole("button", { name: "Done" })).first().click();
  await page.getByRole("row", { name: /Kingston Kitchen/ }).getByRole("link").first().click();
  await expect(page.getByRole("heading", { name: /^Picking sheet SO-.*-P1$/ })).toBeVisible();
}

test("sales sees the preview but cannot issue", async ({ page }) => {
  await signIn(page, fx.users.sales);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await openSeededSheet(page);
  await page.getByRole("link", { name: "Prepare invoice" }).click();
  await expect(page.getByTestId("invoice-total")).toHaveText(/203\.00/);
  await expect(page.getByText("A manager issues this invoice")).toBeVisible();
  await expect(page.getByRole("button", { name: "Issue invoice" })).toHaveCount(0);
});

test("owner bills 7 cases by weight (205 lb × 1.50 = 307.50) and issues once", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.getByRole("button", { name: "Set up authenticator" }).click();
  const secret = (await page.getByTestId("mfa-secret").textContent()) ?? "";
  await page.getByLabel("6-digit code").fill(totp(secret));
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

  await openSeededSheet(page);
  await page.getByRole("link", { name: "Prepare invoice" }).click();
  await expect(page.getByTestId("stock-CAS-FZ")).toContainText("In stock");

  const cw = page.getByTestId("cw-CAS-FZ");
  await cw.getByLabel("Bill CAS-FZ by weight (per lb)").check();
  // Weights 20, 30, 40, 40, 25, 25, 25 → rows of identical weights.
  const rows: [string, string][] = [["1", "20"], ["1", "30"], ["2", "40"], ["3", "25"]];
  for (const [i, [cases, w]] of rows.entries()) {
    if (i > 0) await cw.getByRole("button", { name: "Add row" }).click();
    await cw.getByLabel(`Cases, row ${i + 1}`).fill(cases);
    await cw.getByLabel(`Weight each, row ${i + 1}`).fill(w);
  }
  await expect(cw).toContainText("7 of 7 cases · 205");
  await page.getByRole("button", { name: "Update preview" }).click();
  await expect(page.getByTestId("invoice-total")).toHaveText(/307\.50/);
  await axe(page);
  await page.screenshot({ path: "screenshots/invoices-issue-1440.png", fullPage: true });

  const issue = page.getByRole("button", { name: "Issue invoice" });
  await expect(issue).toBeDisabled();
  await page.getByLabel("The goods on this sheet have been dispatched to the customer").check();
  await issue.click();
  await page.getByRole("dialog").getByRole("button", { name: "Issue invoice" }).click();

  await expect(page.getByRole("heading", { name: /^Invoice INV-\d{4}-\d{6}$/ })).toBeVisible();
  await expect(page.getByTestId("invoice-total")).toHaveText(/307\.50/);
  await expect(page.getByRole("region", { name: "Invoice lines" })).toContainText("3 × 25 lb (weighed)");
  await expect(page.getByText(/CAS-FZ-SEED-2/)).toBeVisible();
  await axe(page);
  await page.screenshot({ path: "screenshots/invoices-detail-1440.png", fullPage: true });

  // Stored PDF is served byte-for-byte with its fingerprint.
  const href = (await page.getByRole("link", { name: "Download PDF" }).getAttribute("href")) ?? "";
  const pdf = await page.request.get(href);
  expect(pdf.status()).toBe(200);
  expect(pdf.headers()["content-type"]).toContain("application/pdf");
  expect((await pdf.body()).subarray(0, 5).toString()).toBe("%PDF-");

  // The sheet now links back to its invoice.
  const invUrl = page.url();
  await page.getByRole("link", { name: /-P1$/ }).click();
  await page.getByRole("link", { name: "View invoice" }).click();
  await expect(page).toHaveURL(invUrl);

  await page.goto("/invoices");
  await expect(page.getByRole("table", { name: /Invoices \(1\)/ })).toContainText("Kingston Kitchen");
  await axe(page);
  await page.screenshot({ path: "screenshots/invoices-list-1440.png", fullPage: true });
});
