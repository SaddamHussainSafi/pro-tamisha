import { execFileSync } from "node:child_process";
import { homedir } from "node:os";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Email (Lark Mail) against the real API. scripts/mail_ui_fixture.py makes a fresh SYNTHETIC
// company: Andy (Admin, "Andy Mail"), Saddam (Super Admin, "Saddam Mail" + shared "Info Mail"),
// Vada (Admin, no mailbox) and an owner (Super Admin, no mailbox). The mail was imported by the
// real sync code from a local stand-in IMAP server that is gone again, and the mailboxes are
// PAUSED. No Lark server is contacted and no real email is sent: the stand-in SMTP server no
// longer exists, so a queued message can only stay queued or fail, and the page must say so.
const fx = JSON.parse(
  execFileSync("uv", ["run", "--no-sync", "python", "scripts/mail_ui_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src:.", UV_PROJECT_ENVIRONMENT: process.env.UV_PROJECT_ENVIRONMENT || `${homedir()}/.local/share/pro-trading/venv` },
  }),
) as { password: string; tag: string; users: Record<"owner" | "andy" | "saddam" | "vada", string>; accounts: Record<"andy" | "saddam" | "info", string> };

test.describe.configure({ mode: "serial" });
test.setTimeout(60_000); // shared dev servers; axe also walks the email frames

async function signIn(page: Page, who: keyof typeof fx.users) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.users[who]);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}
const nav = (page: Page) => page.getByRole("navigation", { name: "Primary" });
const list = (page: Page) => page.getByRole("list", { name: "Email list" });
const rows = (page: Page) => list(page).getByRole("listitem");
const row = (page: Page, text: string | RegExp) => rows(page).filter({ hasText: text }).first();
/** The row's own button opens the email (checkbox, star and chips sit above it). */
const openRow = (page: Page, text: string | RegExp) => row(page, text).getByRole("button").first().click();
const folders = (page: Page) => page.getByRole("navigation", { name: "Folders" });
const reading = (page: Page) => page.getByRole("article", { name: "Email" });
const actions = (page: Page) => page.getByRole("toolbar", { name: "Email actions" });
const listBar = (page: Page) => page.getByRole("toolbar", { name: "List actions" });
const body = (page: Page) => page.frameLocator('iframe[title="Email message"]').first();
const main = (page: Page) => page.locator("#main");
async function noDeleteAnywhere(page: Page) {
  // There is no Trash in this module and nothing deletes, trashes, archives or moves an email.
  // ("Archive" exists only as Lark's own folder name in the rail, never as an action.)
  await expect(main(page).getByRole("button", { name: /delete|trash|bin\b|move to|snooze|^archive (this|selected|email)/i })).toHaveCount(0);
  await expect(main(page).getByRole("link", { name: /delete|trash/i })).toHaveCount(0);
  await expect(page.getByRole("menuitem", { name: /delete|trash|archive|move/i })).toHaveCount(0);
  await expect(main(page).getByRole("checkbox", { name: /delete|trash/i })).toHaveCount(0);
}
async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).setLegacyMode(true).include("#main").withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}

test("Andy sees only Andy Mail; the Email entry sits beside Scan Box, which is unchanged", async ({ page }) => {
  await signIn(page, "andy");
  await expect(nav(page).getByRole("link", { name: /^Email/ })).toBeVisible();
  await expect(nav(page).getByRole("link", { name: "Scan Box" })).toBeVisible();
  await expect(page.getByTestId("mail-badge")).toHaveText("10");
  await nav(page).getByRole("link", { name: /^Email/ }).click();
  await expect(page).toHaveURL(/\/email$/);
  await expect(page.getByRole("heading", { name: "Email", level: 1 })).toBeAttached();
  await expect(main(page).getByText(`andy.${fx.tag}@tamishatrading.com`)).toBeVisible();
  await expect(main(page).getByText("Andy Mail (synthetic)").first()).toBeVisible();
  // honest about the synthetic, paused stand-in: never "Connected"
  await expect(main(page).getByText("Paused").first()).toBeVisible();
  await expect(main(page).getByText(/synthetic test mailbox/)).toBeVisible();
  await expect(main(page).getByText("Connected", { exact: true })).toHaveCount(0);
  // one mailbox: no mailbox picker, nothing of Saddam's or Info's
  await expect(page.getByRole("button", { name: /^Mailbox:/ })).toHaveCount(0);
  await expect(main(page).getByText(/Info Mail|Saddam Mail/)).toHaveCount(0);
  await expect(rows(page).first()).toContainText("Photo of the damaged pallet");
  await expect(listBar(page)).toContainText("1–49 of 49");
  await noDeleteAnywhere(page);
});

