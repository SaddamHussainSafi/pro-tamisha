import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

// Owner report 2026-11-10: Scan Center upload said "Missing or invalid CSRF token". The token is
// kept per tab, the session cookie is shared: signing in again in another tab (or the dev auto
// sign-in firing twice) left the first tab with a stale token. The tab now renews it and the
// action goes through.
const dir = mkdtempSync(join(tmpdir(), "pt-csrf-"));
const sheetPath = join(dir, "order-sheet.pdf");
const fx = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py", "--sheet", sheetPath], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
) as { password: string; users: { sales: string } };
const sheet = readFileSync(sheetPath);
test.afterAll(() => rmSync(dir, { recursive: true, force: true }));

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.users.sales);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

test("signing in again in another tab doesn't break uploads in the first one", async ({ page, context }) => {
  await signIn(page);
  await page.goto("/scan");
  const stale = await page.evaluate(() => sessionStorage.getItem("pt.csrf"));

  // second tab, same browser, signs in again (like the dev auto sign-in): new session and cookie
  const other = await context.newPage();
  await other.goto("/");
  const r = await other.evaluate(
    async ([email, password]) => (await fetch("/api/v1/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ email, password }) })).status,
    [fx.users.sales, fx.password],
  );
  expect(r).toBe(200);
  await other.close();
  expect(await page.evaluate(() => sessionStorage.getItem("pt.csrf"))).toBe(stale);

  await page.getByTestId("scan-upload").setInputFiles({ name: "order-sheet.pdf", mimeType: "application/pdf", buffer: sheet });
  await expect(page.getByRole("heading", { name: "Scan completed successfully" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Missing or invalid CSRF token")).toHaveCount(0);
  expect(await page.evaluate(() => sessionStorage.getItem("pt.csrf"))).not.toBe(stale);

  // a plain JSON action too (no token at all in this tab)
  await page.evaluate(() => sessionStorage.removeItem("pt.csrf"));
  await page.getByRole("button", { name: "New scan" }).click();
  await page.getByTestId("scan-upload").setInputFiles({ name: "copy.pdf", mimeType: "application/pdf", buffer: Buffer.concat([sheet, Buffer.from("\n% copy\n")]) });
  await expect(page.getByRole("heading", { name: "Scan completed successfully" })).toBeVisible({ timeout: 30_000 });
});
