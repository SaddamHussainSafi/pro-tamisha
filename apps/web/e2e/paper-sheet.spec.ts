import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Owner report 2026-11-09: scanning a picking sheet / sales order showed no products.
// Fresh synthetic company with the office catalog + a SYNTHETIC scan of the office's paper order
// sheet (TT-PAPER-1), image-only and turned sideways like the office scanner's output, with
// Okra 12, Yellow Yam 3 and Spice Mango 2 written in Q. Read by the real worker (OCR_PROVIDER from
// apps/api/.env; this checks the products come up, so it expects Tesseract or better).
const dir = mkdtempSync(join(tmpdir(), "pt-paper-"));
const sheetPath = join(dir, "paper-sheet.pdf");
const fx = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py", "--paper-sheet", sheetPath], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
) as { password: string; users: { sales: string } };
const sheet = readFileSync(sheetPath);

test.describe.configure({ mode: "serial" });
test.afterAll(() => rmSync(dir, { recursive: true, force: true }));

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.users.sales);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.goto("/scan");
}

const done = (page: Page) => page.locator("section[aria-labelledby='scan-done']");

test("a sideways picking sheet comes up as product lines to review; the same file again still opens them", async ({ page }) => {
  test.setTimeout(150_000);
  await signIn(page);
  await page.getByLabel("Document type").selectOption({ label: "Picking sheet" });
  await page.getByTestId("scan-upload").setInputFiles({ name: "picking-sheet.pdf", mimeType: "application/pdf", buffer: sheet });

  await expect(page.getByRole("heading", { name: "Scan completed successfully" })).toBeVisible({ timeout: 30_000 });
  await expect(done(page).getByText("Picking sheet", { exact: true })).toBeVisible();
  await expect(done(page).locator("dt", { hasText: "Pages" }).locator("..")).toContainText("1");
  await expect(page.getByRole("button", { name: "Review lines" })).toBeVisible({ timeout: 90_000 });

  const lines = page.locator("[data-testid^='ocr-line-']");
  await expect(lines).toHaveCount(3);
  for (const [sku, qty] of [["OKR", "12"], ["YYM", "3"], ["SPM", "2"]] as const) {
    await expect(lines.filter({ hasText: sku })).toContainText(qty);
  }
  // read from pixels: a person checks each one (no "Confirm" for unclear lines)
  await expect(lines.first().getByRole("button", { name: /^Confirm line/ })).toHaveCount(0);
  await axe(page);
  await page.screenshot({ path: "screenshots/paper-sheet-review-1440.png", fullPage: true });

  // The owner's case: the same file uploaded again (this time as a sales order).
  await page.getByRole("button", { name: "New scan" }).click();
  await page.getByLabel("Document type").selectOption({ label: "Sales order" });
  await page.getByTestId("scan-upload").setInputFiles({ name: "again.pdf", mimeType: "application/pdf", buffer: sheet });
  await expect(page.getByRole("heading", { name: "Already on file" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: "Review lines" })).toBeVisible({ timeout: 30_000 });
  await expect(lines).toHaveCount(3);
});

test("an 'Other' document isn't read until someone asks; then its lines come up", async ({ page }) => {
  test.setTimeout(150_000);
  await signIn(page);
  await page.getByLabel("Document type").selectOption({ label: "Other" });
  const copy = Buffer.concat([sheet, Buffer.from("\n% other copy\n")]);
  await page.getByTestId("scan-upload").setInputFiles({ name: "other.pdf", mimeType: "application/pdf", buffer: copy });
  const unread = page.getByTestId("scan-unread");
  await expect(unread).toContainText("aren't read automatically", { timeout: 30_000 });
  await axe(page);
  await unread.getByRole("button", { name: "Read this scan" }).click();
  await expect(page.getByRole("button", { name: "Review lines" })).toBeVisible({ timeout: 90_000 });
  await expect(page.locator("[data-testid^='ocr-line-']")).toHaveCount(3);
});