test("rail: mailbox card, folders with counts, quick-filter tabs and the URL", async ({ page }) => {
  await signIn(page, "andy");
  await page.goto("/email");
  await expect(folders(page).getByRole("button", { name: /Inbox/ })).toHaveAttribute("aria-current", "page");
  await expect(folders(page).getByRole("button", { name: /Inbox/ })).toContainText("10");
  // a short folder list is shown whole (no More), and there is no Outbox folder
  await expect(folders(page).getByRole("button")).toHaveText([/Inbox/, "Starred", "Sent", "Drafts", "Archive", "Junk"]);
  await folders(page).getByRole("button", { name: "Sent" }).click();
  await expect(page).toHaveURL(/\/email\?account=[0-9a-f-]{36}&folder=[0-9a-f-]{36}$/);
  await expect(rows(page).first()).toContainText("Re: ");
  await expect(rows(page).filter({ hasNotText: "Re: " })).toHaveCount(0);
  // my own sent mail shows who it went to, like a mail client
  await expect(rows(page).first()).toContainText("To: ");
  await folders(page).getByRole("button", { name: "Junk" }).click();
  await expect(main(page).getByText("No emails here")).toBeVisible();
  await folders(page).getByRole("button", { name: /Inbox/ }).click();
  await expect(rows(page)).toHaveCount(49);

  // honest quick filters (no made-up categories)
  const tabs = page.getByRole("tablist", { name: "Show" });
  await expect(tabs.getByRole("tab")).toHaveText(["All", /Unread/, "Starred", "Has attachments"]);
  await tabs.getByRole("tab", { name: /Unread/ }).click();
  await expect(rows(page)).toHaveCount(10);
  await expect(listBar(page)).toContainText("1–10 of 10");
  await tabs.getByRole("tab", { name: "Has attachments" }).click();
  await expect(rows(page).filter({ has: page.getByRole("button", { name: /^Preview / }) }).first()).toBeVisible();
  await expect.poll(() => rows(page).count()).toBeLessThan(49);
  await tabs.getByRole("tab", { name: "All" }).click();
  await expect(rows(page)).toHaveCount(49);
});

