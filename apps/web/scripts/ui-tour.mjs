// UI tour: screenshots of every main screen at 1440 and 390 px, plus a horizontal-overflow check.
// Uses the dev server's auto sign-in (demo owner), so run it only against a local dev stack:
//   node scripts/ui-tour.mjs <out-dir>          e.g. node scripts/ui-tour.mjs screenshots/ux/after
// Exits 1 if any screen scrolls sideways at 390 px.
import { mkdirSync } from "node:fs";
import { chromium } from "@playwright/test";

const BASE = process.env.BASE_URL ?? "http://localhost:5173";
const out = process.argv[2] ?? "screenshots/ux/after";
mkdirSync(out, { recursive: true });

const SCREENS = [
  ["overview", "/"],
  ["orders", "/orders"],
  ["order-new", "/orders/new"],
  ["invoices", "/invoices"],
  ["invoice-new", "/invoices/new"],
  ["payments", "/payments"],
  ["receivables", "/receivables"],
  ["picking", "/picking"],
  ["inventory", "/inventory"],
  ["inventory-ledger", "/inventory?tab=movements"],
  ["inventory-counts", "/inventory?tab=counts"],
  ["receiving", "/receiving"],
  ["scan-center", "/scan"],
  ["ocr-review", "/scan?tab=review"],
  ["products", "/products"],
  ["product-detail", "@product"],
  ["case-sizes", "/products/case-sizes"],
  ["pricing", "/products/prices"],
  ["customers", "/customers"],
  ["customer-detail", "@customer"],
  ["suppliers", "/suppliers"],
  ["supplier-payments", "/suppliers/payments"],
  ["reports", "/reports"],
  ["documents", "/documents"],
  ["settings", "/settings"],
  ["settings-users", "/settings/users"],
];
const WIDTHS = [
  [1440, 900],
  [390, 844],
];

const only = process.env.ONLY ? process.env.ONLY.split(",") : null;
const browser = await chromium.launch();
const overflow = [];

async function settle(page) {
  await page.waitForLoadState("networkidle").catch(() => undefined);
  await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]'), null, { timeout: 8000 }).catch(() => undefined);
  await page.waitForTimeout(400);
}

async function firstLink(page, prefix) {
  await page.goto(BASE + prefix);
  await settle(page);
  const href = await page
    .locator(`main a[href^="${prefix}/"]`)
    .evaluateAll((as) => as.map((a) => a.getAttribute("href")).find((h) => /^\/[a-z]+\/[0-9a-f-]{8,}/.test(h ?? "")) ?? null);
  return href;
}

for (const [w, h] of WIDTHS) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: "reduce" });
  // The app skips dev auto sign-in for automated browsers; this tour wants the demo owner.
  await ctx.addInitScript(() => Object.defineProperty(navigator, "webdriver", { get: () => false }));
  const page = await ctx.newPage();
  const detail = { "@product": await firstLink(page, "/products"), "@customer": await firstLink(page, "/customers") };
  for (const [name, path] of SCREENS) {
    if (only && !only.includes(name)) continue;
    const url = path.startsWith("@") ? detail[path] : path;
    if (!url) continue;
    await page.goto(BASE + url);
    await settle(page);
    const o = await page.evaluate(() => {
      const main = document.getElementById("main");
      const doc = document.scrollingElement;
      return Math.max(doc.scrollWidth - doc.clientWidth, main ? main.scrollWidth - main.clientWidth : 0);
    });
    if (o > 1 && w < 768) overflow.push(`${name} @${w}: ${o}px`);
    // <main> is the scroll container; let the page grow so the full-page capture shows everything.
    const tag = await page.addStyleTag({ content: "html,body,#root{height:auto!important}#main{overflow:visible!important}" });
    await page.screenshot({ path: `${out}/${name}-${w}.png`, fullPage: true });
    await tag.evaluate((el) => el.remove());
  }
  await ctx.close();
}

// Signed-out screens.
for (const [w, h] of WIDTHS) {
  if (only && !only.includes("login")) break;
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: "reduce" });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/login`);
  await settle(page);
  await page.screenshot({ path: `${out}/login-${w}.png`, fullPage: true });
  await ctx.close();
}

await browser.close();
if (overflow.length) {
  console.log("Horizontal overflow at 390 px:\n  " + overflow.join("\n  "));
  process.exit(1);
}
console.log(`Saved to ${out}; no horizontal overflow at 390 px.`);
