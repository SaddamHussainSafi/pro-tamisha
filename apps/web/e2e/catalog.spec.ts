import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

type Fixture = { password: string; users: { owner: string; warehouse: string } };

const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

async function ownerSignIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.users.owner);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  // Two-step sign-in is optional (2026-11-09): the owner lands on Overview.
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}

test.describe.configure({ mode: "serial" });

test("owner creates Cassava (205 lb = CAD 307.50) and sees unknown-conversion rejection", async ({ page }) => {
  await ownerSignIn(page);
  await page.getByRole("link", { name: /Products/ }).click();
  await expect(page.getByRole("heading", { name: "Products" })).toBeVisible();
  await page.getByRole("button", { name: "New product" }).click();

  const form = page.getByRole("form", { name: "New product" });
  await form.getByLabel("SKU").fill("CEE");
  await form.getByLabel("Name").fill("Cassava frozen E2E");
  const sizes = ["20", "30", "40", "25"];
  for (let i = 0; i < sizes.length; i++) {
    if (i > 0) await form.getByRole("button", { name: "Add case size" }).click();
    await form.locator(`#pr-code-${i}`).fill(`CASE${sizes[i]}`);
    await form.getByLabel("Weight / unit").nth(i).fill(sizes[i] ?? "");
  }
  await form.getByLabel("Aliases (one per line)").fill("Yuca congelada E2E");
  await axe(page);
  await form.getByRole("button", { name: "Create product" }).click();

  await expect(page.getByRole("heading", { name: "Cassava frozen E2E" })).toBeVisible();
  await expect(page.getByText("Yuca congelada E2E")).toBeVisible();

  // 2×20 + 2×30 + 2×40 + 1×25 = 205 lb (INV-006)
  const calc = page.getByRole("form", { name: "Weight calculator" });
  const counts = ["2", "2", "2", "1"];
  for (let i = 0; i < counts.length; i++) {
    if (i > 0) await calc.getByRole("button", { name: "Add group" }).click();
    await calc.getByLabel("Profile").nth(i).selectOption({ label: `CASE${sizes[i]} · ${sizes[i]}.00 lb/box` });
    await calc.getByLabel("Count").nth(i).fill(counts[i] ?? "");
  }
  await calc.getByLabel("Price per lb (optional)").fill("1.50");
  await calc.getByRole("button", { name: "Calculate" }).click();
  await expect(page.getByTestId("total-lb")).toHaveText("205.00 lb");
  await expect(page.getByTestId("amount")).toContainText("307.50");
  await expect(page.getByText("Estimated — confirm on scale")).toBeVisible();
  await expect(page.getByText("7 BOX")).toBeVisible();
  await axe(page);
  await page.getByTestId("total-lb").scrollIntoViewIfNeeded();
  await page.locator("section", { has: page.getByRole("heading", { name: "Weight calculator" }) }).screenshot({ path: "screenshots/catalog-weight-1440.png" });

  // Unknown piece weight is rejected with a plain-language message.
  await page.getByRole("link", { name: "Products", exact: true }).first().click();
  await page.getByRole("button", { name: "New product" }).click();
  const form2 = page.getByRole("form", { name: "New product" });
  await form2.getByLabel("SKU").fill("PEE");
  await form2.getByLabel("Name").fill("Plantain E2E");
  await form2.locator("#pr-code-0").fill("PC");
  await form2.locator("#pr-unit-0").selectOption("PIECE");
  await form2.getByRole("button", { name: "Create product" }).click();
  await expect(page.getByRole("heading", { name: "Plantain E2E" })).toBeVisible();

  const calc2 = page.getByRole("form", { name: "Weight calculator" });
  await calc2.getByLabel("Count").fill("3");
  await calc2.getByRole("button", { name: "Calculate" }).click();
  await expect(page.getByRole("alert")).toContainText("No known weight for group 1");
  await page.screenshot({ path: "screenshots/catalog-unknown-1440.png", fullPage: true });
});

test("warehouse user can view products but not manage them", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.users.warehouse);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.getByRole("link", { name: /Products/ }).click();
  // Products open as cards (Grid view) since 2026-11-08.
  const cards = page.getByRole("list", { name: "Products" });
  await expect(cards.getByText(/^CEE/)).toBeVisible();
  await expect(page.getByRole("button", { name: "New product" })).toHaveCount(0);
  await cards.getByRole("link", { name: "Cassava frozen E2E" }).click();
  await expect(page.getByRole("button", { name: "Edit" })).toHaveCount(0);
  await axe(page);
});