test("open a conversation: sandboxed body, Load images, attachment chips and cards, download", async ({ page }) => {
  await signIn(page, "andy");
  await page.goto("/email");
  // a file chip right in the list opens the preview without opening the email
  await row(page, "Photo of the damaged pallet").getByRole("button", { name: "Preview pallet.png" }).click();
  const img = page.getByRole("dialog", { name: "pallet.png" }).getByRole("img", { name: "pallet.png" });
  await expect(img).toBeVisible();
  await expect.poll(() => img.evaluate((i: HTMLImageElement) => i.naturalWidth)).toBeGreaterThan(0);
  await page.keyboard.press("Escape");
  await expect(reading(page)).toHaveCount(0);

  await openRow(page, "Delivery Tuesday — Northside Market");
  await expect(list(page)).toHaveCount(0); // the conversation replaces the list
  await expect(reading(page).getByRole("heading", { name: "Delivery Tuesday — Northside Market", level: 2 })).toBeVisible();
  await expect(reading(page).getByText("Inbox", { exact: true })).toBeVisible(); // folder chip
  await expect(actions(page)).toContainText(/2 of 49/);
  const frame = reading(page).locator('iframe[title="Email message"]').first();
  const sandbox = (await frame.getAttribute("sandbox")) ?? "";
  expect(sandbox).not.toContain("allow-scripts");
  expect(sandbox).not.toContain("allow-same-origin");
  await expect(body(page).getByText("Basmati rice 10 kg")).toBeVisible();
  // details toggle: to / date
  await reading(page).getByRole("button", { name: /^to me/ }).click();
  await expect(reading(page).getByRole("definition").filter({ hasText: `andy.${fx.tag}@tamishatrading.com` })).toBeVisible();
  // remote pictures stay off until asked for
  expect(await frame.getAttribute("srcdoc")).not.toMatch(/<img[^>]*\ssrc="https:/);
  await reading(page).getByRole("button", { name: "Load images" }).click();
  await expect(reading(page).getByRole("button", { name: "Load images" })).toHaveCount(0);
  await expect.poll(async () => (await reading(page).locator('iframe[title="Email message"]').first().getAttribute("srcdoc")) ?? "").toMatch(/<img[^>]*\ssrc="https:\/\/tracker\.example\/open\.gif"/);

  // PDF attachment card: preview inline + download as a file
  const files = reading(page).getByRole("region", { name: "Attachments" });
  await expect(files.getByText(/^PO-\d+\.pdf$/)).toBeVisible();
  await files.getByRole("button", { name: /Preview PO-/ }).click();
  const dlg = page.getByRole("dialog", { name: /PO-\d+\.pdf/ });
  await expect(dlg.locator("iframe")).toHaveAttribute("src", /inline=true/);
  await dlg.getByRole("button", { name: "Close" }).click();
  const href = (await files.getByRole("link", { name: /Download PO-/ }).getAttribute("href")) ?? "";
  const res = await page.request.get(href);
  expect(res.status()).toBe(200);
  expect(res.headers()["content-disposition"]).toMatch(/^attachment/);

  // ‹ › walk the list; u goes back to it
  await actions(page).getByRole("button", { name: "Older" }).click();
  await expect(actions(page)).toContainText(/3 of 49/);
  await actions(page).getByRole("button", { name: "Newer" }).click();
  await expect(reading(page).getByRole("heading", { level: 2 })).toHaveText("Delivery Tuesday — Northside Market");
  await noDeleteAnywhere(page);
  await axe(page);
  await page.locator("body").press("u");
  await expect(list(page)).toBeVisible();
  await axe(page);
});

test("read, unread and star are kept per person: rows, hover actions, bulk actions, keyboard", async ({ page }) => {
  await signIn(page, "andy");
  await page.goto("/email");
  // the pallet photo is still unread (the chip preview did not open it)
  const inbox = folders(page).getByRole("button", { name: /Inbox/ });
  await expect(inbox).toContainText("10");
  await openRow(page, "Photo of the damaged pallet");
  await expect(reading(page).getByRole("heading", { level: 2 })).toHaveText("Photo of the damaged pallet");
  await expect(inbox).toContainText("9");
  await expect(page.getByTestId("mail-badge")).toHaveText("9");
  // s stars the open email, Shift+U marks it unread again and returns to the list
  await page.locator("body").press("s");
  await expect(actions(page).getByRole("button", { name: "Unstar" })).toHaveAttribute("aria-pressed", "true");
  await page.locator("body").press("Shift+U");
  await expect(list(page)).toBeVisible();
  await expect(row(page, "Photo of the damaged pallet")).toContainText("(unread)");
  await expect(inbox).toContainText("10");

  // hover action on a row: mark as unread / read
  const price = row(page, "Price list question (Spice Route Foods)");
  await price.hover();
  await price.getByRole("button", { name: /as unread$/ }).click();
  await expect(price).toContainText("(unread)");
  await price.hover();
  await price.getByRole("button", { name: /as read$/ }).click();
  await expect(price).not.toContainText("(unread)");

  // star from the row; Starred lists both, also after a reload (kept on the server)
  await price.getByRole("button", { name: /^Star / }).click();
  await folders(page).getByRole("button", { name: "Starred" }).click();
  await expect(rows(page)).toHaveCount(2);
  await page.reload();
  await expect(rows(page)).toHaveCount(2);

  // bulk: select all on the page → Unstar
  await listBar(page).getByRole("checkbox", { name: "Select all on this page" }).check();
  await expect(listBar(page)).toContainText("2 selected");
  await listBar(page).getByRole("button", { name: "Unstar" }).click();
  await expect(page.getByRole("status").filter({ hasText: "2 emails unstarred" })).toBeVisible();
  await expect(main(page).getByText("No emails here")).toBeVisible();

  // bulk read / unread through the Select menu (Unread → Mark as read → back)
  await folders(page).getByRole("button", { name: /Inbox/ }).click();
  await expect(rows(page)).toHaveCount(49);
  await listBar(page).getByRole("button", { name: "Select" }).click();
  await page.getByRole("menuitem", { name: "Unread" }).click();
  await expect(listBar(page)).toContainText("10 selected");
  await listBar(page).getByRole("button", { name: "Mark as read" }).click();
  await expect(page.getByRole("status").filter({ hasText: "10 emails marked as read" })).toBeVisible();
  await expect(inbox).not.toContainText(/\d/);
  await expect(page.getByTestId("mail-badge")).toHaveCount(0);
  // keyboard: j moves, x selects, Shift+U marks the selection unread
  await page.locator("body").press("j");
  await page.locator("body").press("x");
  await page.locator("body").press("j");
  await page.locator("body").press("x");
  await expect(listBar(page)).toContainText("2 selected");
  await listBar(page).getByRole("button", { name: "Mark as unread" }).click();
  await expect(page.getByRole("status").filter({ hasText: "2 emails marked as unread" })).toBeVisible();
  await noDeleteAnywhere(page);
});

test("search is live, limited to this mailbox, highlights words, pages with ‹ ›, works from the keyboard", async ({ page }) => {
  await signIn(page, "andy");
  await page.goto("/email");
  const box = page.getByPlaceholder("Search Andy Mail");
  await expect(rows(page).first()).toBeVisible();
  await page.locator("body").press("/");
  await expect(box).toBeFocused();
  await box.fill("Northside");
  await expect(page.getByRole("region", { name: "Search results" })).toBeVisible();
  await expect(rows(page).first().locator("mark:visible").first()).toHaveText(/Northside/i);
  const n = await rows(page).count();
  expect(n).toBeGreaterThan(3);
  await expect(rows(page).filter({ hasNotText: /Northside/i })).toHaveCount(0);
  await box.press("ArrowDown");
  await box.press("Enter");
  await expect(reading(page).getByRole("heading", { level: 2 })).toContainText(/Northside/);
  // search options: attachments only
  await page.getByRole("button", { name: "Search options" }).click();
  const opts = page.getByRole("group", { name: "Search options" });
  await opts.getByLabel("Has attachments").check();
  await opts.getByRole("button", { name: "Search" }).click();
  await expect.poll(() => rows(page).count()).toBeLessThan(n);
  await expect(rows(page).first()).toContainText(/Northside/);
  await page.getByRole("button", { name: "Clear search" }).click();
  await expect(page.getByRole("region", { name: "Search results" })).toHaveCount(0);
  // keyset paging: 30 search results a page, ‹ › walk them
  await box.fill("confirm");
  await expect(listBar(page)).toContainText("1–30 of many");
  const firstPage = await rows(page).first().getAttribute("id");
  await listBar(page).getByRole("button", { name: "Older" }).click();
  await expect(listBar(page)).toContainText(/31–\d+/);
  await expect.poll(() => rows(page).first().getAttribute("id")).not.toBe(firstPage);
  await listBar(page).getByRole("button", { name: "Newer" }).click();
  await expect(listBar(page)).toContainText("1–30 of many");
  await expect(rows(page).first()).toHaveAttribute("id", firstPage ?? "");
  await box.fill("zzqxnothing");
  await expect(main(page).getByText("No emails match")).toBeVisible();
});

test("compose window with Cc/Bcc and a file: draft autosaves, Send queues once, the status is honest", async ({ page }) => {
  await signIn(page, "andy");
  await page.goto("/email");
  await expect(rows(page).first()).toBeVisible();
  await page.locator("body").press("c");
  const dlg = page.getByRole("dialog", { name: "New message" });
  await expect(dlg).toBeVisible();
  await expect(dlg.getByLabel("To", { exact: true })).toBeFocused();
  await expect(dlg.getByRole("combobox", { name: "From" })).toHaveValue(fx.accounts.andy);
  await dlg.getByLabel("To", { exact: true }).fill("maria@greenleaf-grocers.example,");
  await expect(dlg.getByRole("group", { name: "To recipients" })).toContainText("maria@greenleaf-grocers.example");
  await dlg.getByRole("button", { name: "Cc", exact: true }).click();
  await dlg.getByLabel("Cc", { exact: true }).fill("not-an-address");
  await dlg.getByLabel("Cc", { exact: true }).press("Enter");
  await expect(dlg.getByText("1 address is not valid")).toBeVisible();
  await expect(dlg.getByRole("button", { name: /^Send/ })).toBeDisabled();
  await dlg.getByRole("button", { name: "Remove not-an-address from Cc" }).click();
  await dlg.getByLabel("Cc", { exact: true }).fill("tom@northside-market.example");
  await dlg.getByLabel("Cc", { exact: true }).press("Enter");
  await dlg.getByLabel("Bcc", { exact: true }).fill("Priya Shah <priya@spiceroute.example>;");
  await dlg.getByLabel("Subject").fill("E2E synthetic order confirmation");
  await expect(dlg.getByRole("button", { name: "E2E synthetic order confirmation" })).toBeVisible(); // title bar follows the subject
  await dlg.getByRole("textbox", { name: "Message" }).click();
  await page.keyboard.type("Confirmed for Tuesday. (synthetic test, never delivered)");
  await dlg.getByLabel("Files to attach").setInputFiles({ name: "order-note.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n") });
  await expect(dlg.getByRole("region", { name: "Attachments" })).toContainText("order-note.pdf");
  await expect(dlg.getByText(/Draft saved/)).toBeVisible({ timeout: 10_000 });
  // minimise keeps it open as a title bar; restore brings the form back
  await dlg.getByRole("button", { name: "Minimise" }).click();
  await expect(dlg.getByRole("textbox", { name: "Message" })).toBeHidden();
  await dlg.getByRole("button", { name: "Restore" }).click();
  await expect(dlg.getByRole("textbox", { name: "Message" })).toBeVisible();
  await dlg.getByRole("button", { name: "Save and close" }).click();
  await expect(dlg).toHaveCount(0);

  // the draft is listed in Drafts (count in the rail), above any Lark drafts
  await expect(folders(page).getByRole("button", { name: /Drafts/ })).toContainText("1");
  await folders(page).getByRole("button", { name: /Drafts/ }).click();
  const saved = page.getByRole("region", { name: "Drafts saved in Pro Trading" });
  await expect(saved).toContainText("E2E synthetic order confirmation");
  await saved.getByRole("button", { name: /E2E synthetic order confirmation/ }).click();
  const draft = page.getByRole("dialog", { name: "Draft" });
  await expect(draft.getByLabel("Subject")).toHaveValue("E2E synthetic order confirmation");
  await expect(draft.getByRole("group", { name: "Bcc recipients" })).toContainText("Priya Shah");
  await expect(draft.getByRole("region", { name: "Attachments" })).toContainText("order-note.pdf"); // the reopened draft names its file

  // Send: the first request is lost on the way; pressing Send again reuses the SAME key
  const keys: string[] = [];
  await page.route("**/api/v1/mail/send", async (route) => {
    keys.push(route.request().headers()["idempotency-key"] ?? "");
    if (keys.length === 1) return route.abort("connectionreset");
    return route.continue();
  });
  await draft.getByRole("button", { name: /^Send/ }).click();
  await expect(draft.getByRole("alert")).toBeVisible();
  await draft.getByRole("button", { name: /^Send/ }).click();
  await expect(draft).toHaveCount(0);
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);
  await page.unroute("**/api/v1/mail/send");
  // the toast follows the real state (the stand-in SMTP server is gone, so never "Message sent")
  await expect(page.getByRole("status").filter({ hasText: /^(Sending…|Not sent: Lark did not accept it|May have been sent)/ })).toBeVisible();
  await expect(page.getByText("Message sent")).toHaveCount(0);
  await expect(saved).toHaveCount(0); // a sent draft leaves Drafts

  // Sent shows it at once while it is queued; a failed one is never shown as sent, only as a
  // warning in the rail with the details and "Send again"
  const out = (await (await page.request.get(`/api/v1/mail/accounts/${fx.accounts.andy}/outgoing`)).json()) as { subject: string; status: string }[];
  const mine = out.find((o) => o.subject === "E2E synthetic order confirmation");
  expect(mine?.status).toMatch(/^(QUEUED|SENDING|FAILED|UNCERTAIN)$/);
  await folders(page).getByRole("button", { name: "Sent" }).click();
  const pendingRow = list(page).locator("li[data-pending]").filter({ hasText: "E2E synthetic order confirmation" });
  if (mine?.status === "QUEUED" || mine?.status === "SENDING") {
    await expect(pendingRow).toContainText("Sending…");
    await expect(pendingRow).toContainText("To: maria@greenleaf-grocers.example");
  } else {
    await expect(pendingRow).toHaveCount(0);
    await page.getByRole("button", { name: /not sent: see details/ }).click();
    const box = page.getByRole("dialog", { name: "Outbox" });
    const item = box.getByRole("listitem").filter({ hasText: "E2E synthetic order confirmation" });
    await expect(item.getByText(/^(Failed|May have been sent)$/)).toBeVisible();
    await box.getByRole("button", { name: "Close" }).click();
  }
  await noDeleteAnywhere(page);
});

