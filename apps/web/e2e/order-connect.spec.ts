import { execFileSync } from "node:child_process";
import { createServer, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Online Orders (internally Order Connect) end to end: build and publish a form, put the copied
// website code on a (simulated) company page, shop with the cart as a customer and send a quote
// request, watch it arrive live, review it and convert it to a DRAFT order.
// A real WordPress site is NOT part of this test: the "website" is a tiny page served on loopback
// by the test itself (Chrome's local-network checks refuse a faked public page framing localhost).

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };
const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

test.describe.configure({ mode: "serial" });

const shop = `E2E Corner Shop ${Math.random().toString(36).slice(2, 7)}`;
let formUrl = "";
let embedCode = "";
let reference = "";

async function signIn(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByTestId("user-name")).toBeVisible();
}

async function axe(page: Page, exclude: string[] = []) {
  let b = new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]);
  for (const sel of exclude) b = b.exclude(sel);
  const r = await b.analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}

/** A stand-in for the company website: a plain page with the copied iframe code. */
let site: Server;
let sitePort = 0;
test.beforeAll(async () => {
  site = createServer((_req, res) => {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(`<!doctype html><html lang="en"><head><title>Shop</title><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;font-family:sans-serif"><header>Existing site header</header><main><h1>Order online</h1>
${embedCode}</main><footer style="height:300px">Existing site footer</footer></body></html>`);
  });
  await new Promise<void>((ok) => site.listen(0, "127.0.0.1", ok));
  sitePort = (site.address() as AddressInfo).port;
});
test.afterAll(() => new Promise<void>((ok) => site.close(() => ok())));

test("owner builds a form, previews it and publishes it", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.goto("/online-orders/forms");
  await expect(page.getByRole("heading", { name: "Form builder" })).toBeVisible();
  await page.getByRole("button", { name: "New form" }).click();
  await page.getByLabel("Form name").fill("Wholesale orders");
  await page.getByRole("button", { name: "Create form" }).click();
  await expect(page.getByRole("heading", { name: "Wholesale orders" })).toBeVisible();
  formUrl = new URL(page.url()).pathname;

  // Rename a field and add a custom one; the live preview follows without saving.
  await page.getByRole("button", { name: /^Notes/ }).click();
  await page.getByLabel("Label", { exact: true }).fill("Delivery notes");
  await page.getByRole("button", { name: "Add field" }).click();
  await page.getByRole("button", { name: "Checkbox" }).click();
  await page.getByLabel("Label", { exact: true }).fill("We have a loading dock");
  const preview = page.frameLocator('iframe[title="Order form preview"]');
  await page.getByLabel("Preview step").selectOption("checkout");
  await expect(preview.getByLabel("Delivery notes")).toBeVisible();
  await expect(preview.getByLabel("We have a loading dock")).toBeVisible();
  await page.getByRole("button", { name: "Mobile" }).click();
  await expect(page.locator('iframe[title="Order form preview"]')).toHaveCSS("width", "390px");

  await page.getByRole("button", { name: "Publish" }).click();
  await expect(page.getByText(/Published\. Version 1 is now live/)).toBeVisible();
  await expect(page.getByText("Live", { exact: true })).toBeVisible();
  await axe(page, ['iframe[title="Order form preview"]']);
});

test("settings: no minimum fill time for the test, and an allowed website", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.goto("/online-orders/settings");
  await page.getByLabel("Minimum seconds to fill the form").fill("0");
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByText("Settings saved.")).toBeVisible();
  await page.getByLabel("Website address").fill("https://shop.example.test/order");
  await page.getByRole("button", { name: "Allow website" }).click();
  await expect(page.getByText("shop.example.test", { exact: true })).toBeVisible();
  await page.getByLabel("Website address").fill("192.168.1.10");
  await page.getByRole("button", { name: "Allow website" }).click();
  await expect(page.getByText("Use a website name, not an IP address")).toBeVisible();
  await axe(page);

  await page.goto(`/online-orders/embed?form=${formUrl.split("/").pop()}`);
  const code = await page.getByLabel("Iframe code").inputValue();
  expect(code).toMatch(/^<iframe id="tt-order-[A-Za-z0-9_-]{8}" src="http:\/\/localhost:5173\/embed\/[A-Za-z0-9_-]{22}"/);
  expect(code).toContain("<script>");
  expect(code).not.toMatch(/token|csrf|secret/i);
  embedCode = code;
  await axe(page);
});

