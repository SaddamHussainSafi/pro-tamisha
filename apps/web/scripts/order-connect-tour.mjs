// Online Orders (Order Connect) screenshots at 1440 and 390 px (staff screens + the customer embed), with the same
// sideways-scroll check as ui-tour.mjs. Local dev stack only (uses dev auto sign-in):
//   node scripts/order-connect-tour.mjs screenshots/online-orders
// Needs at least one form (and ideally one submission) in the demo company.
import { mkdirSync } from "node:fs";
import { chromium } from "@playwright/test";

const BASE = process.env.BASE_URL ?? "http://localhost:5173";
const out = process.argv[2] ?? "screenshots/online-orders";
mkdirSync(out, { recursive: true });
const WIDTHS = [
  [1440, 900],
  [390, 844],
];
const browser = await chromium.launch();
const overflow = [];

async function settle(page) {
  await page.waitForLoadState("networkidle").catch(() => undefined);
  await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]'), null, { timeout: 8000 }).catch(() => undefined);
  await page.waitForTimeout(500);
}

for (const [w, h] of WIDTHS) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, reducedMotion: "reduce" });
  await ctx.addInitScript(() => Object.defineProperty(navigator, "webdriver", { get: () => false }));
  const page = await ctx.newPage();
  await page.goto(`${BASE}/online-orders`);
  await settle(page);
  const api = (p) => page.evaluate((u) => fetch(u).then((r) => r.json()), `/api/v1/order-connect${p}`);
  const forms = await api("/forms");
  const subs = await api("/submissions?status=ALL&limit=1");
  const form = forms[0];
  const screens = [
    ["oc-overview", "/online-orders"],
    ["oc-forms", "/online-orders/forms"],
    form && ["oc-builder", `/online-orders/forms/${form.id}`],
    ["oc-requests", "/online-orders/requests?status=ALL"],
    subs.items?.[0] && ["oc-request", `/online-orders/requests/${subs.items[0].id}`],
    ["oc-embed", "/online-orders/embed"],
    ["oc-settings", "/online-orders/settings"],
    form && ["oc-customer-form", `/embed/${form.public_id}`],
  ].filter(Boolean);
  for (const [name, url] of screens) {
    await page.goto(BASE + url);
    await settle(page);
    const o = await page.evaluate(() => {
      const main = document.getElementById("main");
      const doc = document.scrollingElement;
      return Math.max(doc.scrollWidth - doc.clientWidth, main ? main.scrollWidth - main.clientWidth : 0);
    });
    if (o > 1 && w < 768) overflow.push(`${name} @${w}: ${o}px`);
    const tag = await page.addStyleTag({ content: "html,body,#root{height:auto!important}#main{overflow:visible!important}" }).catch(() => null);
    await page.screenshot({ path: `${out}/${name}-${w}.png`, fullPage: true });
    await tag?.evaluate((el) => el.remove());
  }
  await ctx.close();
}
await browser.close();
if (overflow.length) {
  console.log("Horizontal overflow at 390 px:\n  " + overflow.join("\n  "));
  process.exit(1);
}
console.log(`Saved to ${out}; no horizontal overflow at 390 px.`);
