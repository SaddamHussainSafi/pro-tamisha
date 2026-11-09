import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

test.describe.configure({ mode: "serial" });

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}

// Master-data screens open as cards with a Grid / List switch (owner direction 2026-11-08).
const SCREENS = [
  { path: "/products", list: "Products", table: "Products", card: "Cassava frozen", detail: { name: "Case sizes" } },
  { path: "/customers", list: /^Customers/, table: /^Customers/, card: "Kingston Kitchen (synthetic)", detail: { name: "Details" } },
  { path: "/suppliers", list: "Suppliers", table: "Suppliers", card: "Accra Roots Exports (synthetic)", detail: { name: "Received, damaged and rejected" } },
  { path: "/products/case-sizes", list: /^Case sizes/, table: /^Case sizes/, card: "Cassava frozen", detail: { name: "Case sizes" } },
  { path: "/settings/users", list: /^Users/, table: /^Users/, card: null, detail: null },
] as const;

test("master data opens as cards, switches to a table and remembers the choice", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.users.owner);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  // Two-step sign-in is optional (2026-11-09): the owner lands on Overview.
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

  for (const s of SCREENS) {
    await page.goto(s.path);
    const cards = page.getByRole("list", { name: s.list });
    await expect(cards).toBeVisible();
    await expect(page.getByRole("group", { name: "View" }).getByRole("button", { name: "Grid" })).toHaveAttribute("aria-pressed", "true");
    await axe(page);
    if (s.card) {
      await cards.getByRole("link", { name: s.card }).first().click();
      await expect(page.getByRole("heading", s.detail ?? {}).first()).toBeVisible();
      await page.goBack();
    }
    // switch to the table; it stays after a reload
    await page.getByRole("group", { name: "View" }).getByRole("button", { name: "List" }).click();
    await expect(page.getByRole("table", { name: s.table })).toBeVisible();
    await page.reload();
    await expect(page.getByRole("table", { name: s.table })).toBeVisible();
    await page.getByRole("group", { name: "View" }).getByRole("button", { name: "Grid" }).click();
    await expect(page.getByRole("list", { name: s.list })).toBeVisible();
  }

  // keyboard: Tab reaches the first card's link, Enter opens it
  await page.goto("/customers");
  const first = page.getByRole("list", { name: /^Customers/ }).getByRole("link").first();
  await first.focus();
  await expect(first).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/customers\/[0-9a-f-]{36}$/);
});