test("a site that is not allowed cannot show the form", async ({ page }) => {
  // evil.localhost is loopback too, but it is not an allowed website (only localhost/127.0.0.1 are, in dev).
  const refused = page.waitForEvent("console", { predicate: (m) => /frame-ancestors/.test(m.text()), timeout: 10_000 });
  await page.goto(`http://evil.localhost:${sitePort}/order`);
  // The browser refuses to render the framed page because of the CSP frame-ancestors list.
  expect((await refused).text()).toMatch(/has been blocked|Refused to frame/);
  const frame = page.frames().find((f) => f !== page.mainFrame());
  expect(await frame?.locator("#oc-app").count().catch(() => 0)).toBe(0);
});

test("a customer shops with the cart on the website page and sends a quote request (phone width)", async ({ browser }) => {
  // Staff inbox open first, in its own browser: the request must appear there without a reload.
  const staffCtx = await browser.newContext({ viewport: { width: 1440, height: 900 }, reducedMotion: "reduce" });
  const staff = await staffCtx.newPage();
  await signIn(staff, fx.users.owner);
  await staff.goto("/online-orders/requests");
  await expect(staff.getByText("No requests here")).toBeVisible();

  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  await page.goto(`http://127.0.0.1:${sitePort}/order`);
  const form = page.frameLocator('iframe[title="Wholesale orders"]');
  // On the website the form has no header of its own: straight to the products.
  await expect(form.getByLabel("Search products")).toBeVisible();
  await expect(form.getByRole("heading", { name: "Wholesale orders" })).toHaveCount(0);

  await form.getByLabel("Search products").fill("cassava");
  const card = form.getByRole("listitem").filter({ has: form.getByRole("heading", { level: 3 }) }).first();
  const name = (await card.getByRole("heading", { level: 3 }).textContent()) ?? "";
  // Boxes need a size first: the product's own case sizes, or the standard list.
  await card.getByRole("button", { name: `Add ${name} to your order` }).click();
  const size = card.getByLabel(`Box size for ${name}`);
  if ((await size.inputValue()) === "") {
    await expect(card.getByText("Choose a box size first")).toBeVisible();
    await size.selectOption({ index: 1 });
    await card.getByRole("button", { name: `Add ${name} to your order` }).click();
  }
  const boxSize = await size.inputValue();
  await card.getByRole("button", { name: /^More .* of / }).click();
  // ...and the same product per lb, as a second line.
  await card.getByRole("radio", { name: /Per lb/ }).click();
  await card.getByRole("button", { name: `Add ${name} to your order` }).click();
  await expect(form.getByRole("button", { name: /View order/ })).toContainText("2");
  // The website code makes the frame as tall as the form: no box scrolling inside the page,
  // and nothing in the form scrolls sideways at phone width.
  const frame = page.frames().find((f) => f.url().includes("/embed/"));
  await expect
    .poll(async () => {
      const inner = await frame?.evaluate(() => document.documentElement.scrollHeight);
      const outer = await page.locator('iframe[title="Wholesale orders"]').evaluate((el) => el.getBoundingClientRect().height);
      return Math.abs((inner ?? 0) - outer);
    })
    .toBeLessThan(4);
  expect(await frame?.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);

  await form.getByRole("button", { name: /View order/ }).click();
  const sheet = form.getByRole("dialog", { name: "Your order" });
  await expect(sheet.getByLabel(/^Quantity of .* in boxes$/)).toHaveValue("2");
  await expect(sheet.getByLabel(/^Quantity of .* in lb$/)).toHaveValue("1");
  await expect(sheet.getByLabel(`Box size for ${name}`)).toHaveValue(boxSize);
  await sheet.getByRole("button", { name: "Continue" }).click();

  await expect(form.getByRole("heading", { name: "Send your request" })).toBeVisible();
  await form.getByText("Request a quote").click();
  await form.getByRole("button", { name: "Send quote request" }).click();
  await expect(form.getByText("This field is required").first()).toBeVisible();
  await form.getByLabel("Your name").fill("Ada Buyer");
  await form.getByLabel("Business or store name").fill(shop);
  await form.getByLabel("Email").fill("ada@example.test");
  await form.getByLabel("Delivery notes").fill("<script>alert(1)</script> back door");
  await form.getByLabel("We have a loading dock").check();
  const summary = form.getByRole("complementary", { name: "Order summary" });
  await expect(summary.getByText(`2 boxes · ${boxSize} lb`)).toBeVisible();
  await expect(summary.getByText("1 lb", { exact: true })).toBeVisible();
  await form.getByRole("button", { name: "Send quote request" }).click();
  await expect(form.getByRole("heading", { name: "Quote request sent" })).toBeVisible();
  reference = (await form.locator(".oc-ref strong").textContent()) ?? "";
  expect(reference).toMatch(/^OC-[0-9A-Z]{6}$/);

  // Live: the staff inbox, toast and sidebar badge update by themselves.
  await expect(staff.getByTestId(`oc-row-${reference}`)).toBeVisible({ timeout: 15_000 });
  await expect(staff.getByTestId(`oc-row-${reference}`).getByText("Quote request")).toBeVisible();
  await expect(staff.getByText(`New quote request ${reference}`).first()).toBeVisible(); // toast
  await expect(staff.getByTestId("oc-badge").first()).toHaveText("1");
  await axe(staff);
  await ctx.close();
  await staffCtx.close();
});