test("reply and forward open inline under the conversation; reply quotes and goes to the sender", async ({ page }) => {
  await signIn(page, "andy");
  await page.goto("/email");
  await openRow(page, "Price list question (Spice Route Foods)");
  await expect(reading(page).getByRole("heading", { level: 2 })).toHaveText("Price list question (Spice Route Foods)");
  await page.locator("body").press("r");
  const reply = reading(page).getByRole("region", { name: "Reply", exact: true });
  await expect(reply).toBeVisible();
  await expect(reply.getByRole("group", { name: "To recipients" })).toContainText("Priya Shah");
  await expect(reply.getByRole("textbox", { name: "Message" }).locator("blockquote")).toContainText("Spice Route Foods");
  await expect(reply.getByRole("combobox", { name: "From" })).toHaveCount(0); // a reply goes out from this mailbox
  await reply.getByRole("button", { name: "Discard draft" }).click();
  await expect(reply).toHaveCount(0);
  await expect(reading(page).getByRole("button", { name: "Reply all" })).toBeVisible();

  await page.locator("body").press("u");
  await openRow(page, "Delivery Tuesday — Northside Market");
  await expect(reading(page).getByRole("heading", { level: 2 })).toHaveText("Delivery Tuesday — Northside Market");
  await reading(page).getByRole("button", { name: "Forward" }).click();
  const fwd = reading(page).getByRole("region", { name: "Forward" });
  await expect(fwd.getByLabel("Subject")).toHaveValue("Fwd: Delivery Tuesday — Northside Market");
  await expect(fwd.getByRole("checkbox", { name: /PO-\d+\.pdf/ })).toBeChecked();
  await fwd.getByRole("button", { name: "Discard draft" }).click();
});

