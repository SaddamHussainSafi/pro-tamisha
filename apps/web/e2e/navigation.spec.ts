import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect, test, type Browser, type Page } from "@playwright/test";

// Keyboard and small-screen navigation from the UX pass (2026-11-09).
type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

async function signIn(page: Page, email = fx.users.warehouse) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

const sidebar = (page: Page) => page.getByRole("complementary", { name: "Sections" });
const crumbs = (page: Page) => page.getByRole("navigation", { name: "Breadcrumb" });
/** The one highlighted destination in the sidebar. */
const current = (page: Page) => sidebar(page).locator("[aria-current=page]");

test("Ctrl K opens search (screens included), and / jumps to the screen's own search", async ({ page }) => {
  await signIn(page);
  await page.keyboard.press("ControlOrMeta+k");
  const go = page.getByRole("dialog", { name: "Search" }).getByRole("combobox");
  await expect(go).toBeFocused();
  await go.fill("pack");
  await expect(page.getByRole("group", { name: "Screens" }).getByRole("option", { name: /Packing/ })).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/packing$/);
  await expect(page.getByRole("heading", { name: "Packing" })).toBeVisible();

  await page.locator("body").click();
  await page.keyboard.press("/");
  await expect(page.getByPlaceholder("Sheet code (scan QR)")).toBeFocused();
});

test("at 390 px nothing scrolls sideways and the menu takes focus", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);
  for (const path of ["/", "/orders", "/packing", "/inventory", "/receiving", "/scan", "/products", "/customers", "/suppliers", "/settings"]) {
    await page.goto(path);
    await expect(page.getByRole("status", { name: "Loading" })).toHaveCount(0);
    const extra = await page.evaluate(() => {
      const main = document.getElementById("main");
      const doc = document.scrollingElement as HTMLElement;
      return Math.max(doc.scrollWidth - doc.clientWidth, main ? main.scrollWidth - main.clientWidth : 0);
    });
    expect(extra, `${path} scrolls sideways`).toBeLessThanOrEqual(1);
  }

  const open = page.getByRole("button", { name: "Open navigation" });
  await open.click();
  await expect(page.getByRole("button", { name: "Close navigation" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(open).toBeFocused();
});

test("signing in returns to the page that was opened", async ({ page }) => {
  await page.goto("/inventory?tab=movements");
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("Email").fill(fx.users.warehouse);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/inventory\?tab=movements$/);
  await expect(page.getByRole("heading", { name: "Inventory" })).toBeVisible();
});

