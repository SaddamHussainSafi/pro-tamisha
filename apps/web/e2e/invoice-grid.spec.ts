import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// Fresh synthetic company: Island Grocers (C-1001, wholesale), CFZ 29.00/box standard and
// 1.35/lb for C-1001, plantain 0.40/piece and 0.95/lb (box = 50 lb, no box price for
// wholesale), jasmine rice 39.50/box and 2.10/kg, all with opening stock in MAIN.
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
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

const cell = (page: Page, name: string) => page.getByLabel(name, { exact: true });

test("a direct invoice typed and pasted with the keyboard only, checked and issued", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await signIn(page, fx.users.owner);
  await page.goto("/invoices/new?from=direct");
  await expect(cell(page, "Item, line 20")).toBeVisible(); // 20 rows open from the start

  // The customer is a native select (type-ahead in a real browser; headless macOS Chromium
  // ignores it, so pick by value). Enter on it jumps into the grid.
  const customer = page.getByLabel("Customer", { exact: true });
  await customer.selectOption({ label: "Island Grocers (synthetic) (C-1001)" });
  await expect(page.getByLabel("Salesperson")).toHaveValue("Sam Synthetic");
  await customer.focus();
  await page.keyboard.press("Enter");
  await expect(cell(page, "Item, line 1")).toBeFocused();

  // Item → Enter → Qty → Enter → next line; SKU, name and alias all match
  for (const [text, qty] of [["cfz", "2"], ["jasmine", "3"], ["platano", "1"]] as const) {
    await page.keyboard.type(text);
    await page.keyboard.press("Enter");
    await page.keyboard.type(qty);
    await page.keyboard.press("Enter");
  }
  await expect(cell(page, "Item, line 4")).toBeFocused();
  await expect(cell(page, "Item, line 1")).toHaveValue("Cassava frozen");
  await expect(cell(page, "Item, line 2")).toHaveValue("Jasmine rice");
  await expect(cell(page, "Item, line 3")).toHaveValue("Plantain green");
  await expect(cell(page, "Unit, line 3")).toHaveValue("BOX");

  // Arrow keys move around; a box with no box price is per lb x 50 lb (server preview)
  await page.keyboard.press("ArrowUp");
  await expect(cell(page, "Item, line 3")).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByTestId("grid-row-3")).toContainText("47.50");

  // Three more lines pasted from a spreadsheet (item, qty, unit)
  await page.evaluate(() => navigator.clipboard.writeText("CFZ\t10\tlb\r\nPlantain green\t12\tpcs\r\nRJS\t5\tkg\r\n"));
  await page.keyboard.press("ControlOrMeta+V");
  await expect(page.getByText("Pasted 3 rows.")).toBeVisible();
  await expect(cell(page, "Unit, line 4")).toHaveValue("LB");
  await expect(cell(page, "Unit, line 5")).toHaveValue("PIECE");
  await expect(cell(page, "Quantity, line 6")).toHaveValue("5");
  await expect(page.getByTestId("net-total")).toHaveText("CAD 252.80"); // provisional, from the server

  // Enter on the next empty row → Check prices and stock → hand-over → Issue, by keyboard
  await cell(page, "Item, line 7").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: "Check prices and stock" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Checked against today's prices and stock.")).toBeVisible();
  await expect(page.getByTestId("net-total")).toHaveText("CAD 252.80");
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
  await page.screenshot({ path: "screenshots/invoice-grid-1440.png", fullPage: true });

  const handed = page.getByLabel("Goods handed over to the customer");
  await expect(handed).toBeFocused();
  await page.keyboard.press("Space");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "Issue invoice" })).toBeFocused();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Issue invoice" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: /^Invoice TT\d{4}-\d{6}$/ })).toBeVisible();
  await expect(page.getByTestId("invoice-total")).toHaveText("CAD 252.80");
});

test("an edit after checking needs a new check; a chosen batch is used", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.goto("/invoices/new?from=direct");
  await page.getByLabel("Customer", { exact: true }).selectOption({ label: "Kingston Kitchen (synthetic) (C-1002)" });
  await cell(page, "Item, line 1").focus();
  await page.keyboard.type("cfz");
  await page.keyboard.press("Enter");
  await page.keyboard.type("9");
  // Batch: oldest expiry first by default (SEED-2 holds at most 8, so 9 spill into SEED-1)
  const batch = cell(page, "Batch, line 1");
  await expect(batch.locator("option").first()).toHaveText("CAS-FZ-SEED-2");
  await expect(batch.locator("option").nth(1)).toHaveText(/^CAS-FZ-SEED-2 · \d+ free · exp /);
  await batch.selectOption({ index: 1 }); // that one batch only
  await expect(page.getByRole("list", { name: "Lines to fix" })).toContainText(/Line 1: Only \d+ boxes in stock in this batch\./);
  await batch.selectOption({ index: 0 }); // back to oldest first
  await page.getByRole("button", { name: "Check prices and stock" }).click();
  await expect(batch.locator("option").first()).toHaveText("CAS-FZ-SEED-2 +1"); // the check: both batches
  await expect(page.getByRole("button", { name: "Issue invoice" })).toBeVisible();
  await cell(page, "Quantity, line 1").fill("4");
  await expect(page.getByRole("button", { name: "Issue invoice" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Check prices and stock" })).toBeVisible();
});