test("better than the usual: reading pane on the right, compact density, shortcut sheet; remembered", async ({ page }) => {
  await signIn(page, "andy");
  await page.goto("/email");
  await expect(rows(page).first()).toBeVisible();
  await page.locator("body").press("?");
  const help = page.getByRole("dialog", { name: "Keyboard shortcuts" });
  await expect(help).toContainText("Mark as unread");
  await help.getByRole("button", { name: "Close" }).click();

  await listBar(page).getByRole("button", { name: "More options" }).click();
  await page.getByRole("menuitemradio", { name: "Right of inbox" }).click();
  await openRow(page, "Delivery Tuesday — Northside Market");
  await expect(reading(page)).toBeVisible();
  await expect(list(page)).toBeVisible(); // both side by side
  await page.reload();
  await openRow(page, "Price list question (Spice Route Foods)");
  await expect(list(page)).toBeVisible();
  await expect(reading(page).getByRole("heading", { level: 2 })).toHaveText("Price list question (Spice Route Foods)");
  await actions(page).getByRole("button", { name: "Close" }).click();
  await listBar(page).getByRole("button", { name: "More options" }).click();
  await expect(page.getByRole("menuitemradio", { name: "Right of inbox" })).toHaveAttribute("aria-checked", "true");
  await page.getByRole("menuitemradio", { name: "Compact" }).click();
  await listBar(page).getByRole("button", { name: "More options" }).click();
  await page.getByRole("menuitemradio", { name: "No split" }).click();
  await listBar(page).getByRole("button", { name: "More options" }).click();
  await page.getByRole("menuitemradio", { name: "Comfortable" }).click();
  expect(await page.evaluate(() => localStorage.getItem("pt.mail.prefs"))).toBe(JSON.stringify({ pane: "none", density: "comfortable" }));
});

