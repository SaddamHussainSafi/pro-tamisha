import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// Fresh synthetic company with catalog, prices (C-1001 CFZ BOX → 29.0000 standard) and opening stock.
const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

test.describe.configure({ mode: "serial" });

let orderUrl = "";
let pickCode = "";

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

async function optionValue(page: Page, select: string, text: string) {
  const opt = page.locator(`${select} option`, { hasText: text }).first();
  await opt.waitFor({ state: "attached" });
  return (await opt.getAttribute("value")) ?? "";
}

test("sales captures 15 boxes, confirms (price frozen) and prints a packing sheet", async ({ page }) => {
  await signIn(page, fx.users.sales);
  await page.getByRole("link", { name: /Orders/ }).first().click();
  await expect(page.getByRole("heading", { name: "Sales orders" })).toBeVisible();
  await page.getByRole("link", { name: "New order" }).click();

  const form = page.getByRole("form", { name: "New sales order" });
  await form.getByLabel("Customer", { exact: true }).selectOption(await optionValue(page, "#so-cust", "Island Grocers"));
  await form.getByLabel(/Original request/).fill("15 box cassava please");
  // The entry grid: type the SKU, Enter picks it and moves to the quantity.
  await form.getByLabel("Item, line 1", { exact: true }).fill("CFZ");
  await form.getByLabel("Item, line 1", { exact: true }).press("Enter");
  await expect(form.getByLabel("Quantity, line 1", { exact: true })).toBeFocused();
  await form.getByLabel("Unit, line 1", { exact: true }).selectOption("BOX");
  await form.getByLabel("Quantity, line 1", { exact: true }).fill("15.5");
  await expect(form.getByRole("list", { name: "Lines to fix" })).toContainText("Line 1: Boxes and pieces are whole numbers.");
  await form.getByLabel("Quantity, line 1", { exact: true }).fill("15");
  await form.getByLabel("As written, line 1", { exact: true }).fill("15 box cassava");
  await expect(page.getByTestId("net-total")).toHaveText("CAD 435.00"); // today's 29.00/box, an estimate
  await form.getByRole("button", { name: "Save draft" }).click();

  await expect(page.getByRole("heading", { name: /^SO-/ })).toBeVisible();
  orderUrl = new URL(page.url()).pathname;
  // the draft opens as the same form (2026-11-11); Confirm order fixes today's prices
  await expect(page.getByRole("form", { name: /^Order SO-/ })).toBeVisible();
  await expect(page.getByTestId("net-total")).toHaveText("CAD 435.00");
  await page.getByRole("form", { name: /^Order SO-/ }).getByRole("button", { name: "Confirm order" }).click(); // also offered in the page header
  await expect(page.getByText("Confirmed", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("table", { name: "Order lines" })).toContainText("29.00");
  await expect(page.getByTestId("pending-CFZ")).toHaveText("15");
  await axe(page);
  await page.screenshot({ path: "screenshots/orders-detail-1440.png", fullPage: true });

  await page.getByRole("button", { name: "Print packing sheet" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Create sheet" }).click();
  await expect(page.getByRole("heading", { name: /^Packing sheet SO-/ })).toBeVisible();
  pickCode = ((await page.getByRole("heading", { name: /^Packing sheet/ }).textContent()) ?? "").replace("Packing sheet ", "");
  await expect(page.getByRole("img", { name: `QR code ${pickCode}` })).toBeVisible();
  // Sales cannot record quantities or verify.
  await expect(page.getByRole("button", { name: "Save quantities" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Verify packing" })).toHaveCount(0);
});

test("warehouse packs 10 of 15, records why, and submits", async ({ page }) => {
  await signIn(page, fx.users.warehouse);
  await page.getByRole("link", { name: /Packing/ }).first().click();
  await page.getByPlaceholder("Sheet code (scan QR)").fill(pickCode);
  await page.getByRole("link", { name: pickCode }).click();

  const row = page.getByTestId("pick-CFZ");
  await row.getByLabel("Packed CFZ").fill("10");
  await expect(row.getByText("Short", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Save quantities" }).click();
  await expect(page.getByRole("alert")).toContainText("say why less was packed");
  await row.getByLabel("Why short?").selectOption("OUT_OF_STOCK");
  await page.getByRole("button", { name: "Save quantities" }).click();
  await expect(page.getByText("Quantities saved.")).toBeVisible();
  await axe(page);
  await page.screenshot({ path: "screenshots/packing-sheet-1440.png", fullPage: true });
  await page.getByRole("button", { name: "Submit for check" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Submit" }).click();
  await expect(page.getByText("Awaiting check").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Verify packing" })).toHaveCount(0);

  // Warehouse cannot change the order.
  await page.goto(orderUrl);
  await expect(page.getByRole("button", { name: "Close with shortage" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Print packing sheet" })).toHaveCount(0);
});

test("owner verifies: 5 stays pending as backorder, then closes the shortfall", async ({ page }) => {
  await signIn(page, fx.users.owner);
  // Two-step sign-in is optional (2026-11-09): the owner lands on Overview.
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

  await page.goto(orderUrl);
  await page.getByRole("link", { name: "Open packing sheet" }).click();
  await expect(page.getByLabel("Shortfall for CFZ")).toHaveValue("BACKORDER");
  await page.getByRole("button", { name: "Verify packing" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Verify packing" }).click();
  await expect(page.getByText("Packing verified.")).toBeVisible();

  await page.getByRole("link", { name: /^SO-/ }).click();
  await expect(page.getByTestId("pending-CFZ")).toContainText("5");
  await expect(page.getByText(/Backorder:/)).toBeVisible();
  await expect(page.getByText("Partly supplied").first()).toBeVisible();

  await page.goto("/orders");
  await page.getByRole("button", { name: "Backorders" }).click();
  await expect(page.getByRole("table", { name: /Sales orders/ })).toContainText("Island Grocers");
  await axe(page);
  await page.screenshot({ path: "screenshots/orders-list-1440.png", fullPage: true });

  // The 10 picked boxes are awaiting invoice, so closing is refused until invoiced (Module 05).
  await page.goto(orderUrl);
  await expect(page.getByRole("button", { name: "Close with shortage" })).toHaveCount(0);
  await page.getByRole("button", { name: "Cancel qty" }).click();
  const d = page.getByRole("dialog");
  await d.getByLabel("Quantity to cancel").fill("5");
  await d.getByLabel("Reason (required)").fill("customer no longer needs it");
  await d.getByRole("button", { name: "Cancel quantity" }).click();
  await expect(page.getByTestId("pending-CFZ")).toHaveText("0");
  await expect(page.getByText("Quantity cancelled")).toBeVisible();
  await axe(page);
});
