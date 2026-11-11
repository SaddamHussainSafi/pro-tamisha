import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Gmail Inbox, the document picker on Create invoice and "Use for OCR", against the real API.
// The inbox is a fresh synthetic company filled by scripts/gmail_ui_fixture.py: made-up emails
// stored as the sync would store them, with each file already kept as a source document. No
// Google call is made anywhere in this file, and the fixture's mailbox is DISCONNECTED, so the
// page truthfully reports that no mailbox is connected. Whether a real mailbox syncs is not
// tested here (it needs the owner's Google Cloud project).
const fx = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/gmail_ui_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
) as {
  password: string;
  mailbox: string;
  users: { owner: string; sales: string; warehouse: string };
  messages: Record<string, string>;
  attachments: Record<string, string>;
};
const SHOTS = process.env.GMAIL_UI_SHOTS ?? "screenshots/gmail";
mkdirSync(SHOTS, { recursive: true });
const SLOW = 15_000; // drawing a PDF page or fetching a file, on dev servers other tests share

test.describe.configure({ mode: "serial" });

async function signIn(page: Page, who: keyof typeof fx.users = "sales") {
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.users[who]);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}
async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}
const emails = (page: Page) => page.getByRole("region", { name: "Emails" });
const rows = (page: Page) => page.getByRole("list", { name: "Email list" }).getByRole("listitem");
const subjects = async (page: Page) => (await rows(page).locator("span.block:nth-child(2)").allTextContents()).map((s) => s.trim());
const sideways = (page: Page) =>
  page.evaluate(() => {
    const main = document.getElementById("main");
    const doc = document.scrollingElement as HTMLElement;
    return Math.max(doc.scrollWidth - doc.clientWidth, main ? main.scrollWidth - main.clientWidth : 0);
  });

test("the sidebar gains one entry and nothing else about it changes", async ({ page }) => {
  await signIn(page);
  const nav = page.getByRole("navigation", { name: /main|primary/i }).first();
  const labels = (await nav.getByRole("link").allTextContents()).map((s) => s.trim()).filter(Boolean);
  const want = ["Overview", "Orders", "Invoices", "Payments", "Online Orders", "Packing", "Inventory", "Receiving", "Scan Center", "Gmail Inbox", "Products", "Customers", "Suppliers", "Reports", "Documents", "Activity"];
  expect(labels.filter((l) => want.includes(l))).toEqual(want);
  await nav.getByRole("link", { name: "Gmail Inbox" }).click();
  await expect(page).toHaveURL(/\/gmail$/);
  await expect(page.getByRole("heading", { name: "Gmail Inbox", level: 1 })).toBeVisible();
  await expect(nav.getByRole("link", { name: "Gmail Inbox" })).toHaveAttribute("aria-current", "page");
});

