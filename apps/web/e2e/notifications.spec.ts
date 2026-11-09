import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, request as pwRequest, test, type APIRequestContext, type Page } from "@playwright/test";
import { totp } from "./totp";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// Fresh synthetic company. Every notification below comes from a real API action in it.
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

let sales: Api;
let customerId = "";

async function signIn(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  // Two-step sign-in may or may not be asked for (it depends on the role policy).
  const secret = page.getByTestId("mfa-secret");
  const bell = page.getByTestId("notification-bell");
  await expect(secret.or(bell)).toBeVisible();
  if (await secret.isVisible()) {
    await page.getByLabel("6-digit code").fill(totp((await secret.textContent()) ?? ""));
    await page.getByRole("button", { name: /Verify/ }).click();
  }
  await expect(bell).toBeVisible();
}

async function axe(page: Page, include?: string) {
  let b = new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]);
  if (include) b = b.include(include);
  const r = await b.analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}

async function reportPayment(amount: string) {
  return post(sales, "/payments", { customer_id: customerId, method: "INTERAC", amount, received_on: new Date().toISOString().slice(0, 10), payer_ref: "e-Transfer" });
}

test.beforeAll(async () => {
  sales = await apiAs(fx.users.sales);
  const [cust] = await get(sales, "/customers?q=C-1001");
  customerId = cust.id;
  const products = await get(sales, "/products?search=CFZ");
  const [wh] = await get(sales, "/warehouses");
  // 1) Sales confirms an order -> warehouse staff and the owner hear about it.
  const o = await post(sales, "/sales-orders", {
    customer_id: customerId,
    warehouse_id: wh.id,
    source_text: "4 box cassava",
    lines: [{ product_id: products.items[0].id, unit_code: "BOX", qty: "4", source_line_text: "4 box" }],
  });
  await post(sales, `/sales-orders/${o.id}/confirm`, { version: o.version });
  // 2) Sales reports a customer payment -> the owner (payment.verify) is asked to check it.
  await reportPayment("125.00");
  // 3) Five wrong current passwords on the change-password form lock the warehouse account ->
  //    a critical security alert. (Per person; failed sign-ins would also count against the
  //    shared localhost address that every session's browser tests use.)
  const whApi = await apiAs(fx.users.warehouse);
  for (let i = 0; i < 5; i++)
    await whApi.ctx.post("/api/v1/auth/password", {
      data: { current_password: "wrong-password-1", new_password: "Another-Synthetic-Pass-9!" },
      headers: { "X-CSRF-Token": whApi.csrf },
    });
  await whApi.ctx.dispose();
});

test.afterAll(async () => {
  await sales?.ctx.dispose();
});

test("bell shows the unread count; the panel groups real events and is keyboard friendly", async ({ page }) => {
  await signIn(page, fx.users.owner);
  const bell = page.getByTestId("notification-bell");
  await expect(bell).toHaveAccessibleName(/Notifications, 3 unread, 1 critical/);
  await bell.click();
  const panel = page.getByRole("dialog", { name: "Notifications" });
  await expect(panel).toBeVisible();
  await expect(panel.getByRole("heading", { name: "Today" })).toBeVisible();
  await expect(panel.getByText(/^Order SO-.* confirmed$/)).toBeVisible();
  await expect(panel.getByText(/^Payment PAY-.* to verify$/)).toBeVisible();
  await expect(panel.getByText("Account locked: E2E warehouse")).toBeVisible();
  await expect(panel.getByText("Critical ·")).toBeVisible(); // never colour alone
  // Focus starts inside the panel; Escape closes it and gives focus back to the bell.
  await expect(panel.getByRole("button", { name: "Unread" })).toBeFocused();
  await axe(page, "[role=dialog]");
  await page.screenshot({ path: "screenshots/notifications-panel-1440.png" });
  await page.keyboard.press("Escape");
  await expect(panel).toBeHidden();
  await expect(bell).toBeFocused();
  // Enter reopens; a click outside closes.
  await page.keyboard.press("Enter");
  await expect(panel).toBeVisible();
  await page.mouse.click(300, 600);
  await expect(panel).toBeHidden();
});