test("signature: pick a template, fill details, save; it comes back and compose uses it", async ({ page }) => {
  await signIn(page, "andy");
  await page.goto("/email");
  await expect(rows(page).first()).toBeVisible();
  await listBar(page).getByRole("button", { name: "More options" }).click();
  await page.getByRole("menuitem", { name: "Signature…" }).click();
  const dlg = page.getByRole("dialog", { name: /^Signature · Andy Mail/ });
  // sensible defaults from the person and the mailbox
  await expect(dlg.getByLabel("Full name", { exact: true })).toHaveValue("Andy (synthetic)");
  await expect(dlg.getByLabel("Email", { exact: true })).toHaveValue(`andy.${fx.tag}@tamishatrading.com`);
  await expect(dlg.getByLabel("Company", { exact: true })).toHaveValue("Tamisha Trading Inc.");
  await dlg.getByRole("radio", { name: "Modern" }).click();
  await dlg.getByLabel("Job title", { exact: true }).fill("Sales Manager");
  await dlg.getByLabel("Phone", { exact: true }).fill("+1 416 555 0100");
  await dlg.getByRole("radio", { name: "Forest" }).click();
  const preview = dlg.getByRole("region", { name: "Signature preview" });
  await expect(preview).toContainText("Sales Manager");
  await expect(preview.locator("img")).toHaveAttribute("src", "/api/v1/mail/signature-logo");
  await expect.poll(() => preview.locator("img").evaluate((i: HTMLImageElement) => i.naturalWidth)).toBeGreaterThan(0);
  await dlg.getByRole("button", { name: "Save signature" }).click();
  await expect(dlg.getByText("Signature saved.")).toBeVisible();
  // stored on the server with the logo as an embedded picture reference, not a web address
  const stored = (await (await page.request.get(`/api/v1/mail/accounts/${fx.accounts.andy}/signature`)).json()) as { html: string; fields: { template: string; title: string } };
  expect(stored.fields).toMatchObject({ template: "modern", title: "Sales Manager" });
  expect(stored.html).toContain("tt-logo");
  expect(stored.html).not.toContain("/api/v1/mail/signature-logo");
  await dlg.getByRole("button", { name: "Close" }).first().click();

  await page.reload();
  await page.getByRole("button", { name: "Signature", exact: true }).click(); // the rail link
  const again = page.getByRole("dialog", { name: /^Signature · Andy Mail/ });
  await expect(again.getByRole("radio", { name: "Modern" })).toHaveAttribute("aria-checked", "true");
  await expect(again.getByLabel("Job title", { exact: true })).toHaveValue("Sales Manager");
  await expect(again.getByLabel("Phone", { exact: true })).toHaveValue("+1 416 555 0100");
  await again.getByRole("button", { name: "Close" }).first().click();

  await page.locator("body").press("c");
  const compose = page.getByRole("dialog", { name: "New message" });
  const editor = compose.getByRole("textbox", { name: "Message" });
  await expect(editor).toContainText("Sales Manager");
  await expect(editor).toContainText("--");
  await expect(editor.locator("img")).toHaveAttribute("src", "/api/v1/mail/signature-logo");
  await compose.getByRole("button", { name: "Signature" }).click();
  await page.getByRole("menuitemradio", { name: "No signature" }).click();
  await expect(editor).not.toContainText("Sales Manager");
  await compose.getByRole("button", { name: "Signature" }).click();
  await page.getByRole("menuitemradio", { name: "My signature" }).click();
  await expect(editor).toContainText("Sales Manager");
  // the formatting bar is there, Gmail-style
  const bar = compose.getByRole("toolbar", { name: "Formatting" });
  for (const name of ["Undo (Ctrl / ⌘ Z)", "Font", "Size", "Bold (Ctrl / ⌘ B)", "Text colour", "Align", "Numbered list", "Bulleted list", "Indent more", "Quote", "Strikethrough", "Remove formatting"]) await expect(bar.getByRole("button", { name })).toBeVisible();
  await compose.getByRole("button", { name: "Discard draft" }).click();
  await expect(compose).toHaveCount(0);
});