test("inbox: every email is listed, filters and search run on the server, nothing claims a connection", async ({ page }) => {
  await signIn(page);
  await page.goto("/gmail");
  await expect(emails(page).getByText("7 emails, newest first")).toBeVisible();
  // honest header: this fixture has no Google connection behind it
  await expect(page.getByText("Not set up").or(page.getByText("Not connected")).first()).toBeVisible();
  await expect(page.getByText("Connected", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Refresh" })).toHaveCount(0);

  expect(await subjects(page)).toEqual([
    "Scanned picking sheet",
    "2 scanned documents",
    "Price list next week",
    "Photo of signed sheet",
    "Statement October",
    "Scanned picking sheet",
    "Scanned picking sheet",
  ]);
  // an email without attachments is there too; unread ones are marked
  await expect(rows(page).filter({ hasText: "Maria Lopez" })).toBeVisible();
  await expect(rows(page).filter({ hasText: "(unread)" })).toHaveCount(2);
  await expect(rows(page).first()).toContainText("Picking-Sheet-Nov11.pdf");
  await page.screenshot({ path: `${SHOTS}/inbox-1440.png`, animations: "disabled" });

  const chip = (name: string) => page.getByRole("button", { name, exact: true });
  await chip("Today").click();
  await expect(emails(page).getByText("2 emails, newest first")).toBeVisible();
  await chip("Yesterday").click();
  await expect(emails(page).getByText("1 email, newest first")).toBeVisible();
  await expect(rows(page)).toContainText(["Maria Lopez"]);
  await chip("Last 7 days").click();
  await expect(emails(page).getByText("4 emails, newest first")).toBeVisible();
  await chip("Last 30 days").click();
  await expect(emails(page).getByText("5 emails, newest first")).toBeVisible();
  await chip("All emails").click();
  await chip("PDF documents").click();
  await expect(emails(page).getByText("4 emails, newest first")).toBeVisible();
  await chip("Has attachments").click();
  await expect(emails(page).getByText("6 emails, newest first")).toBeVisible();
  await chip("Not processed").click();
  await expect(emails(page).getByText("5 emails, newest first")).toBeVisible();
  await chip("Processed").click();
  await expect(emails(page).getByText("No emails match")).toBeVisible();
  await chip("Processed").click(); // off again
  await chip("Custom range").click();
  const day = new Date(Date.now() - 45 * 86_400_000).toLocaleDateString("en-CA", { timeZone: "America/Toronto" });
  await page.getByLabel("From").fill(day);
  await page.getByLabel("to", { exact: true }).fill(day);
  await expect(emails(page).getByText("1 email, newest first")).toBeVisible();
  await expect(rows(page)).toContainText(["Picking-Sheet-Sep27.pdf"]);
  await chip("All emails").click();

  const search = page.getByPlaceholder("Search sender, subject or file name");
  await search.fill("maria");
  await expect(emails(page).getByText("1 email, newest first")).toBeVisible();
  await search.fill("jul24");
  await expect(rows(page)).toContainText(["Picking-Sheet-Jul24.pdf"]);
  await search.fill("statement");
  await expect(emails(page).getByText("1 email, newest first")).toBeVisible();
  await search.fill("");
  await expect(emails(page).getByText("7 emails, newest first")).toBeVisible();
});

test("an email opens with a cleaned message, its files and what can be done with each", async ({ page }) => {
  await signIn(page);
  await page.goto("/gmail");
  await rows(page).filter({ hasText: "Maria Lopez" }).getByRole("button").click();
  await expect(page.getByRole("heading", { name: "Price list next week" })).toBeVisible();
  await expect(page.getByText("No attachments")).toBeVisible();
  const frame = page.frameLocator('iframe[title="Email message"]');
  await expect(frame.getByText("this week's price list")).toBeVisible();
  await expect(frame.getByRole("link", { name: "their page" })).toHaveAttribute("rel", /noopener/);
  await expect(frame.locator("script, img, form")).toHaveCount(0);
  await expect(frame.getByText("[image: logo]")).toBeVisible();
  // the frame is sandboxed: no scripts, no same-origin access
  const sandbox = await page.locator('iframe[title="Email message"]').getAttribute("sandbox");
  expect(sandbox).not.toContain("allow-scripts");
  expect(sandbox).not.toContain("allow-same-origin");

  // several attachments: each is its own choice
  await rows(page).filter({ hasText: "2 scanned documents" }).getByRole("button").click();
  await expect(page.getByRole("heading", { name: "Attachments (2)" })).toBeVisible();
  // opening an unread email marks it read (here only: the mailbox is never written to)
  await expect(rows(page).filter({ hasText: "(unread)" })).toHaveCount(1);
  const files = page.getByRole("region", { name: "Attachments" }).getByRole("listitem");
  await expect(files).toContainText(["Order-Sheets-10-pages.pdf", "Island-Grocers-order.pdf"]);
  await expect(files.first()).toContainText("10 pages");
  await expect(page.getByRole("button", { name: "Use Order-Sheets-10-pages.pdf for OCR" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Download Island-Grocers-order.pdf" })).toHaveAttribute("href", new RegExp(`/api/v1/gmail/attachments/${fx.attachments.second}/content$`));
  await page.screenshot({ path: `${SHOTS}/email-detail-1440.png`, animations: "disabled" });
  await axe(page);

  // ten pages stay ten pages, shown one by one
  await page.getByRole("button", { name: "Preview Order-Sheets-10-pages.pdf" }).click();
  const viewer = page.getByRole("dialog", { name: "Order-Sheets-10-pages.pdf" });
  await expect(viewer.getByText("Page 1 of 10")).toBeVisible({ timeout: SLOW });
  await expect(viewer.getByRole("img", { name: /page 1$/ })).toBeVisible({ timeout: SLOW });
  await viewer.getByRole("button", { name: "Next page" }).click();
  await viewer.getByRole("button", { name: "Next page" }).click();
  await expect(viewer.getByText("Page 3 of 10")).toBeVisible();
  await viewer.getByRole("button", { name: "Zoom in" }).click();
  await expect(viewer.getByText("125%")).toBeVisible();
  await viewer.getByRole("button", { name: "Rotate" }).click();
  await page.screenshot({ path: `${SHOTS}/preview-10-pages-1440.png`, animations: "disabled" });
  await viewer.getByRole("button", { name: "Close" }).click();

  // an unsupported file says why, and offers nothing to do with it
  await rows(page).filter({ hasText: "Statement October" }).getByRole("button").click();
  await expect(page.getByRole("heading", { name: "Statement October" })).toBeVisible();
  const xlsx = page.getByRole("region", { name: "Attachments" }).getByRole("listitem");
  await expect(xlsx).toContainText("Only PDF, PNG, JPEG or TIFF attachments can be opened or read");
  await expect(xlsx.getByRole("button")).toHaveCount(0);
  await expect(xlsx.getByRole("link")).toHaveCount(0);
});

test("Create invoice keeps its two ways and gains Pick from Gmail", async ({ page }) => {
  await signIn(page);
  await page.goto("/invoices/new");
  const how = page.getByRole("navigation", { name: "How to invoice" });
  await expect(how.getByRole("link")).toHaveText(["From a packing sheet", "Pick from Gmail", "Direct invoice (no packing sheet)"]);
  await expect(page.getByRole("heading", { name: "Packing sheets ready to invoice" })).toBeVisible();
  await how.getByRole("link", { name: "Direct invoice (no packing sheet)" }).click();
  await expect(page.getByRole("heading", { name: "Direct invoice" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Invoice lines" })).toBeVisible();

  await how.getByRole("link", { name: "Pick from Gmail" }).click();
  await expect(page).toHaveURL(/from=gmail/);
  const picker = page.getByRole("dialog", { name: "Pick from Gmail" });
  await expect(picker).toBeVisible();
  // newest first, older ones are all reachable
  await expect(picker.getByText("6 emails, newest first")).toBeVisible(); // "Has attachments" is on
  await expect(picker.getByRole("button", { name: "Select document" })).toBeDisabled();
  await page.screenshot({ path: `${SHOTS}/pick-from-gmail-1440.png`, animations: "disabled" });

  // a document from 45 days ago, found by date range
  await picker.getByRole("button", { name: "Custom range" }).click();
  const day = new Date(Date.now() - 45 * 86_400_000).toLocaleDateString("en-CA", { timeZone: "America/Toronto" });
  await picker.getByLabel("From").fill(day);
  await picker.getByLabel("to", { exact: true }).fill(day);
  await picker.getByRole("list", { name: "Email list" }).getByRole("listitem").getByRole("button").click();
  await picker.getByRole("button", { name: "Select Picking-Sheet-Sep27.pdf" }).click();
  await expect(picker.getByText("Selected: Picking-Sheet-Sep27.pdf · 1 page")).toBeVisible();
  await expect(picker.getByText("Page 1 of 1")).toBeVisible({ timeout: SLOW }); // preview of the chosen document
  await expect(picker.getByRole("img", { name: /Picking-Sheet-Sep27.pdf, page 1/ })).toBeVisible({ timeout: SLOW });
  await page.screenshot({ path: `${SHOTS}/pick-from-gmail-selected-1440.png`, animations: "disabled" });
  await picker.getByRole("button", { name: "Select document" }).click();

  const use = page.getByRole("dialog", { name: "Use for OCR" });
  await expect(use.getByLabel("Document type")).toHaveValue("PICK_SHEET");
  await expect(use.getByLabel("Warehouse")).toContainText("Main Warehouse");
  await expect(use.getByText("Nothing is invoiced and no stock moves.")).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/use-for-ocr-1440.png`, animations: "disabled" });
  await use.getByRole("button", { name: "Read with OCR" }).click();
  // the existing review screen takes over (reading it is the worker's job)
  await expect(page).toHaveURL(/\/scan\/review\/[0-9a-f-]{36}$/, { timeout: SLOW });
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: SLOW });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${SHOTS}/ocr-review-1440.png`, animations: "disabled" });
});

test("a processed document shows its history and is not read again by accident", async ({ page, browser }) => {
  await signIn(page);
  await page.goto(`/gmail?message=${fx.messages.old_sheet}`);
  await expect(page.getByRole("heading", { name: "Scanned picking sheet" })).toBeVisible();
  const file = page.getByRole("region", { name: "Attachments" }).getByRole("listitem");
  await expect(file).not.toContainText("Not processed");
  await page.getByRole("button", { name: "Processed", exact: true }).click();
  await expect(emails(page).getByText("1 email, newest first")).toBeVisible();

  await page.getByRole("button", { name: "Use Picking-Sheet-Sep27.pdf for OCR" }).click();
  const hist = page.getByRole("dialog", { name: "Previously processed" });
  await expect(hist.getByText("by Gmail UI sales")).toBeVisible();
  await expect(hist.getByText(/SCN-\d{8}-\d{4} · attempt 1/)).toBeVisible();
  await expect(hist.getByRole("link", { name: "View extraction" })).toHaveAttribute("href", /\/scan\/review\//);
  // Sales may not read it again
  await expect(hist.getByText("Reading a document again needs an administrator.")).toBeVisible();
  await expect(hist.getByRole("button", { name: "Reprocess document" })).toBeDisabled();
  await page.screenshot({ path: `${SHOTS}/previously-processed-1440.png`, animations: "disabled" });

  // an administrator can, as a separate attempt
  const ctx = await browser.newContext();
  const admin = await ctx.newPage();
  await signIn(admin, "owner");
  await admin.goto(`/gmail?message=${fx.messages.old_sheet}`);
  await admin.getByRole("button", { name: "Use Picking-Sheet-Sep27.pdf for OCR" }).click();
  const again = admin.getByRole("dialog", { name: "Previously processed" });
  await again.getByRole("button", { name: "Reprocess document" }).click();
  await expect(admin).toHaveURL(/\/scan\/review\/[0-9a-f-]{36}$/, { timeout: SLOW });
  await admin.goto(`/gmail?message=${fx.messages.old_sheet}`);
  await admin.getByRole("button", { name: "Use Picking-Sheet-Sep27.pdf for OCR" }).click();
  await expect(admin.getByRole("dialog", { name: "Previously processed" }).getByText(/attempt 2/)).toBeVisible();
  await expect(admin.getByRole("dialog", { name: "Previously processed" }).getByText(/attempt 1/)).toBeVisible();
  await ctx.close();
});

test("someone without access sees neither the inbox nor the picker", async ({ page }) => {
  await signIn(page, "warehouse");
  await page.goto("/gmail");
  await expect(page.getByText("Your account can't open the Gmail Inbox")).toBeVisible();
  const r = await page.request.get("/api/v1/gmail/messages");
  expect(r.status()).toBe(403);
  const file = await page.request.get(`/api/v1/gmail/attachments/${fx.attachments.sheet}/content`);
  expect(file.status()).toBe(403);
});

test("returning from Google without consent connects nothing", async ({ page }) => {
  await signIn(page, "owner");
  await page.goto("/gmail/oauth/callback?error=access_denied&state=x");
  await expect(page.getByText("The mailbox was not connected")).toBeVisible();
  await expect(page.getByText("Access was not allowed, so nothing was connected.")).toBeVisible();
  expect(page.url()).not.toContain("state=");
  // administrators are told what the server still needs; nothing looks connected
  await page.getByRole("link", { name: "Back to Gmail Inbox" }).click();
  await page.getByRole("button", { name: "Settings" }).click();
  const dlg = page.getByRole("dialog", { name: "Gmail connection" });
  await expect(dlg.getByText("Read-only. Pro Trading cannot send, delete or change mail.")).toBeVisible();
  // This synthetic company has no mailbox either way. Whether the server has a Google client
  // (a developer's .env may) decides what the dialog offers; the test never clicks Connect.
  const status = await (await page.request.get("/api/v1/gmail/status")).json();
  expect(status.account).toBeNull();
  await expect(dlg.getByText("Not connected").or(dlg.getByText("Not set up")).first()).toBeVisible();
  if (status.configured) {
    await expect(dlg.getByText("Connecting opens Google's sign-in page.", { exact: false })).toBeVisible();
    await expect(dlg.getByRole("button", { name: "Connect Gmail" })).toBeVisible();
  } else {
    await expect(dlg.getByText("Gmail is not set up on this server.")).toBeVisible();
    await expect(dlg.getByText("/gmail/oauth/callback")).toBeVisible();
    await expect(dlg.getByRole("button", { name: /Connect/ })).toHaveCount(0);
  }
  await page.screenshot({ path: `${SHOTS}/settings-${status.configured ? "ready-to-connect" : "not-set-up"}-1440.png`, animations: "disabled" });
});

test("at 390 px the inbox and the picker fit, one pane at a time", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page);
  await page.goto("/gmail");
  await expect(emails(page).getByText("7 emails, newest first")).toBeVisible();
  expect(await sideways(page), "/gmail scrolls sideways").toBeLessThanOrEqual(1);
  await page.screenshot({ path: `${SHOTS}/inbox-390.png`, animations: "disabled" });
  await rows(page).filter({ hasText: "2 scanned documents" }).getByRole("button").click();
  await expect(page.getByRole("heading", { name: "Attachments (2)" })).toBeVisible();
  await expect(rows(page).first()).toBeHidden(); // the list makes room for the email
  expect(await sideways(page), "email detail scrolls sideways").toBeLessThanOrEqual(1);
  await page.screenshot({ path: `${SHOTS}/email-detail-390.png`, animations: "disabled" });
  await page.getByRole("button", { name: "All emails" }).last().click();
  await expect(rows(page).first()).toBeVisible();

  await page.goto("/invoices/new?from=gmail");
  const picker = page.getByRole("dialog", { name: "Pick from Gmail" });
  await expect(picker.getByText(/emails, newest first/)).toBeVisible();
  expect(await sideways(page), "picker scrolls sideways").toBeLessThanOrEqual(1);
  await page.screenshot({ path: `${SHOTS}/pick-from-gmail-390.png`, animations: "disabled" });
});

test("header of a connected mailbox (status answer mocked in the browser; not a real connection)", async ({ page }) => {
  await signIn(page);
  await page.route("**/api/v1/gmail/status", async (route) => {
    const real = await (await route.fetch()).json();
    await route.fulfill({
      json: {
        ...real,
        configured: true,
        account: {
          id: "00000000-0000-0000-0000-000000000000",
          email_address: fx.mailbox,
          status: "CONNECTED",
          connected_at: new Date(Date.now() - 86_400_000).toISOString(),
          connected_by_name: "Gmail UI owner",
          last_sync_at: new Date(Date.now() - 120_000).toISOString(),
          last_error_code: null,
          last_error: null,
          last_error_at: null,
          watch_expires_at: null,
          last_push_at: null,
          backfill_done: true,
          version: 1,
        },
      },
    });
  });
  await page.goto("/gmail");
  await expect(page.getByText(fx.mailbox)).toBeVisible();
  await expect(page.getByText("Connected", { exact: true })).toBeVisible();
  await expect(page.getByText("Last synchronized 2 min ago")).toBeVisible();
  await expect(page.getByRole("button", { name: "Refresh" })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/inbox-connected-header-MOCKED-1440.png`, animations: "disabled" });
});