test("a new event reaches the open tab live, without a reload", async ({ page }) => {
  await signIn(page, fx.users.owner);
  const bell = page.getByTestId("notification-bell");
  await expect(bell).toHaveAccessibleName(/3 unread/);
  // Wait until the live stream is connected (the Notification Center shows it).
  await page.goto("/notifications");
  await expect(page.getByText("Live", { exact: true })).toBeVisible();
  const cards = page.getByTestId("notification-card");
  await expect(cards).toHaveCount(3);
  await reportPayment("40.00");
  await expect(bell).toHaveAccessibleName(/4 unread/);
  await expect(cards).toHaveCount(4);
  await expect(page.getByTestId("notification-banner")).toContainText(/Payment PAY-.* to verify/);
});

test("read, unread, mark all, dismiss and restore keep the count in sync", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.goto("/notifications");
  const bell = page.getByTestId("notification-bell");
  await expect(bell).toHaveAccessibleName(/4 unread/);
  const first = page.getByTestId("notification-card").first();
  await first.getByRole("button", { name: /as read$/ }).click();
  await expect(bell).toHaveAccessibleName(/3 unread/);
  await first.getByRole("button", { name: /as unread$/ }).click();
  await expect(bell).toHaveAccessibleName(/4 unread/);
  // Filters + search.
  await page.getByLabel("Priority").selectOption("critical");
  await expect(page.getByTestId("notification-card")).toHaveCount(1);
  await page.getByLabel("Priority").selectOption("");
  await page.getByLabel("Category").selectOption("payment");
  await expect(page.getByTestId("notification-card")).toHaveCount(2);
  await page.getByRole("button", { name: "Clear filters" }).click();
  await page.getByLabel("Search notifications").fill("locked");
  await expect(page.getByTestId("notification-card")).toHaveCount(1);
  // Dismiss it, find it under Archived, restore it.
  await page.getByRole("button", { name: /^Dismiss “Account locked/ }).click();
  await expect(page.getByTestId("notification-card")).toHaveCount(0);
  await page.getByRole("button", { name: "Archived", exact: true }).click();
  await expect(page.getByText("Account locked: E2E warehouse")).toBeVisible();
  await page.getByRole("button", { name: /^Restore/ }).click();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(page.getByTestId("notification-card")).toHaveCount(4);
  await axe(page);
  await page.screenshot({ path: "screenshots/notifications-center-1440.png", fullPage: true });
  await page.getByRole("button", { name: "Mark all as read" }).click();
  await expect(bell).toHaveAccessibleName("Notifications");
  // Opening a notification goes to its record.
  await page.getByRole("link", { name: /^Order SO-.* confirmed$/ }).click();
  await expect(page).toHaveURL(/\/orders\/[0-9a-f-]{36}$/);
});

// Headless Chromium can't grant real notification permission, so the Web Notifications API is
// replaced by a recording stand-in that behaves like a browser (default -> asked -> granted).
async function fakeNotifications(page: Page, initial: NotificationPermission) {
  await page.addInitScript((start) => {
    const w = window as unknown as { __shown: unknown[]; __asked: number };
    w.__shown = [];
    w.__asked = 0;
    const key = "fake-notification-permission";
    class FakeNotification {
      static get permission() {
        return (sessionStorage.getItem(key) as NotificationPermission | null) ?? start;
      }
      static async requestPermission() {
        w.__asked += 1;
        sessionStorage.setItem(key, "granted");
        return "granted";
      }
      onclick: (() => void) | null = null;
      constructor(title: string, opts?: NotificationOptions) {
        w.__shown.push({ title, body: opts?.body, tag: opts?.tag });
      }
      close() {}
    }
    Object.defineProperty(window, "Notification", { value: FakeNotification, configurable: true });
  }, initial);
}

