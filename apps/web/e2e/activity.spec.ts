import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, request as pwRequest, test, type APIRequestContext, type Page } from "@playwright/test";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// Fresh synthetic company. Every activity row below comes from a real API action in it.
const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);
const BASE = "http://localhost:5173";

test.describe.configure({ mode: "serial" });

type Api = { ctx: APIRequestContext; csrf: string };
async function apiAs(email: string): Promise<Api> {
  const ctx = await pwRequest.newContext({ baseURL: BASE, extraHTTPHeaders: { Origin: BASE } });
  const r = await ctx.post("/api/v1/auth/login", { data: { email, password: fx.password } });
  expect(r.status()).toBe(200);
  return { ctx, csrf: (await r.json()).csrf_token };
}
async function post(a: Api, path: string, data: unknown) {
  const r = await a.ctx.post(`/api/v1${path}`, { data, headers: { "X-CSRF-Token": a.csrf, "Idempotency-Key": crypto.randomUUID() } });
  expect(r.ok(), `${path}: ${await r.text()}`).toBeTruthy();
  return r.json();
}
async function get(a: Api, path: string) {
  const r = await a.ctx.get(`/api/v1${path}`);
  expect(r.ok()).toBeTruthy();
  return r.json();
}

async function signIn(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByTestId("notification-bell")).toBeVisible();
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}

let orderNo = "";

test.beforeAll(async () => {
  const sales = await apiAs(fx.users.sales);
  const [cust] = await get(sales, "/customers?q=C-1001");
  const products = await get(sales, "/products?search=CFZ");
  const [wh] = await get(sales, "/warehouses");
  const o = await post(sales, "/sales-orders", {
    customer_id: cust.id,
    warehouse_id: wh.id,
    source_text: "3 box cassava",
    lines: [{ product_id: products.items[0].id, unit_code: "BOX", qty: "3", source_line_text: "3 box" }],
  });
  orderNo = o.order_no;
  await post(sales, `/sales-orders/${o.id}/confirm`, { version: o.version });
  await sales.ctx.dispose();
  const owner = await apiAs(fx.users.owner);
  await post(owner, "/customers", { external_ref: "C-ACT-1", name: "Activity Test Market" });
  await owner.ctx.dispose();
});

test("sign-in page: photo panel, reset link, show password", async ({ page }) => {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  await expect(page.getByText("Sign in to your Tamisha Trading account")).toBeVisible();
  await expect(page.getByText("Wholesale Produce")).toBeVisible();
  const pw = page.getByLabel("Password", { exact: true });
  await pw.fill("secret-words");
  await expect(pw).toHaveAttribute("type", "password");
  await page.getByRole("button", { name: "Show the typed characters" }).click();
  await expect(pw).toHaveAttribute("type", "text");
  await page.getByRole("button", { name: "Hide the typed characters" }).click();
  await expect(pw).toHaveAttribute("type", "password");
  await axe(page);
  await page.screenshot({ path: "screenshots/sign-in-1440.png" });
  await page.getByRole("link", { name: "Forgot password? Reset it" }).click();
  await expect(page).toHaveURL(/\/forgot-password$/);
  await expect(page.getByRole("heading", { name: "Forgot your password?" })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
  await page.screenshot({ path: "screenshots/sign-in-390.png", fullPage: true });
});

test("activity timeline: everyone's actions, my own only, and one person", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.getByRole("button", { name: "Settings menu" }).click();
  await page.getByRole("link", { name: "Activity Log" }).click();
  await expect(page).toHaveURL(/\/activity$/);
  const rows = page.getByTestId("activity-row");
  await expect(rows.filter({ hasText: `E2E sales created an order — ${orderNo}` })).toHaveCount(1);
  await expect(rows.filter({ hasText: `E2E sales confirmed an order — ${orderNo}` })).toHaveCount(1);
  await expect(rows.filter({ hasText: "E2E owner added a customer — Activity Test Market" })).toHaveCount(1);
  await expect(page.getByRole("heading", { name: /^\d+ Activit(y|ies)$/ })).toBeVisible();
  await expect(rows.first().getByText("Customer Added")).toBeVisible();
  await axe(page);
  await page.screenshot({ path: "screenshots/activity-1440.png", fullPage: true });

  // My own only.
  await page.getByRole("switch", { name: "Show my activities only" }).click();
  await expect(rows.filter({ hasText: "E2E sales" })).toHaveCount(0);
  await expect(rows.filter({ hasText: "E2E owner added a customer" })).toHaveCount(1);
  await page.getByRole("switch", { name: "Show my activities only" }).click();
  await expect(rows.filter({ hasText: "E2E sales" })).toHaveCount(2);

  // "…" menu: open the record (keyboard), or only one person's activity.
  const orderRow = rows.filter({ hasText: `confirmed an order — ${orderNo}` });
  await orderRow.getByRole("button", { name: /^More for:/ }).click();
  const menu = page.getByRole("menu");
  await expect(menu.getByRole("menuitem").first()).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(menu).toBeHidden();
  await orderRow.getByRole("button", { name: /^More for:/ }).click();
  await page.getByRole("menuitem", { name: /^Only E2E's activity$/ }).click();
  await expect(page.getByRole("button", { name: "Stop showing only E2E sales" })).toBeVisible(); // the filter chip
  await expect(rows.filter({ hasText: "E2E owner" })).toHaveCount(0);
  await page.getByRole("button", { name: "Stop showing only E2E sales" }).click();
  await orderRow.getByRole("link", { name: orderNo }).click();
  await expect(page).toHaveURL(/\/orders\/[0-9a-f-]{36}$/);
});

test("people without audit access see only their own activity; notifications tab", async ({ page }) => {
  await signIn(page, fx.users.sales);
  await page.goto("/activity");
  await expect(page.getByText("Showing your own activity")).toBeVisible();
  await expect(page.getByRole("switch", { name: "Show my activities only" })).toHaveCount(0);
  const rows = page.getByTestId("activity-row");
  await expect(rows.filter({ hasText: "E2E sales created an order" })).toHaveCount(1);
  await expect(rows.filter({ hasText: "E2E owner" })).toHaveCount(0);
  // The old Notification Center address opens the Notifications tab.
  await page.goto("/notifications");
  await expect(page).toHaveURL(/\/activity\?view=notifications/);
  await expect(page.getByRole("link", { name: /Notifications/, exact: false }).first()).toHaveAttribute("aria-current", "page");
  await expect(page.getByRole("button", { name: "Mark all as read" })).toBeVisible();
});

test("phone width: timeline fits without sideways scroll", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, fx.users.owner);
  await page.goto("/activity");
  await expect(page.getByTestId("activity-row").first()).toBeVisible();
  const sideways = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth || (document.getElementById("main")?.scrollWidth ?? 0) > window.innerWidth);
  expect(sideways).toBe(false);
  await page.screenshot({ path: "screenshots/activity-390.png", fullPage: true });
});