test("Saddam switches between Saddam Mail and Info Mail: list and search follow the mailbox", async ({ page }) => {
  await signIn(page, "saddam");
  await page.goto("/email");
  await page.getByRole("button", { name: /^Mailbox: Saddam Mail/ }).click();
  await expect(page.getByRole("menuitemradio")).toHaveText([/Saddam Mail \(synthetic\)/, /Info Mail \(synthetic\)/]);
  await page.keyboard.press("Escape");
  const ids = async () => (await rows(page).evaluateAll((els) => els.map((e) => e.id))).sort();
  await expect(main(page).getByText(`saddam.${fx.tag}@tamishatrading.com`).first()).toBeVisible();
  await expect(rows(page)).toHaveCount(49);
  const saddamIds = await ids();
  await page.getByPlaceholder("Search Saddam Mail").fill("Greenleaf");
  await expect(page.getByRole("region", { name: "Search results" })).toBeVisible();
  await openRow(page, "Maria Lopez"); // search covers Sent too
  await expect(body(page).getByText("Hi Saddam")).toBeVisible();

  await page.getByRole("button", { name: /^Mailbox: Saddam Mail/ }).click();
  await page.getByRole("menuitemradio", { name: /Info Mail/ }).click();
  await expect(page).toHaveURL(new RegExp(`/email\\?account=${fx.accounts.info}$`));
  await expect(main(page).getByText(`info.${fx.tag}@tamishatrading.com`).first()).toBeVisible();
  // everything was reset: no search, nothing open, the Inbox of Info Mail
  await expect(page.getByPlaceholder("Search Info Mail")).toHaveValue("");
  await expect(reading(page)).toHaveCount(0);
  await expect(rows(page)).toHaveCount(49);
  const infoIds = await ids();
  expect(infoIds.filter((i) => saddamIds.includes(i))).toEqual([]);
  await page.getByPlaceholder("Search Info Mail").fill("Greenleaf");
  await openRow(page, "Maria Lopez");
  await expect(body(page).getByText("Hi Info")).toBeVisible();

  // a ⌘K search hit: /email?account=…&message=… opens that mailbox and that email, even one
  // that is not on the first page of the list
  const page1 = (await (await page.request.get(`/api/v1/mail/accounts/${fx.accounts.saddam}/messages?limit=60`)).json()) as { items: { id: string; subject: string }[] };
  const old = page1.items[page1.items.length - 1];
  if (!old) throw new Error("fixture has no mail");
  await page.goto(`/email?account=${fx.accounts.saddam}&message=${old.id}`);
  await expect(main(page).getByText(`saddam.${fx.tag}@tamishatrading.com`).first()).toBeVisible();
  await expect(reading(page).getByRole("heading", { name: old.subject, level: 2 })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`/email\\?account=${fx.accounts.saddam}$`));
});

test("Vada has the Email permission but no mailbox: a plain empty state", async ({ page }) => {
  await signIn(page, "vada");
  await nav(page).getByRole("link", { name: /^Email/ }).click();
  await expect(main(page).getByText("No mailbox has been given to you yet")).toBeVisible();
  await expect(main(page).getByText("A Super Admin sets this up in Settings > Email Services.")).toBeVisible();
  await expect(page.getByTestId("mail-badge")).toHaveCount(0);
});

