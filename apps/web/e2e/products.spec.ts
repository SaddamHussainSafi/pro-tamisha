import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { totp } from "./totp";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// Fresh company; CFZ (cassava, frozen) costs 29.00/box and 1.50/lb standard in the seeded book.
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

test("owner adds a 35 lb case size and changes a price from the product page", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.getByRole("button", { name: "Set up authenticator" }).click();
  const secret = (await page.getByTestId("mfa-secret").textContent()) ?? "";
  await page.getByLabel("6-digit code").fill(totp(secret));
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

  // Products › Case sizes
  await page.getByRole("button", { name: "Products sections" }).click();
  await page.getByRole("link", { name: "Case sizes", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Case sizes" })).toBeVisible();
  await page.getByRole("group", { name: "View" }).getByRole("button", { name: "List" }).click(); // table assertions below
  const grid = page.getByRole("table", { name: /^Case sizes/ });
  await expect(grid.getByRole("row", { name: /CFZ/ }).first()).toBeVisible();
  await page.getByRole("button", { name: "New case size" }).click();
  await page.getByLabel("Product", { exact: true }).selectOption({ label: "Cassava frozen (CFZ)" });
  await page.getByLabel("Weight of one box").fill("35");
  await expect(page.getByLabel("Size name")).toHaveValue("35LB");
  await expect(page.getByText("Preview: 35LB · 35 lb a box")).toBeVisible();
  await page.getByRole("button", { name: "Add case size" }).click();
  await expect(page.getByRole("status")).toContainText("Added 35LB to");
  const row = grid.getByRole("row", { name: /CFZ.*35LB/ });
  await expect(row).toBeVisible();
  await axe(page);

  // Make it the default, then open the product page.
  await row.getByRole("button", { name: /^Make 35LB the default/ }).click();
  await expect(page.getByRole("status")).toContainText("35LB is now the default size");
  await expect(row.getByText("Default")).toBeVisible();
  await row.getByRole("link").click();
  await expect(page.getByRole("heading", { name: "Case sizes" })).toBeVisible();

  // Prices on the product page: change the standard box price through a draft, then activate.
  const prices = page.getByRole("region", { name: "Prices" });
  await expect(prices.getByRole("row", { name: /Standard per box CAD 29/ })).toBeVisible();
  await prices.getByRole("button", { name: "Change price" }).click();
  await prices.getByLabel("Unit").selectOption({ label: "per box" });
  await prices.getByLabel("New price").fill("31.50");
  await prices.getByRole("button", { name: "Save to draft" }).click();
  await expect(prices.getByRole("status")).toContainText(/Saved in draft PB-\d{8}-\d{4}/);
  await expect(prices.getByText(/now CAD 29/)).toBeVisible();
  await expect(prices.getByRole("row", { name: /Standard per box CAD 29/ })).toBeVisible(); // not in force yet
  await axe(page);
  await prices.getByRole("button", { name: "Activate draft now" }).click();
  await expect(prices.getByRole("status")).toContainText("is now the price book in force");
  await expect(prices.getByRole("row", { name: /Standard per box CAD 31.5/ })).toBeVisible();
});

test("sales sees prices and case sizes but cannot change them", async ({ page }) => {
  await signIn(page, fx.users.sales);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.goto("/products/case-sizes");
  await expect(page.getByRole("heading", { level: 1, name: "Case sizes" })).toBeVisible();
  await expect(page.getByRole("button", { name: "New case size" })).toHaveCount(0);
  await page.getByRole("list", { name: /^Case sizes/ }).getByRole("link", { name: "Cassava frozen" }).click(); // product card
  const prices = page.getByRole("region", { name: "Prices" });
  await expect(prices.getByRole("row", { name: /per lb/ }).first()).toBeVisible();
  await expect(prices.getByRole("button", { name: "Change price" })).toHaveCount(0);
  await axe(page);
});
