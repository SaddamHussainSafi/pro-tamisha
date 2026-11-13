import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// Fresh synthetic company. The network-scanner bridge is not used any more (owner, 2026-11-12):
// documents are uploaded or imported from the Scan Box.
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

test("New scan offers upload and Scan Box import only: no scanner device, no Scanners tab, no header pill", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.getByText(/^Scanner: /)).toHaveCount(0);
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Scan", exact: true }).click(); // the Scan label opens the Scan Center
  await expect(page.getByRole("heading", { name: "Scan Center" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "Scanners" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "New scan", level: 2 })).toBeVisible();
  await expect(page.getByText("network scanner")).toHaveCount(0);
  await expect(page.getByText("Scanner / device")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Scan now" })).toHaveCount(0);
  await expect(page.getByText("Upload files")).toBeVisible();
  await page.getByRole("button", { name: "Import from Scan Box" }).click();
  const dlg = page.getByRole("dialog", { name: "Import from Scan Box" });
  await expect(dlg).toBeVisible();
  await axe(page);
  await page.screenshot({ path: "screenshots/scan-import-1440.png", fullPage: true });
  await dlg.getByRole("button", { name: /Close/ }).first().click();
  await expect(dlg).toHaveCount(0);
  await page.screenshot({ path: "screenshots/scans-1440.png", fullPage: true });
  // the old ?tab=scanners link lands on New scan
  await page.goto("/scan?tab=scanners");
  await expect(page.getByRole("heading", { name: "New scan", level: 2 })).toBeVisible();
});

test("upload works and a duplicate is recognised", async ({ page }) => {
  await signIn(page, fx.users.sales);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Scan", exact: true }).click(); // the Scan label opens the Scan Center
  const png = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
    "base64",
  );
  const file = { name: "phone-photo.png", mimeType: "image/png", buffer: png };
  // Let the scan list finish its first load so its loading placeholder isn't a second role=status.
  await expect(page.getByRole("heading", { name: "Scan Center" })).toBeVisible();
  await expect(page.getByRole("status", { name: "Loading" })).toHaveCount(0);
  await page.getByTestId("scan-upload").setInputFiles(file);
  await expect(page.getByRole("status")).toContainText("uploaded");
  await page.getByTestId("scan-upload").setInputFiles(file);
  await expect(page.getByRole("status")).toContainText("already uploaded");
  await page.getByTestId("scan-upload").setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hello") });
  await expect(page.getByRole("alert")).toBeVisible();

});