test("sidebar auto-hides to an icon rail and slides open, pushing the page, when the pointer is on it", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page);
  const nav = sidebar(page);
  // the sidebar's own width: 88 px rail, 276 px open; it pushes the page (never covers it)
  const width = async () => Math.round((await nav.boundingBox())?.width ?? 0);
  const pageLeft = async () => Math.round((await page.locator("#main").boundingBox())?.x ?? 0);
  await expect(nav.getByRole("link", { name: "Packing", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Auto-hide sidebar" }).click();
  await page.mouse.move(800, 400);
  // not fully hidden: the icons stay (larger), the pin arrow and the entries inside sections do not
  await expect.poll(width).toBe(88);
  await expect.poll(pageLeft).toBe(88);
  await expect(nav.getByRole("link", { name: "Overview" })).toBeVisible();
  await expect(nav.getByRole("button", { name: "Warehouse menu" })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Packing", exact: true })).toBeHidden();
  await expect(nav.getByRole("button", { name: "Keep sidebar open" })).toHaveCount(0);
  await page.screenshot({ path: "screenshots/sidebar-rail-1440.png" });
  await page.mouse.move(40, 450);
  await expect.poll(width).toBe(276);
  await expect.poll(pageLeft).toBe(276); // pushed, not overlaid
  await page.screenshot({ path: "screenshots/sidebar-peek-1440.png" });
  await nav.getByRole("link", { name: "Packing", exact: true }).click();
  await expect(page).toHaveURL(/\/packing$/);
  await page.mouse.move(900, 400);
  await expect.poll(width).toBe(88);
  // remembered after a reload, and can be pinned open again
  await page.reload();
  await expect.poll(width).toBe(88);
  await page.mouse.move(40, 300);
  await nav.getByRole("button", { name: "Keep sidebar open" }).click();
  await page.mouse.move(900, 400);
  await expect(page.getByRole("button", { name: "Auto-hide sidebar" })).toBeVisible();
});

test("icon rail: every submenu is reachable by keyboard", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page);
  const nav = sidebar(page);
  const width = async () => Math.round((await nav.boundingBox())?.width ?? 0);
  await page.getByRole("button", { name: "Auto-hide sidebar" }).click();
  await page.mouse.move(800, 400);
  await expect.poll(width).toBe(88);
  // Focus on a section icon slides the sidebar open and keeps the focus where it was.
  const warehouse = nav.getByRole("button", { name: "Warehouse menu" });
  await warehouse.focus();
  await expect.poll(width).toBe(276);
  await expect(warehouse).toBeFocused();
  await expect(warehouse).toHaveAttribute("aria-expanded", "true");
  await page.keyboard.press("Tab");
  await expect(nav.getByRole("link", { name: "Packing", exact: true })).toBeFocused();
  // Settings is closed at first: Enter on its icon opens it, and its entries follow in tab order.
  const settings = nav.getByRole("button", { name: "Settings menu" });
  await settings.focus();
  await page.keyboard.press("Enter");
  await expect(settings).toHaveAttribute("aria-expanded", "true");
  await page.keyboard.press("Tab");
  await expect(nav.getByRole("link", { name: "General", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/settings$/);
});

test("icon rail: a tap on a section icon opens the sidebar with that section", async ({ browser }: { browser: Browser }) => {
  const ctx = await browser.newContext({ hasTouch: true, viewport: { width: 1280, height: 800 }, baseURL: "http://localhost:5173" });
  await ctx.addInitScript(() => localStorage.setItem("pt.sidebar.collapsed", "1"));
  const page = await ctx.newPage();
  await signIn(page);
  const nav = sidebar(page);
  const width = async () => Math.round((await nav.boundingBox())?.width ?? 0);
  await expect.poll(width).toBe(88);
  await nav.getByRole("button", { name: "Sales menu" }).tap(); // open by default: the tap must not close it
  await expect.poll(width).toBe(276);
  await expect(nav.getByRole("link", { name: "Sales Orders" })).toBeVisible();
  await nav.getByRole("link", { name: "Customers", exact: true }).tap();
  await expect(page).toHaveURL(/\/customers$/);
  await ctx.close();
});

test("Scan: the label opens the Scan Center, the chevron only opens and closes its entries", async ({ page }) => {
  await signIn(page, fx.users.owner);
  const nav = sidebar(page);
  const scan = nav.getByRole("link", { name: "Scan", exact: true });
  const chevron = nav.getByRole("button", { name: "Scan menu" });
  await expect(nav.getByRole("link", { name: "Scan Box" })).toBeVisible();
  await chevron.click();
  await expect(nav.getByRole("link", { name: "Scan Box" })).toBeHidden();
  await expect(page).toHaveURL(/localhost:5173\/$/); // the chevron goes nowhere
  await chevron.click();
  await expect(nav.getByRole("link", { name: "Scan History" })).toBeVisible();

  await scan.click();
  await expect(page).toHaveURL(/\/scan$/);
  await expect(page.getByRole("heading", { level: 1, name: "Scan Center" })).toBeVisible();
  await expect(current(page)).toHaveText(["Scan"]);
  await expect(crumbs(page).getByRole("listitem")).toHaveText(["Scan", "Scan Center"]);

  await nav.getByRole("link", { name: "Scan History" }).click();
  await expect(page).toHaveURL(/\/scan\?tab=history$/);
  await expect(page.getByRole("tab", { name: "Scan History" })).toHaveAttribute("aria-selected", "true");
  await expect(current(page)).toHaveText(["Scan History"]); // one current destination; Scan is only its parent now
  await expect(crumbs(page).getByRole("listitem")).toHaveText(["Scan", "Scan History"]);
  await expect(crumbs(page).getByRole("link", { name: "Scan", exact: true })).toHaveAttribute("href", "/scan");

  await nav.getByRole("link", { name: "Scan Box" }).click();
  await expect(crumbs(page).getByRole("listitem")).toHaveText(["Scan", "Scan Box"]);
  await nav.getByRole("link", { name: "Dashboard", exact: true }).click();
  await expect(page).toHaveURL(/\/reports$/);
  await expect(crumbs(page).getByRole("listitem")).toHaveText(["Dashboard"]);
  await expect(crumbs(page).locator("[aria-current=page]")).toHaveText("Dashboard");
});

test("deep links, refresh and Back open the right section and highlight one entry", async ({ page }) => {
  await signIn(page, fx.users.owner);
  const nav = sidebar(page);
  // Close Warehouse by hand; a direct link into it opens it again.
  await nav.getByRole("button", { name: "Warehouse menu" }).click();
  await expect(nav.getByRole("link", { name: "Packing", exact: true })).toBeHidden();
  await page.goto("/products/case-sizes");
  await expect(current(page)).toHaveText(["Product Packages"]);
  await expect(nav.getByRole("button", { name: "Warehouse menu" })).toHaveAttribute("aria-expanded", "true");
  await expect(crumbs(page).getByRole("listitem")).toHaveText(["Warehouse", "Product Packages"]);
  await expect(crumbs(page).getByRole("link", { name: "Warehouse" })).toHaveCount(0); // a section has no page: plain text

  // Settings is closed until a settings page is open.
  await expect(nav.getByRole("button", { name: "Settings menu" })).toHaveAttribute("aria-expanded", "false");
  await page.goto("/settings/roles");
  await expect(current(page)).toHaveText(["Users & Roles"]);
  await expect(crumbs(page).getByRole("listitem")).toHaveText(["Settings", "Users & Roles", "Roles & permissions"]);
  await page.reload();
  await expect(current(page)).toHaveText(["Users & Roles"]);

  await nav.getByRole("link", { name: "Sales Invoices", exact: true }).click();
  await expect(page).toHaveURL(/\/invoices$/);
  await expect(current(page)).toHaveText(["Sales Invoices"]);
  await page.goBack();
  await expect(page).toHaveURL(/\/settings\/roles$/);
  await expect(current(page)).toHaveText(["Users & Roles"]);

  // Online orders live inside Sales Orders, as its Online tab; their settings under Settings.
  await page.goto("/online-orders/requests");
  await expect(current(page)).toHaveText(["Sales Orders"]);
  await expect(page.getByRole("tab", { name: "Online" })).toHaveAttribute("aria-selected", "true");
  await expect(crumbs(page).getByRole("listitem")).toHaveText(["Sales", "Sales Orders", "Online", "Requests"]);
  await page.getByRole("navigation", { name: "Online orders pages" }).getByRole("link", { name: "Form builder" }).click();
  await expect(page).toHaveURL(/\/online-orders\/forms$/);
  await page.goto("/online-orders/settings");
  await expect(current(page)).toHaveText(["Online Order Settings"]);
  // The price book is reached from Products.
  await page.goto("/products");
  await page.getByRole("navigation", { name: "Products pages" }).getByRole("link", { name: "Price book" }).click();
  await expect(page).toHaveURL(/\/products\/prices$/);
  await expect(current(page)).toHaveText(["Products"]);
});

test("menu by role: Super Admin sees the admin entries, other staff do not; phone drawer has the same menu", async ({ page }) => {
  await signIn(page, fx.users.owner);
  const nav = sidebar(page);
  await nav.getByRole("button", { name: "Settings menu" }).click();
  for (const name of ["General", "Users & Roles", "Online Order Settings", "Activity Log", "ERP Trash"]) await expect(nav.getByRole("link", { name, exact: true })).toBeVisible();
  await page.screenshot({ path: "screenshots/sidebar-menu-1440.png" });
  await nav.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);

  await signIn(page); // warehouse staff
  await expect(nav.getByRole("link", { name: "General", exact: true })).toBeVisible(); // Settings was left open in this browser
  for (const name of ["Users & Roles", "Integrations", "ERP Trash"]) await expect(nav.getByRole("link", { name, exact: true })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: /^Email/ })).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Open navigation" }).click();
  const drawer = page.getByRole("complementary", { name: "Navigation" });
  await expect(drawer.getByRole("link", { name: "Sales Orders" })).toBeVisible();
  await drawer.getByRole("link", { name: "Scan History" }).click();
  await expect(page).toHaveURL(/\/scan\?tab=history$/);
  await expect(drawer.getByRole("link", { name: "Scan History" })).toBeHidden(); // the drawer closes on arrival
  await expect(crumbs(page).getByRole("listitem")).toHaveText(["Scan", "Scan History"]);
  await page.screenshot({ path: "screenshots/breadcrumbs-390.png" });
});