test("settings: preferences and quiet hours save", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.goto("/settings/notifications");
  await expect(page.getByRole("heading", { name: "How you're notified" })).toBeVisible();
  await page.getByRole("switch", { name: "Quiet hours" }).click();
  await page.getByLabel("From").fill("21:00");
  await page.getByRole("checkbox", { name: "Vendor receiving: in-app" }).uncheck();
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Notification settings saved.")).toBeVisible();
  await page.reload();
  await expect(page.getByRole("switch", { name: "Quiet hours" })).toHaveAttribute("aria-checked", "true");
  await expect(page.getByLabel("From")).toHaveValue("21:00");
  await expect(page.getByRole("checkbox", { name: "Vendor receiving: in-app" })).not.toBeChecked();
  // The owner may mute security (an ordinary user can't; see the API tests).
  await expect(page.getByRole("checkbox", { name: "Account & security: in-app" })).toBeEnabled();
  await axe(page);
  // Back to no quiet hours, so the next test's alert isn't held back.
  await page.getByRole("switch", { name: "Quiet hours" }).click();
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByText("Notification settings saved.")).toBeVisible();
});

test("desktop notifications: blocked stays off; asked only on request; then shown in the background", async ({ page, browser }) => {
  // Blocked in the browser: explained, and the switch can't be turned on.
  const blocked = await browser.newPage();
  await fakeNotifications(blocked, "denied");
  await signIn(blocked, fx.users.owner);
  await blocked.goto("/settings/notifications");
  await expect(blocked.getByText(/Blocked in your browser/)).toBeVisible();
  await expect(blocked.getByRole("switch", { name: "Desktop notifications" })).toBeDisabled();
  await blocked.close();

  await fakeNotifications(page, "default");
  await signIn(page, fx.users.owner);
  await page.goto("/settings/notifications");
  const allow = page.getByRole("button", { name: "Allow…" });
  await expect(allow).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { __asked: number }).__asked)).toBe(0); // never on load
  await allow.click();
  await expect(page.getByText("Notification settings saved.")).toBeVisible();
  const desk = page.getByRole("switch", { name: "Desktop notifications" });
  await expect(desk).toHaveAttribute("aria-checked", "true");
  await page.getByRole("button", { name: "Test" }).click();
  await page.screenshot({ path: "screenshots/notification-settings-1440.png", fullPage: true });
  // The app goes to the background; a new real event becomes a desktop notification.
  await page.evaluate(() => Object.defineProperty(document, "visibilityState", { get: () => "hidden", configurable: true }));
  await reportPayment("15.00");
  await expect
    .poll(() => page.evaluate(() => (window as unknown as { __shown: { title: string }[] }).__shown.map((n) => n.title)))
    .toEqual(["Pro Trading", expect.stringMatching(/^Payment PAY-.* to verify$/)]);
});

test("phone width: the panel is a full-width sheet and nothing scrolls sideways", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, fx.users.owner);
  await page.getByTestId("notification-bell").click();
  const panel = page.getByRole("dialog", { name: "Notifications" });
  await expect(panel).toBeVisible();
  const box = await panel.boundingBox();
  expect(box?.width).toBeGreaterThan(360);
  await expect(panel.getByTestId("notification-card").first()).toBeVisible();
  await page.screenshot({ path: "screenshots/notifications-panel-390.png" });
  await panel.getByRole("button", { name: "Close notifications" }).click();
  await expect(panel).toBeHidden();
  await page.goto("/notifications");
  await expect(page.getByRole("heading", { name: "Notifications", level: 1 })).toBeVisible();
  await expect(page.getByTestId("notification-card").first()).toBeVisible();
  const sideways = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth || (document.getElementById("main")?.scrollWidth ?? 0) > window.innerWidth);
  expect(sideways).toBe(false);
  await page.screenshot({ path: "screenshots/notifications-center-390.png", fullPage: true });
});
