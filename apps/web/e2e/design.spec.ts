import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Task 00b: mock screens at the four reference viewports + WCAG AA checks.
type Fixture = { password: string; users: { owner: string; warehouse: string } };
const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

const VIEWPORTS = [
  { width: 1440, height: 900 },
  { width: 1366, height: 768 },
  { width: 1024, height: 768 },
  { width: 390, height: 844 },
];
const SCREENS = [
  { path: "/preview/overview", heading: "Overview" },
  { path: "/preview/orders", heading: "Orders" },
  { path: "/preview/invoice", heading: "Invoice INV-DEMO-DRAFT" },
  { path: "/preview/receiving", heading: /^Receive SHP-DEMO/ },
];

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.users.warehouse);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

for (const vp of VIEWPORTS) {
  test(`preview screens @ ${vp.width}x${vp.height}`, async ({ page }) => {
    await page.setViewportSize(vp);
    await signIn(page);
    for (const s of SCREENS) {
      await page.goto(s.path);
      await expect(page.getByRole("heading", { level: 1, name: s.heading })).toBeVisible();
      await expect(page.getByRole("note")).toContainText("sample data");
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      expect(overflow, `${s.path} horizontal page overflow`).toBeLessThanOrEqual(0);
      await page.screenshot({ path: `screenshots/${s.path.split("/").pop()}-${vp.width}.png`, fullPage: true });
    }
  });
}

test("preview screens pass axe WCAG 2.2 AA", async ({ page }) => {
  await signIn(page);
  for (const s of SCREENS) {
    await page.goto(s.path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
    expect(r.violations.flatMap((v) => v.nodes.map((n) => `${s.path}: ${v.id} ${n.target.join(" ")} ${n.failureSummary ?? ""}`))).toEqual([]);
  }
});

test("invoice line math is exact and removal needs confirmation", async ({ page }) => {
  await signIn(page);
  await page.goto("/preview/invoice");
  const weight = page.getByLabel("Line 1 weight");
  await weight.fill("41.35"); // 41.35 × 1.15 = 47.5525 → 47.55
  await expect(page.getByRole("row", { name: /Line 1 · box/ })).toContainText("CAD 47.55");
  await page.getByRole("button", { name: "Remove line 1" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeHidden();
  await expect(page.getByRole("button", { name: "Finalize & issue" })).toBeDisabled();
});

test("orders grid: filter, keyboard open", async ({ page }) => {
  await signIn(page);
  await page.goto("/preview/orders");
  await page.getByRole("group", { name: "Status filter" }).getByRole("button", { name: /^Backorder/ }).click();
  const rows = page.getByRole("row");
  await expect(rows.nth(1)).toContainText("Backorder");
  await rows.nth(1).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("complementary", { name: /^Order SO-DEMO/ })).toBeVisible();
});
