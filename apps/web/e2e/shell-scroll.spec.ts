import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

// The app frame never scrolls: only the page area (<main>) does. Found on the scan review
// screen (2026-11-11): a screen-reader-only label far down a long page is absolutely placed,
// and with nothing to contain it it stretched the document, so scrolling past the end of the
// page (or any scrollIntoView) dragged the whole frame, sidebar included, off the top and left
// the window half empty. The frame now contains such elements and clips its own overflow.
type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };
const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.users.sales);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

/** Try every way a page could move the frame, then report where things are. */
async function shake(page: Page) {
  const main = page.locator("#main");
  await main.hover();
  for (let i = 0; i < 12; i++) await page.mouse.wheel(0, 1500); // far past the end of the page
  return page.evaluate(async () => {
    const m = document.getElementById("main") as HTMLElement;
    const mainAtEnd = Math.abs(m.scrollHeight - m.clientHeight - m.scrollTop) < 4; // after the wheel
    const last = [...m.querySelectorAll<HTMLElement>(".sr-only, td, li")].pop();
    last?.scrollIntoView({ block: "end" });
    window.scrollTo(0, 99_999);
    document.body.scrollTop = 99_999;
    await new Promise((r) => setTimeout(r, 250));
    const doc = document.scrollingElement as HTMLElement;
    const header = document.querySelector("header") as HTMLElement;
    return {
      windowScroll: window.scrollY + document.body.scrollTop,
      documentOverflow: doc.scrollHeight - doc.clientHeight,
      headerTop: Math.round(header.getBoundingClientRect().top),
      frameTop: Math.round((header.parentElement?.parentElement as HTMLElement).getBoundingClientRect().top),
      mainScrolled: m.scrollTop > 0,
      mainAtEnd,
    };
  });
}

for (const [w, h] of [[1440, 900], [1024, 600], [390, 844]] as const) {
  test(`a long page scrolls inside the frame, never the window (${w}x${h})`, async ({ page }) => {
    await page.setViewportSize({ width: w, height: h });
    await signIn(page);
    await page.goto("/orders/new");
    await expect(page.getByRole("heading", { name: "Create sales order" })).toBeVisible();
    // make the page long, with a screen-reader-only note on its last row
    await page.getByRole("button", { name: "Add 10 more rows" }).click();
    await page.getByRole("button", { name: "Add 10 more rows" }).click();
    await page.getByLabel("Item, line 40", { exact: true }).fill("no such product");
    await page.getByLabel("Quantity, line 39", { exact: true }).click(); // leave the row: its problem shows
    await expect(page.getByTestId("grid-row-40").locator(".sr-only")).toHaveCount(1);

    const got = await shake(page);
    expect(got.mainScrolled, "the page area scrolls").toBe(true);
    expect(got.mainAtEnd, "the page area reaches its end").toBe(true);
    expect(got.windowScroll, "the window did not scroll").toBe(0);
    expect(got.documentOverflow, "nothing stretches the document").toBeLessThanOrEqual(0);
    expect(got.headerTop, "the top bar stays in place").toBeGreaterThanOrEqual(0);
    expect(got.frameTop, "the frame stays at the top of the window").toBe(0);
    if (w >= 1024) await expect(page.getByRole("navigation", { name: "Primary" })).toBeInViewport();
    // the sticky totals bar is still at the bottom of the window
    const bar = await page.getByTestId("net-total").boundingBox();
    expect(bar && bar.y + bar.height).toBeLessThanOrEqual(h);
    expect(bar?.y ?? 0).toBeGreaterThan(h * 0.5);
  });
}

test("data tables with a hidden caption do not stretch the window either", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 500 });
  await signIn(page);
  for (const path of ["/orders", "/customers", "/inventory", "/scan", "/invoices", "/activity"]) {
    await page.goto(path);
    await expect(page.getByRole("status", { name: "Loading" })).toHaveCount(0);
    const got = await shake(page);
    expect(got.windowScroll, `${path}: the window scrolled`).toBe(0);
    expect(got.documentOverflow, `${path}: the document is taller than the window`).toBeLessThanOrEqual(0);
    expect(got.frameTop, `${path}: the frame moved`).toBe(0);
  }
});
