import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Fresh synthetic company + a hand-filled synthetic PT-OS-1 sheet for customer C-1001:
// CFZ 12 (clear), PLG 3 (smudged → low confidence), and a write-in the catalog doesn't know.
// Reading runs in the real Celery worker with the fake OCR engine.
const dir = mkdtempSync(join(tmpdir(), "pt-ocr-"));
const sheetPath = join(dir, "order-sheet.pdf");
const fx = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py", "--sheet", sheetPath], {
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

test("a scanned order sheet is read, every line is checked, and only a draft order is created", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.users.sales);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

  await page.getByRole("link", { name: /Scan Center/ }).first().click();
  await expect(page.getByRole("heading", { name: "Scan Center" })).toBeVisible();
  await page.getByTestId("scan-upload").setInputFiles({ name: "order-sheet.pdf", mimeType: "application/pdf", buffer: sheet });
  await expect(page.getByRole("status").filter({ hasText: "uploaded" })).toBeVisible();

  // The worker reads it; it shows up in the review queue.
  await page.getByRole("tab", { name: "To review" }).click();
  const row = page.getByRole("row").filter({ hasText: "Needs review" }).first();
  await expect(row).toBeVisible({ timeout: 30_000 });
  await axe(page);
  await page.screenshot({ path: "screenshots/ocr-queue-1440.png", fullPage: true });
  await row.click();

  await expect(page.getByRole("heading", { name: /Review scan/ })).toBeVisible();
  const lines = page.locator("[data-testid^='ocr-line-']");
  await expect(lines).toHaveCount(3);
  const cassava = lines.filter({ hasText: "CFZ" });
  // the original: hovering a line shows its row of the scan, zoomed (no full-page view)
  await cassava.hover();
  await expect(page.getByTestId("scan-lens")).toBeVisible();
  const plantain = lines.filter({ hasText: "PLG" });
  const writeIn = lines.filter({ hasText: "Mango" });
  await expect(plantain).toContainText(/Unclear|Unreadable/); // fake engine vs real OCR: both need a person
  await expect(writeIn).toContainText(/Unknown product|Unreadable/); // depends on the engine; both need a person
  // Unclear lines can't be waved through.
  await expect(plantain.getByRole("button", { name: /^Confirm line/ })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Create draft order" })).toBeDisabled();
  await axe(page);
  await page.screenshot({ path: "screenshots/ocr-review-1440.png", fullPage: true });

  // Clear lines are ticked and confirmed together; unclear ones can't be ticked.
  await expect(plantain.getByRole("checkbox")).toHaveCount(0);
  await cassava.getByRole("checkbox", { name: /^Select line/ }).check();
  await page.getByRole("button", { name: "Confirm selected (1)" }).click();
  await expect(cassava).toContainText("Confirmed");
  await expect(page.getByText("1 line confirmed.")).toBeVisible();

  await plantain.getByRole("button", { name: /^Enter line/ }).click();
  const form = page.getByRole("form", { name: "Correct line" });
  await expect(form.getByLabel("Product")).not.toHaveValue("");
  await form.getByLabel("Quantity").fill("5");
  await form.getByRole("button", { name: "Save line" }).click();
  await expect(plantain).toContainText("Corrected");
  await expect(plantain).toContainText("5");

  await writeIn.getByRole("button", { name: /^Remove line/ }).click();
  await expect(writeIn).toContainText("Removed");

  await expect(page.getByLabel("Customer")).not.toHaveValue("");
  await page.getByLabel("I checked every line against the scan.").check();
  await page.getByRole("button", { name: "Create draft order" }).click();

  await expect(page).toHaveURL(/\/orders\/[0-9a-f-]+$/);
  await expect(page.getByText("Draft", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("CFZ").first()).toBeVisible();
  await expect(page.getByText("PLG").first()).toBeVisible();
  await axe(page);
  await page.screenshot({ path: "screenshots/ocr-draft-order-1440.png", fullPage: true });
});
