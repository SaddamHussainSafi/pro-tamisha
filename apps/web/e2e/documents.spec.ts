import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// Fresh company: Kingston Kitchen (C-1002) has a VERIFIED sheet for 7 boxes of CFZ.
const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

// A synthetic signed delivery note (1×1 PNG).
const pod = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
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

test("issued invoice is filed and copied to the archive; POD upload is appended", async ({ page }) => {
  await signIn(page, fx.users.owner);
  // Two-step sign-in is optional (2026-11-09): the owner lands on Overview.
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

  await page.goto("/packing");
  await page.getByRole("radio", { name: "Done" }).or(page.getByRole("button", { name: "Done" })).first().click();
  await page.getByRole("row", { name: /Kingston Kitchen/ }).getByRole("link").first().click();
  await page.getByRole("link", { name: "Prepare invoice" }).click();
  await page.getByLabel("The goods on this sheet have been dispatched to the customer").check();
  await page.getByRole("button", { name: "Issue invoice" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Issue invoice" }).click();
  const heading = page.getByRole("heading", { name: /^Invoice TT\d{4}-\d{6}$/ });
  await expect(heading).toBeVisible();
  const invoiceNo = ((await heading.textContent()) ?? "").replace("Invoice ", "");

  // The archive panel lists the issued PDF; the worker copies it to Drive in the background.
  const archive = page.getByRole("region", { name: "Archive" });
  await expect(archive.getByRole("link", { name: `${invoiceNo}.pdf` })).toBeVisible();
  await expect(archive).toContainText(/Folder: Customers\/C-1002 Kingston Kitchen.*\/\d{4}\/\d{2}/);
  await expect(archive.getByText("In Drive").first()).toBeVisible({ timeout: 20_000 });

  await archive.getByLabel("Signed delivery file").setInputFiles({ name: "pod.png", mimeType: "image/png", buffer: pod });
  await archive.getByLabel("Note (optional)").fill("Signed by store manager (synthetic)");
  await archive.getByRole("button", { name: "Upload proof of delivery" }).click();
  await expect(archive.getByRole("status")).toContainText(`Proof of delivery filed as ${invoiceNo} POD.png`);
  await expect(archive.getByRole("link", { name: `${invoiceNo} POD.png` })).toBeVisible();
  await axe(page);

  // The archive page shows both, linked back to the invoice, and the bytes are the issued ones.
  await page.getByRole("link", { name: /^Documents/ }).first().click();
  await expect(page.getByRole("heading", { name: "Documents" })).toBeVisible();
  await page.getByPlaceholder("File name, invoice or shipment no.").fill(invoiceNo);
  const grid = page.getByRole("table");
  await expect(grid.getByRole("row", { name: new RegExp(`${invoiceNo}\\.pdf`) })).toContainText("Invoice");
  await expect(grid.getByRole("row", { name: new RegExp(`${invoiceNo} POD\\.png`) })).toContainText("Proof of delivery");
  await expect(grid.getByText("In Drive")).toHaveCount(2, { timeout: 20_000 });
  const href = (await grid.getByRole("link", { name: `${invoiceNo}.pdf` }).getAttribute("href")) ?? "";
  const pdf = await page.request.get(href);
  expect(pdf.status()).toBe(200);
  expect((await pdf.body()).subarray(0, 5).toString()).toBe("%PDF-");
  await page.getByPlaceholder("File name, invoice or shipment no.").fill("");
  await expect(grid.getByRole("row")).not.toHaveCount(1);
  await axe(page);
  await page.screenshot({ path: "screenshots/documents-1440.png", fullPage: true });
});

test("warehouse role has no access to the archive", async ({ page }) => {
  await signIn(page, fx.users.warehouse);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.goto("/documents");
  await expect(page.getByText("You don't have access to the document archive")).toBeVisible();
  await axe(page);
});