test("Super Admin: Settings › Email Services › Lark Mail shows the API's truth; the password is never kept", async ({ page }) => {
  await signIn(page, "owner");
  await page.goto("/settings");
  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Email services" }).click();
  await expect(page).toHaveURL(/\/settings\/email-services$/);
  await expect(page.getByRole("heading", { name: "Lark Mail" })).toBeVisible();
  await expect(main(page).getByText("imap.larksuite.com : 993 · SSL")).toBeVisible();
  await expect(main(page).getByText("smtp.larksuite.com : 465 · SSL")).toBeVisible();
  await expect(main(page).getByText(/Use the Lark app authorization code/).first()).toBeVisible();
  const boxes = page.getByRole("region", { name: "Mailboxes" }).getByRole("listitem");
  await expect(boxes).toHaveCount(3);
  for (const b of await boxes.all()) await expect(b.getByText("Paused").first()).toBeVisible();
  await expect(main(page).getByText("Connected", { exact: true })).toHaveCount(0);

  const info = boxes.filter({ hasText: `info.${fx.tag}@` });
  await info.getByRole("button", { name: /Info Mail/ }).click();
  await expect(info.getByText(/whether info@ is its own mailbox, a public mailbox or an alias/)).toBeVisible();
  await expect(info.getByText(/Synthetic test mailbox: imported from a local stand-in, not Lark/)).toBeVisible(); // the API's own words
  await expect(info.getByRole("region", { name: "Who can use this mailbox" })).toContainText("Saddam (synthetic)");
  const pw = info.getByRole("textbox", { name: /app password/i });
  await expect(pw).toHaveAttribute("type", "password");
  await expect(pw).toHaveAttribute("autocomplete", "new-password");
  await expect(pw).toHaveValue("");
  // a made-up password against the (gone) synthetic stand-in: fails, is not saved, field cleared
  await pw.fill("synthetic-not-a-real-password");
  await info.getByRole("button", { name: "Test only (don't save)" }).click();
  await expect(info.getByText("The password was not saved.")).toBeVisible({ timeout: 20_000 });
  await expect(pw).toHaveValue("");
  expect(await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }))).not.toContain("synthetic-not-a-real-password");
  await expect(info.getByText("Paused").first()).toBeVisible();
  await axe(page);

  // the owner holds mail.use too, but was given no mailbox
  await page.goto("/email");
  await expect(main(page).getByText("No mailbox has been given to you yet")).toBeVisible();
});

test("a regular Admin gets no Email Services entry and a not-allowed screen on its address", async ({ page }) => {
  await signIn(page, "andy");
  await page.goto("/settings");
  await expect(page.getByRole("link", { name: "Email services" })).toHaveCount(0);
  await page.goto("/settings/email-services");
  await expect(main(page).getByText("Only a Super Admin can open Email Services")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Lark Mail" })).toHaveCount(0);
  const res = await page.request.get("/api/v1/mail/admin/accounts");
  expect(res.status()).toBe(403);
});

test("tablet: icon rail with a mailbox menu; phone: one column, folders drawer, floating Compose", async ({ page }) => {
  await page.setViewportSize({ width: 834, height: 1112 });
  await signIn(page, "saddam");
  await page.goto("/email");
  await expect(rows(page).first()).toBeVisible();
  await page.getByRole("button", { name: /^Mailbox: Saddam Mail/ }).click();
  await page.getByRole("menuitemradio", { name: /Info Mail/ }).click();
  await expect(page).toHaveURL(new RegExp(`account=${fx.accounts.info}`));

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/email");
  await openRow(page, "Delivery Tuesday — Northside Market");
  await expect(reading(page).getByRole("heading", { name: "Delivery Tuesday — Northside Market" })).toBeVisible();
  await expect(list(page)).toHaveCount(0);
  const extra = await page.evaluate(() => {
    const m = document.getElementById("main");
    const doc = document.scrollingElement as HTMLElement;
    return Math.max(doc.scrollWidth - doc.clientWidth, m ? m.scrollWidth - m.clientWidth : 0);
  });
  expect(extra).toBeLessThanOrEqual(1);
  await actions(page).getByRole("button", { name: "Back to list" }).click();
  await expect(list(page)).toBeVisible();
  await page.getByRole("button", { name: "Folders and mailboxes" }).click();
  await expect(folders(page).getByRole("button", { name: "Sent" })).toBeVisible();
  await folders(page).getByRole("button", { name: "Sent" }).click();
  await expect(rows(page).first()).toContainText("Re: ");
  await page.getByRole("button", { name: "Compose" }).click();
  await expect(page.getByRole("dialog", { name: "New message" })).toBeVisible();
  await page.getByRole("dialog", { name: "New message" }).getByRole("button", { name: "Save and close" }).click();
  await noDeleteAnywhere(page);
});
