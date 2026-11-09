import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// Fresh synthetic company: seeded catalog, price book and MAIN-warehouse opening stock
// (CAS-FZ-SEED-1: 40 box + 120.5 lb, CAS-FZ-SEED-2: 8 box, PLN-GR-SEED-1, RICE-JAS-SEED-1).
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

const balance = (page: Page, unit: string) => page.getByTestId(`bal-${unit}`);

test("warehouse staff record a count but cannot change stock", async ({ page }) => {
  await signIn(page, fx.users.warehouse);
  await page.getByRole("link", { name: /Inventory/ }).click();
  await expect(page.getByRole("heading", { name: "Inventory" })).toBeVisible();
  await expect(page.getByText("Live — updates automatically")).toBeVisible();
  await expect(page.getByRole("button", { name: "Opening balance" })).toHaveCount(0);

  await page.getByRole("button", { name: "Record count" }).click();
  const form = page.getByRole("form", { name: "Record stock count" });
  await form.getByLabel("Find lot or SKU").fill("CAS-FZ-SEED-2");
  await form.getByLabel("Counted CAS-FZ-SEED-2 BOX").fill("6.5");
  await expect(form.getByText("Whole numbers only")).toBeVisible();
  await form.getByLabel("Counted CAS-FZ-SEED-2 BOX").fill("6");
  await form.getByRole("button", { name: "Submit 1 line(s)" }).click();
  await expect(page.getByRole("tab", { name: "Counts", selected: true })).toBeVisible();
  const count = page.getByRole("region", { name: /Count CNT-/ });
  await expect(count).toContainText("submitted");
  await expect(count.getByRole("button", { name: "Post corrections" })).toHaveCount(0);

  await page.getByRole("tab", { name: "Stock on hand" }).click();
  await page.getByRole("link", { name: "CAS-FZ-SEED-1" }).first().click();
  await expect(page.getByRole("heading", { name: "Lot CAS-FZ-SEED-1" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Adjust stock" })).toHaveCount(0);
  await axe(page);
});

test("owner adjusts, is stopped from overselling, records damage, reverses, posts the count", async ({ page }) => {
  await signIn(page, fx.users.owner);
  // Two-step sign-in is optional (2026-11-09): the owner lands on Overview.
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

  await page.getByRole("link", { name: /Inventory/ }).click();
  const grid = page.getByRole("table", { name: "Stock on hand by lot and unit" });
  await expect(grid.getByRole("row").filter({ hasText: "CAS-FZ-SEED-1" }).filter({ hasText: "BOX" })).toContainText("40");
  await expect(grid.getByRole("row").filter({ hasText: "CAS-FZ-SEED-1" }).filter({ hasText: "LB" })).toContainText("120.5");
  await page.getByPlaceholder("Search SKU, product or lot").fill("rice");
  await expect(grid.getByRole("row")).toHaveCount(3); // header + BOX + KG
  await page.getByPlaceholder("Search SKU, product or lot").fill("");
  await expect(page.getByText("Live — updates automatically")).toBeVisible();
  await axe(page);
  await page.screenshot({ path: "screenshots/inventory-stock-1440.png", fullPage: true });

  await grid.getByRole("link", { name: "CAS-FZ-SEED-1" }).first().click();
  await expect(balance(page, "BOX")).toContainText("40");

  // Adjust: remove 2 boxes.
  await page.getByRole("button", { name: "Adjust stock" }).click();
  let form = page.getByRole("form", { name: "Adjust stock" });
  await form.getByLabel("Unit").selectOption("BOX");
  await form.getByLabel("Quantity").fill("2");
  await form.getByLabel("Note (required)").fill("synthetic shrink");
  await form.getByRole("button", { name: "Post adjustment" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Adjustment posted" })).toBeVisible();
  await expect(balance(page, "BOX")).toContainText("38");

  // Oversell is refused with the available quantity; nothing changes.
  await page.getByRole("button", { name: "Adjust stock" }).click();
  form = page.getByRole("form", { name: "Adjust stock" });
  await form.getByLabel("Unit").selectOption("BOX");
  await form.getByLabel("Quantity").fill("100");
  await form.getByLabel("Note (required)").fill("too many");
  await form.getByRole("button", { name: "Post adjustment" }).click();
  await expect(page.getByRole("alert")).toContainText("only 38 available");
  await expect(balance(page, "BOX")).toContainText("38");
  await form.getByRole("button", { name: "Cancel" }).click();

  // Damage moves stock to the companion lot.
  await page.getByRole("button", { name: "Record damage" }).click();
  const dmg = page.getByRole("form", { name: "Record damage" });
  await dmg.getByLabel("Unit").selectOption("BOX");
  await dmg.getByLabel("Damaged quantity").fill("1");
  await dmg.getByLabel("What happened (required)").fill("crushed in transit");
  await dmg.getByRole("button", { name: "Move to damage" }).click();
  await expect(balance(page, "BOX")).toContainText("37");
  await expect(page.getByRole("link", { name: "CAS-FZ-SEED-1-DMG" })).toBeVisible();

  // Reverse the 2-box adjustment: compensating entry, original kept.
  const ledger = page.getByRole("table", { name: "Stock movements" });
  const adjRow = ledger.getByRole("row").filter({ hasText: "synthetic shrink" });
  await adjRow.getByRole("button", { name: "Reverse" }).click();
  await page.getByLabel("Reason (required)").fill("counted wrong");
  await page.getByRole("button", { name: "Post reversal" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Reversal posted" })).toBeVisible();
  await expect(balance(page, "BOX")).toContainText("39");
  await expect(adjRow).toContainText("Reversed");
  await expect(ledger.getByRole("row").filter({ hasText: "Reversal" })).toHaveCount(1);
  await axe(page);
  await page.screenshot({ path: "screenshots/inventory-lot-1440.png", fullPage: true });

  // Post the warehouse count (expected 8, counted 6).
  await page.getByRole("navigation", { name: "Breadcrumb" }).getByRole("link", { name: "Inventory" }).click();
  await page.getByRole("tab", { name: "Counts" }).click();
  const count = page.getByRole("region", { name: /Count CNT-/ });
  await count.getByRole("button", { name: /CNT-/ }).click();
  await expect(count).toContainText("−2");
  await count.getByRole("button", { name: "Post corrections" }).click();
  await expect(count).toContainText("posted");
  await page.getByRole("tab", { name: "Stock on hand" }).click();
  await expect(grid.getByRole("row").filter({ hasText: "CAS-FZ-SEED-2" })).toContainText("6");

  // Ledger and cached balances agree.
  await page.getByRole("tab", { name: "Reconciliation" }).click();
  await expect(page.getByText("all match")).toBeVisible();
  await axe(page);
});

test("sales sees availability read-only", async ({ page }) => {
  await signIn(page, fx.users.sales);
  await page.getByRole("link", { name: /Inventory/ }).click();
  await expect(page.getByRole("link", { name: "CAS-FZ-SEED-1" }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Opening balance" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Record count" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Reconciliation" })).toHaveCount(0);
  await page.getByRole("link", { name: "CAS-FZ-SEED-1" }).first().click();
  await expect(page.getByRole("button", { name: "Adjust stock" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Reverse" })).toHaveCount(0);
  await axe(page);
});
