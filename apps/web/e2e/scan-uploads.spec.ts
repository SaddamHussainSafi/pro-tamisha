import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

// Fresh company + a filled PT-OS-1 order sheet. Two different files are uploaded at once; each
// goes through the real stages (upload bytes -> OCR in the worker -> ready for review).
const dir = mkdtempSync(join(tmpdir(), "pt-multi-"));
const sheetPath = join(dir, "order-sheet.pdf");
const fx: { password: string; users: { sales: string } } = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py", "--sheet", sheetPath], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);
const pdf = readFileSync(sheetPath);

test("several files upload with real progress and each reaches review", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.users.sales);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.goto("/scan");
  await page.getByTestId("scan-upload").setInputFiles([
    { name: "sheet-a.pdf", mimeType: "application/pdf", buffer: pdf },
    { name: "sheet-b.pdf", mimeType: "application/pdf", buffer: Buffer.concat([pdf, Buffer.from("\n% copy b\n")]) },
  ]);
  const items = page.getByTestId("upload-item");
  await expect(items).toHaveCount(2);
  for (const i of [0, 1]) {
    await expect(items.nth(i).getByRole("link", { name: /^Open SCN-/ })).toBeVisible({ timeout: 30_000 });
    await expect(items.nth(i).getByRole("link", { name: "Review" })).toBeVisible({ timeout: 90_000 });
    await expect(items.nth(i).getByText("Ready for review")).toBeVisible();
  }
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
});
