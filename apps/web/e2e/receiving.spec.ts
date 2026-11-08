import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Locator, type Page } from "@playwright/test";
import { totp } from "./totp";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// Fresh synthetic company with seeded vendors (V-ACC Accra Roots Exports …) and catalog.
const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

// A synthetic one-page "vendor invoice" image.
const invoice = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64",
);

test.describe.configure({ mode: "serial" });
let shipmentUrl = "";

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

/** Select the first option whose text contains `text`. */
async function pick(select: Locator, text: string) {
  await expect(select.locator("option", { hasText: text }).first()).toBeAttached();
  const value = await select.locator("option", { hasText: text }).first().getAttribute("value");
  await select.selectOption(value ?? "");
}

test("warehouse counts a shipment: 25 declared, 23 good + 2 damaged, sent for approval", async ({ page }) => {
  await signIn(page, fx.users.warehouse);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.getByRole("link", { name: /^Receiving/ }).first().click();
  await expect(page.getByRole("heading", { name: "Receiving" })).toBeVisible();

  await page.getByRole("button", { name: "New shipment" }).click();
  const form = page.getByRole("form", { name: "New shipment" });
  await pick(form.getByLabel("Supplier", { exact: true }), "V-ACC");
  await form.getByLabel("Supplier invoice no. (optional)").fill("ARX-5521");
  await form.getByLabel("Airway bill no. (optional)").fill("176-55512345");
  await form.getByRole("button", { name: "Create shipment" }).click();
  await expect(page.getByRole("heading", { name: /^Receive SHP-/ })).toBeVisible();
  shipmentUrl = page.url();

  // Vendor invoice, kept unaltered.
  const add = page.getByRole("group", { name: "Add a document" });
  await add.getByLabel("Document type").selectOption("INVOICE");
  await add.getByLabel("Number on the document (optional)").fill("ARX-5521");
  await page.getByTestId("doc-upload").setInputFiles({ name: "vendor-invoice.png", mimeType: "image/png", buffer: invoice });
  await expect(page.getByRole("status").filter({ hasText: "Supplier invoice added" })).toBeVisible();
  await expect(page.getByRole("img", { name: /^Original Supplier invoice/ })).toBeVisible();

  // What the invoice says — the vendor's own wording maps through the remembered alias.
  await page.getByRole("button", { name: "Add document line" }).click();
  const dl = page.getByRole("form", { name: "Add document line" });
  await dl.getByLabel("Description as printed").fill("MANIOC FROZEN 40LB CTN");
  await dl.getByLabel("Unit", { exact: true }).selectOption("BOX");
  await dl.getByLabel("Quantity on document").fill("25");
  await dl.getByLabel(/Unit cost/).fill("18.50");
  await dl.getByRole("button", { name: "Add line" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Document line added" })).toBeVisible();
  await expect(page.getByText("“MANIOC FROZEN 40LB CTN”")).toBeVisible();

  // What physically arrived.
  const cl = page.getByRole("form", { name: "Add count line" });
  await pick(cl.getByLabel("Product", { exact: true }), "CAS-FZ");
  await cl.getByLabel("Unit", { exact: true }).selectOption("BOX");
  await cl.getByLabel("Accepted (good)").fill("23");
  await cl.getByLabel("Damaged", { exact: true }).fill("2");
  await cl.getByLabel(/^Expiry date/).fill("2027-06-30");
  await cl.getByLabel("Batch (optional)").fill("ACC-2611");
  await cl.getByRole("button", { name: "Save count line" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Count line saved" })).toBeVisible();

  const cmp = page.getByRole("region", { name: "Documents vs. what arrived" });
  await expect(cmp.getByRole("row").filter({ hasText: "CAS-FZ" })).toContainText("Damaged");
  await axe(page);
  await page.screenshot({ path: "screenshots/receiving-count-1440.png", fullPage: true });

  // Warehouse can't approve; it sends the count on.
  await expect(page.getByRole("button", { name: "Approve & post stock" })).toHaveCount(0);
  await page.getByRole("button", { name: "Send count for approval" }).click();
  await expect(page.getByText("Waiting for approval").first()).toBeVisible();
  await expect(page.getByRole("form", { name: "Add count line" })).toHaveCount(0);
});

test("manager approves: only the 23 good boxes become saleable, and posting twice is impossible", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.getByRole("button", { name: "Set up authenticator" }).click();
  const secret = (await page.getByTestId("mfa-secret").textContent()) ?? "";
  await page.getByLabel("6-digit code").fill(totp(secret));
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

  await page.getByRole("link", { name: /^Receiving/ }).first().click();
  const row = page.getByRole("row").filter({ hasText: "Waiting for approval" }).first();
  await expect(row).toBeVisible();
  await axe(page);
  await page.screenshot({ path: "screenshots/receiving-list-1440.png", fullPage: true });
  await row.getByRole("link").first().click();
  await expect(page).toHaveURL(shipmentUrl);

  await page.getByRole("button", { name: "Approve & post stock" }).click();
  const dlg = page.getByRole("dialog", { name: "Approve and post stock?" });
  await expect(dlg).toContainText("23 box");
  // Differences must be acknowledged with a note.
  await dlg.getByRole("button", { name: "Approve & post" }).click();
  await expect(dlg.getByRole("alert")).toBeVisible();
  await dlg.getByLabel(/I checked the differences/).check();
  await dlg.getByLabel("What happened? (required)").fill("2 cartons crushed in transit; photos sent to vendor.");
  await axe(page);
  await dlg.getByRole("button", { name: "Approve & post" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Approved." })).toBeVisible();
  await expect(page.getByText(/GRN-\d{8}-\d{4} posted/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve & post stock" })).toHaveCount(0);
  await page.screenshot({ path: "screenshots/receiving-posted-1440.png", fullPage: true });

  // Stock: CAS-FZ now has the 23 saleable boxes plus a separate damaged lot of 2.
  await page.goto("/inventory");
  await page.getByRole("searchbox").first().fill("CAS-FZ");
  await expect(page.getByRole("row").filter({ hasText: /-DMG/ }).first()).toBeVisible();
});

test("suppliers list is managed by the owner", async ({ page }) => {
  await signIn(page, fx.users.warehouse);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.getByRole("link", { name: "Suppliers", exact: true }).click();
  await expect(page.getByRole("table", { name: "Suppliers" })).toContainText("V-ACC");
  await expect(page.getByRole("form", { name: "Add a supplier" })).toHaveCount(0);
  await axe(page);
  await page.screenshot({ path: "screenshots/suppliers-1440.png", fullPage: true });
});