test("staff review the order and convert it into a draft sales order", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.goto("/online-orders/requests?type=QUOTE");
  await page.getByTestId(`oc-row-${reference}`).click();
  await expect(page.getByRole("heading", { name: shop })).toBeVisible();
  await expect(page.getByText(reference)).toBeVisible();
  await expect(page.getByText("<script>alert(1)</script> back door")).toBeVisible();
  await expect(page.getByText("We have a loading dock")).toBeVisible();
  await expect(page.getByRole("cell", { name: /^2 boxes · \d+ lb$/ })).toBeVisible(); // box size kept
  await expect(page.getByRole("cell", { name: "1 lb", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Start review" }).click();
  await expect(page.getByText("Marked reviewing.")).toBeVisible();
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("heading", { name: "Create draft sales order" })).toBeVisible();
  await expect(page.getByLabel("New customer name")).toHaveValue(shop);
  await axe(page);
  await page.getByRole("button", { name: "Create draft order" }).click();
  await expect(page.getByText(/Converted to draft sales order SO-/)).toBeVisible();
  await expect(page.getByText("No invoice, stock movement or reservation was made.")).toBeVisible();

  await page.getByRole("link", { name: /^SO-/ }).first().click();
  await expect(page.getByRole("heading", { name: /^SO-/ })).toBeVisible();
  await expect(page.getByText("sent from the website, converted")).toBeVisible();
  await expect(page.getByText("Draft", { exact: true }).first()).toBeVisible();
});

test("overview shows the converted request; old Order Connect links still open", async ({ page }) => {
  await signIn(page, fx.users.owner);
  const id = await page.evaluate(async (ref) => {
    const r = await fetch(`/api/v1/order-connect/submissions?status=ALL&q=${ref}`).then((x) => x.json());
    return r.items[0].id as string;
  }, reference);
  await page.goto(`/order-connect/submissions/${id}`);
  await expect(page).toHaveURL(new RegExp(`/online-orders/requests/${id}$`));
  await expect(page.getByText(reference)).toBeVisible();
  await page.goto("/online-orders");
  await expect(page.getByTestId(`oc-row-${reference}`)).toBeVisible();
  await expect(page.getByText("100%")).toBeVisible();
  await axe(page);
});
