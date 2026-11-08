import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { totp } from "./totp";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// Fresh synthetic company with the seeded catalog and the active book PB-2026-01.
const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
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
}

async function ownerSignIn(page: Page) {
  await signIn(page, fx.users.owner);
  await expect(page).toHaveURL(/\/mfa$/);
  await page.getByRole("button", { name: "Set up authenticator" }).click();
  const secret = (await page.getByTestId("mfa-secret").textContent()) ?? "";
  await page.getByLabel("6-digit code").fill(totp(secret));
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}

async function pick(page: Page, selectId: string, text: string) {
  const sel = page.locator(`#${selectId}`);
  await expect(sel.locator("option", { hasText: text })).toHaveCount(1);
  const value = await sel.locator("option", { hasText: text }).getAttribute("value");
  await sel.selectOption(value ?? "");
}

async function check(page: Page, sku: string, unit: string, customer?: string) {
  await pick(page, "lk-prod", `(${sku})`);
  await page.locator("#lk-unit").selectOption(unit);
  if (customer) await pick(page, "lk-cust", customer);
  else await page.locator("#lk-cust").selectOption("");
  await page.getByRole("button", { name: "Check price" }).click();
}

const BASE = [
  "sku,unit,rate,customer_ref,group_code",
  "CAS-FZ,BOX,29.00,,",
  "CAS-FZ,LB,1.40,,WHOLESALE",
  "CAS-FZ,LB,1.35,C-1001,",
  "PLN-GR,LB,0.95,,",
  "PLN-GR,PIECE,0.40,,",
  "PLN-GR,BOX,45.00,,RESTAURANT",
  "RICE-JAS,KG,2.10,,",
  "RICE-JAS,BOX,39.50,,",
];
const NEW_BOOK = [...BASE, "CAS-FZ,LB,1.60,,"].join("\n");
const BAD_UNIT = [...BASE, "CAS-FZ,LB,1.60,,", "RICE-JAS,PIECE,0.75,,", "CAS-FZ,TON,9,,"].join("\n");

test.describe.configure({ mode: "serial" });

test("owner imports a new price book; old rate stays on the old version; duplicates and bad units rejected", async ({ page }) => {
  await ownerSignIn(page);
  await page.getByRole("link", { name: /Pricing/ }).click();
  await expect(page.getByRole("heading", { name: "Pricing", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "PB-2026-01" })).toBeVisible();

  // Precedence: customer beats group beats standard.
  await check(page, "CAS-FZ", "LB");
  await expect(page.getByTestId("quote")).toContainText("CAD 1.50");
  await check(page, "CAS-FZ", "LB", "Island Grocers");
  await expect(page.getByTestId("quote")).toContainText("CAD 1.35");
  await check(page, "CAS-FZ", "LB", "Kingston Kitchen");
  await expect(page.getByTestId("quote")).toContainText("CAD 1.50");
  await axe(page);

  // Invalid units are reported per row; nothing can be created.
  await page.getByRole("button", { name: "Import CSV" }).click();
  const imp = page.getByRole("form", { name: "Import price list (CSV)" });
  await imp.getByText("Or paste CSV text").click();
  await imp.getByLabel("CSV text").fill(BAD_UNIT);
  await imp.getByRole("button", { name: "Check file" }).click();
  const errs = page.getByRole("region", { name: "Row errors" });
  await expect(errs).toContainText("RICE-JAS has no active PIECE unit profile");
  await expect(errs).toContainText("INVALID_UNIT");
  await expect(imp.getByRole("button", { name: "Create draft" })).toBeDisabled();
  await axe(page);

  // Valid file: diff shows exactly one changed rate.
  await imp.getByLabel("CSV text").fill(NEW_BOOK);
  await imp.getByRole("button", { name: "Check file" }).click();
  await expect(page.getByTestId("diff-summary")).toHaveText("1 changed · 0 added · 0 removed · 8 unchanged");
  await expect(page.getByRole("region", { name: "Changed prices" })).toContainText("CAD 1.60");
  await imp.getByLabel("Version code").fill("PB-E2E-02");
  await axe(page);
  await imp.screenshot({ path: "screenshots/pricing-import-1440.png" });
  await imp.getByRole("button", { name: "Create draft" }).click();

  await expect(page.getByRole("heading", { name: "PB-E2E-02" })).toBeVisible();
  await expect(page.getByText("Draft", { exact: true }).first()).toBeVisible();
  await axe(page);
  await page.getByRole("button", { name: "Activate…" }).click();
  const dlg = page.getByRole("dialog");
  await expect(dlg).toContainText("1 changed · 0 added · 0 removed");
  await dlg.getByRole("button", { name: "Activate" }).click();
  await expect(page.getByText("Published price books are read-only")).toBeVisible();
  await expect(page.getByText("Current", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Set a price" })).toHaveCount(0);
  await page.screenshot({ path: "screenshots/pricing-version-1440.png", fullPage: true });

  // New price applies now; the previous version still holds 1.50 (immutable, INV-005).
  await page.getByRole("navigation", { name: "Breadcrumb" }).getByRole("link", { name: "Pricing" }).click();
  await check(page, "CAS-FZ", "LB");
  await expect(page.getByTestId("quote")).toContainText("CAD 1.60");
  await expect(page.getByTestId("quote")).toContainText("PB-E2E-02");
  await page.getByRole("link", { name: "PB-2026-01" }).click();
  await expect(page.getByText("Superseded", { exact: true }).first()).toBeVisible();
  const oldRow = page.getByRole("row").filter({ hasText: "CAS-FZ" }).filter({ hasText: "per lb" }).filter({ hasText: "Standard" });
  await expect(oldRow).toContainText("CAD 1.50");

  // Uploading the same file again is caught before any version is created.
  await page.getByRole("navigation", { name: "Breadcrumb" }).getByRole("link", { name: "Pricing" }).click();
  await page.getByRole("button", { name: "Import CSV" }).click();
  const imp2 = page.getByRole("form", { name: "Import price list (CSV)" });
  await imp2.getByText("Or paste CSV text").click();
  await imp2.getByLabel("CSV text").fill(NEW_BOOK.split("\n").reverse().join("\n").replace(/^(.*)\n(sku,.*)$/s, "$2\n$1"));
  await imp2.getByRole("button", { name: "Check file" }).click();
  await expect(imp2.getByRole("alert")).toContainText("already uploaded");
  await expect(imp2.getByRole("button", { name: "Create draft" })).toBeDisabled();
  await axe(page);
});

test("sales can check prices but cannot draft or activate", async ({ page }) => {
  await signIn(page, fx.users.sales);
  await page.getByRole("link", { name: /Pricing/ }).click();
  await expect(page.getByRole("link", { name: "PB-E2E-02" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Import CSV" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "New draft" })).toHaveCount(0);
  await check(page, "CAS-FZ", "LB", "Island Grocers");
  await expect(page.getByTestId("quote")).toContainText("CAD 1.35");
  await page.getByRole("link", { name: "PB-E2E-02" }).click();
  await expect(page.getByRole("button", { name: "Activate…" })).toHaveCount(0);
  await axe(page);
});
