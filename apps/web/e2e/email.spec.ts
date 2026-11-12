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
const folders = (page: Page) => page.getByRole("navigation", { name: "Folders" });
const reading = (page: Page) => page.getByRole("article", { name: "Email" });
const main = (page: Page) => page.locator("#main");
async function noDeleteAnywhere(page: Page) {
  // There is no Trash in this module and nothing deletes, trashes or moves an email.
  await expect(main(page).getByRole("button", { name: /delete|trash|bin\b|move to/i })).toHaveCount(0);
  await expect(main(page).getByRole("link", { name: /delete|trash/i })).toHaveCount(0);
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
  await expect(page.getByRole("heading", { name: "Email", level: 1 })).toBeVisible();
  await expect(main(page).getByText(`andy.${fx.tag}@tamishatrading.com`)).toBeVisible();
  await expect(main(page).getByText("Andy Mail (synthetic)").first()).toBeVisible();
  // honest about the synthetic, paused stand-in: never "Connected"
  await expect(main(page).getByText("Paused").first()).toBeVisible();
  await expect(main(page).getByText(/synthetic test mailbox/)).toBeVisible();
  await expect(main(page).getByText("Connected", { exact: true })).toHaveCount(0);
  // one mailbox: no mailbox picker, nothing of Saddam's or Info's
  await expect(page.getByRole("combobox", { name: "Mailbox" })).toHaveCount(0);
  await expect(main(page).getByText(/Info Mail|Saddam Mail/)).toHaveCount(0);
  await expect(rows(page).first()).toContainText("Photo of the damaged pallet");
  await noDeleteAnywhere(page);
});

test("folders switch the list (Inbox, Sent, Starred, Drafts) and the URL", async ({ page }) => {
  await signIn(page, "andy");
  await page.goto("/email");
  await expect(folders(page).getByRole("button", { name: /Inbox/ })).toHaveAttribute("aria-current", "page");
  await expect(folders(page).getByRole("button")).toHaveText([/Inbox/, "Starred", "Sent", "Drafts", "Archive", "Junk"]);
  await folders(page).getByRole("button", { name: "Sent" }).click();
  await expect(page).toHaveURL(/\/email\?account=[0-9a-f-]{36}&folder=[0-9a-f-]{36}$/);
  await expect(rows(page).first()).toContainText("Re: ");
  await expect(rows(page).filter({ hasNotText: "Re: " })).toHaveCount(0);
  await folders(page).getByRole("button", { name: "Junk" }).click();
  await expect(main(page).getByText("No emails here")).toBeVisible();
  await folders(page).getByRole("button", { name: /Inbox/ }).click();
  await expect(rows(page).first()).toContainText("Photo of the damaged pallet");
  // keyset pages load as the list scrolls (49 inbox emails, 50 per page: no more page; the list ends)
  await expect(rows(page)).toHaveCount(49);
});

test("open an email: sandboxed body, Load images bar, PDF and picture preview, download", async ({ page }) => {
  await signIn(page, "andy");
  await page.goto("/email");
  await row(page, "Delivery Tuesday — Northside Market").getByRole("button").first().click();
  await expect(reading(page).getByRole("heading", { name: "Delivery Tuesday — Northside Market" })).toBeVisible();
  const frame = reading(page).locator('iframe[title="Email message"]');
  const sandbox = (await frame.getAttribute("sandbox")) ?? "";
  expect(sandbox).not.toContain("allow-scripts");
  expect(sandbox).not.toContain("allow-same-origin");
  await expect(page.frameLocator('iframe[title="Email message"]').getByText("Basmati rice 10 kg")).toBeVisible();
  // remote pictures stay off until asked for
  expect(await frame.getAttribute("srcdoc")).not.toMatch(/<img[^>]*\ssrc="https:/);
  await reading(page).getByRole("button", { name: "Load images" }).click();
  await expect(reading(page).getByRole("button", { name: "Load images" })).toHaveCount(0);
  await expect.poll(async () => (await reading(page).locator('iframe[title="Email message"]').getAttribute("srcdoc")) ?? "").toMatch(/<img[^>]*\ssrc="https:\/\/tracker\.example\/open\.gif"/);

  // PDF attachment: preview inline + download as a file
  const pdf = reading(page).getByRole("region", { name: "Attachments" });
  await expect(pdf.getByText(/^PO-\d+\.pdf$/)).toBeVisible();
  await pdf.getByRole("button", { name: /Preview PO-/ }).click();
  const dlg = page.getByRole("dialog", { name: /PO-\d+\.pdf/ });
  await expect(dlg.locator("iframe")).toHaveAttribute("src", /inline=true/);
  await dlg.getByRole("button", { name: "Close" }).click();
  const href = (await pdf.getByRole("link", { name: /Download PO-/ }).getAttribute("href")) ?? "";
  const res = await page.request.get(href);
  expect(res.status()).toBe(200);
  expect(res.headers()["content-disposition"]).toMatch(/^attachment/);

  // picture attachment previews as a picture
  await row(page, "Photo of the damaged pallet").getByRole("button").first().click();
  await reading(page).getByRole("button", { name: "Preview pallet.png" }).click();
  const img = page.getByRole("dialog", { name: "pallet.png" }).getByRole("img", { name: "pallet.png" });
  await expect(img).toBeVisible();
  await expect.poll(() => img.evaluate((i: HTMLImageElement) => i.naturalWidth)).toBeGreaterThan(0);
  await page.keyboard.press("Escape");
  await noDeleteAnywhere(page);
  await axe(page);
});

test("read and star are kept per person; starred mail shows in Starred", async ({ page }) => {
  await signIn(page, "andy");
  await page.goto("/email");
  // the pallet photo was opened by the previous test: take the next unread email
  const inbox = folders(page).getByRole("button", { name: /Inbox/ });
  await expect(inbox).toContainText("9");
  await expect(page.getByTestId("mail-badge")).toHaveText("9");
  const unread = rows(page).filter({ hasText: "(unread)" }).first();
  const id = (await unread.getAttribute("id")) ?? "";
  const first = page.locator(`[id="${id}"]`);
  const subject = (await first.locator("span.block").first().textContent()) ?? "";
  await first.getByRole("button").first().click();
  await expect(first).not.toContainText("(unread)");
  await expect(inbox).toContainText("8");
  await expect(page.getByTestId("mail-badge")).toHaveText("8");

  // star from the reading pane with "s", and from the row
  await page.locator("body").press("s");
  await expect(reading(page).getByRole("button", { name: "Starred" })).toHaveAttribute("aria-pressed", "true");
  await row(page, "Price list question (Spice Route Foods)").getByRole("button", { name: /^Star / }).click();
  await folders(page).getByRole("button", { name: "Starred" }).click();
  await expect(rows(page)).toHaveCount(2);
  await page.reload();
  await expect(rows(page)).toHaveCount(2); // kept on the server, not just on screen
  await row(page, subject).getByRole("button", { name: /^Unstar / }).click();
  await row(page, "Price list question").getByRole("button", { name: /^Unstar / }).click();
  await page.reload();
  await expect(main(page).getByText("No emails here")).toBeVisible();
});

test("search is live, limited to this mailbox, highlights words and works from the keyboard", async ({ page }) => {
  await signIn(page, "andy");
  await page.goto("/email");
  const box = page.getByPlaceholder("Search Andy Mail");
  await expect(rows(page).first()).toBeVisible();
  await page.locator("body").press("/");
  await expect(box).toBeFocused();
  await box.fill("Northside");
  await expect(main(page).getByText("Search results")).toBeVisible();
  await expect(rows(page).first().locator("mark").first()).toHaveText(/Northside/i);
  const n = await rows(page).count();
  expect(n).toBeGreaterThan(3);
  await expect(rows(page).filter({ hasNotText: /Northside/i })).toHaveCount(0);
  await box.press("ArrowDown");
  await box.press("Enter");
  await expect(reading(page).getByRole("heading", { level: 2 })).toContainText(/Northside/);
  // filters: attachments only
  await page.getByRole("button", { name: "Search filters" }).click();
  await page.getByRole("group", { name: "Search filters" }).getByLabel("Has attachments").check();
  await expect.poll(() => rows(page).count()).toBeLessThan(n);
  await expect(rows(page).first()).toContainText(/Northside/);
  await page.getByRole("button", { name: "Clear search" }).click();
  await expect(main(page).getByText("Search results")).toHaveCount(0);
  await box.fill("zzqxnothing");
  await expect(main(page).getByText("No emails match")).toBeVisible();
});

test("compose with Cc/Bcc and a file: draft autosaves, Send queues once, the status is honest", async ({ page }) => {
  await signIn(page, "andy");
  await page.goto("/email");
  await expect(rows(page).first()).toBeVisible();
  await page.locator("body").press("c");
  const dlg = page.getByRole("dialog", { name: "New email" });
  await expect(dlg).toBeVisible();
  await expect(dlg.getByRole("combobox", { name: "From" })).toHaveValue(fx.accounts.andy);
  await dlg.getByLabel("To", { exact: true }).fill("maria@greenleaf-grocers.example,");
  await expect(dlg.getByRole("group", { name: "To recipients" })).toContainText("maria@greenleaf-grocers.example");
  await dlg.getByRole("button", { name: "Cc / Bcc" }).click();
  await dlg.getByLabel("Cc", { exact: true }).fill("not-an-address");
  await dlg.getByLabel("Cc", { exact: true }).press("Enter");
  await expect(dlg.getByText("1 address is not valid")).toBeVisible();
  await expect(dlg.getByRole("button", { name: "Send" })).toBeDisabled();
  await dlg.getByRole("button", { name: "Remove not-an-address from Cc" }).click();
  await dlg.getByLabel("Cc", { exact: true }).fill("tom@northside-market.example");
  await dlg.getByLabel("Cc", { exact: true }).press("Enter");
  await dlg.getByLabel("Bcc", { exact: true }).fill("Priya Shah <priya@spiceroute.example>;");
  await dlg.getByLabel("Subject").fill("E2E synthetic order confirmation");
  await dlg.getByRole("textbox", { name: "Message" }).click();
  await page.keyboard.type("Confirmed for Tuesday. (synthetic test, never delivered)");
  await dlg.getByLabel("Files to attach").setInputFiles({ name: "order-note.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n") });
  await expect(dlg.getByRole("region", { name: "Attachments" })).toContainText("order-note.pdf");
  await expect(dlg.getByText(/Draft saved/)).toBeVisible({ timeout: 10_000 });
  await dlg.getByRole("button", { name: "Save and close" }).click();
  await expect(dlg).toBeHidden();

  // the draft is listed in Drafts, above any Lark drafts
  await folders(page).getByRole("button", { name: "Drafts" }).click();
  const saved = page.getByRole("region", { name: "Drafts saved in Pro Trading" });
  await expect(saved).toContainText("E2E synthetic order confirmation");
  await saved.getByRole("button", { name: /E2E synthetic order confirmation/ }).click();
  const draft = page.getByRole("dialog", { name: "Draft" });
  await expect(draft.getByLabel("Subject")).toHaveValue("E2E synthetic order confirmation");
  await expect(draft.getByRole("group", { name: "Bcc recipients" })).toContainText("Priya Shah");
  await expect(draft.getByText("1 file attached earlier")).toBeVisible();

  // Send: the first request is lost on the way; pressing Send again reuses the SAME key
  const keys: string[] = [];
  await page.route("**/api/v1/mail/send", async (route) => {
    keys.push(route.request().headers()["idempotency-key"] ?? "");
    if (keys.length === 1) return route.abort("connectionreset");
    return route.continue();
  });
  await draft.getByRole("button", { name: "Send" }).click();
  await expect(draft.getByRole("alert")).toBeVisible();
  await draft.getByRole("button", { name: "Send" }).click();
  await expect(draft).toBeHidden();
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBeTruthy();
  expect(keys[1]).toBe(keys[0]);
  await page.unroute("**/api/v1/mail/send");
  await expect(main(page).getByText(/is queued for sending/)).toBeVisible();
  await expect(saved).toHaveCount(0); // a sent draft leaves Drafts

  // delivery state as the API reports it (the stand-in SMTP server is gone: never "Sent")
  await page.getByRole("button", { name: /^Outbox:/ }).click();
  const box = page.getByRole("dialog", { name: "Outbox" });
  const item = box.getByRole("listitem").filter({ hasText: "E2E synthetic order confirmation" });
  await expect(item).toHaveCount(1);
  await expect(item.getByText(/^(Queued|Sending|Failed|May have been sent)$/)).toBeVisible();
  await expect(item.getByText("Sent", { exact: true })).toHaveCount(0);
  await box.getByRole("button", { name: "Close" }).click();
  await noDeleteAnywhere(page);
});

test("reply quotes the original and goes to the sender; r / f open from the keyboard", async ({ page }) => {
  await signIn(page, "andy");
  await page.goto("/email");
  await row(page, "Price list question (Spice Route Foods)").getByRole("button").first().click();
  await expect(reading(page).getByRole("heading", { level: 2 })).toHaveText("Price list question (Spice Route Foods)");
  await page.locator("body").press("r");
  const dlg = page.getByRole("dialog", { name: "Reply" });
  await expect(dlg.getByLabel("Subject")).toHaveValue("Re: Price list question (Spice Route Foods)");
  await expect(dlg.getByRole("group", { name: "To recipients" })).toContainText("Priya Shah");
  await expect(dlg.getByRole("textbox", { name: "Message" }).locator("blockquote")).toContainText("Spice Route Foods");
  await expect(dlg.getByRole("combobox", { name: "From" })).toBeDisabled();
  await dlg.getByRole("button", { name: "Discard draft" }).click();
  await expect(dlg).toBeHidden();
  await row(page, "Delivery Tuesday — Northside Market").getByRole("button").first().click();
  await expect(reading(page).getByRole("heading", { level: 2 })).toHaveText("Delivery Tuesday — Northside Market");
  await page.locator("body").press("f");
  const fwd = page.getByRole("dialog", { name: "Forward" });
  await expect(fwd.getByLabel("Subject")).toHaveValue("Fwd: Delivery Tuesday — Northside Market");
  await expect(fwd.getByRole("checkbox", { name: /PO-\d+\.pdf/ })).toBeChecked();
  await fwd.getByRole("button", { name: "Discard draft" }).click();
});

test("Saddam switches between Saddam Mail and Info Mail: list and search follow the mailbox", async ({ page }) => {
  await signIn(page, "saddam");
  await page.goto("/email");
  const picker = main(page).getByRole("combobox", { name: "Mailbox" }).first();
  await expect(picker.locator("option")).toHaveText([/Saddam Mail \(synthetic\)/, /Info Mail \(synthetic\)/]);
  const ids = async () => (await rows(page).evaluateAll((els) => els.map((e) => e.id))).sort();
  await picker.selectOption(fx.accounts.saddam);
  await expect(main(page).getByText(`saddam.${fx.tag}@tamishatrading.com`)).toBeVisible();
  await expect(rows(page)).toHaveCount(49);
  const saddamIds = await ids();
  await page.getByPlaceholder("Search Saddam Mail").fill("Greenleaf");
  await expect(main(page).getByText("Search results")).toBeVisible();
  await row(page, "Maria Lopez").getByRole("button").first().click(); // search covers Sent too
  await expect(page.frameLocator('iframe[title="Email message"]').getByText("Hi Saddam")).toBeVisible();

  await main(page).getByRole("combobox", { name: "Mailbox" }).first().selectOption(fx.accounts.info);
  await expect(page).toHaveURL(new RegExp(`/email\\?account=${fx.accounts.info}$`));
  await expect(main(page).getByText(`info.${fx.tag}@tamishatrading.com`)).toBeVisible();
  // everything was reset: no search, nothing open, the Inbox of Info Mail
  await expect(page.getByPlaceholder("Search Info Mail")).toHaveValue("");
  await expect(reading(page)).toHaveCount(0);
  await expect(rows(page)).toHaveCount(49);
  const infoIds = await ids();
  expect(infoIds.filter((i) => saddamIds.includes(i))).toEqual([]);
  await page.getByPlaceholder("Search Info Mail").fill("Greenleaf");
  await row(page, "Maria Lopez").getByRole("button").first().click(); // search covers Sent too
  await expect(page.frameLocator('iframe[title="Email message"]').getByText("Hi Info")).toBeVisible();

  // a ⌘K search hit: /email?account=…&message=… opens that mailbox and that email, even one
  // that is not on the first page of the list
  const page1 = (await (await page.request.get(`/api/v1/mail/accounts/${fx.accounts.saddam}/messages?limit=60`)).json()) as { items: { id: string; subject: string }[] };
  const old = page1.items[page1.items.length - 1];
  if (!old) throw new Error("fixture has no mail");
  await page.goto(`/email?account=${fx.accounts.saddam}&message=${old.id}`);
  await expect(main(page).getByText(`saddam.${fx.tag}@tamishatrading.com`).first()).toBeVisible();
  await expect(reading(page).getByRole("heading", { name: old.subject })).toBeVisible();
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

test("phone width: one pane at a time with a way back, nothing scrolls sideways", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, "andy");
  await page.goto("/email");
  await row(page, "Delivery Tuesday — Northside Market").getByRole("button").first().click();
  await expect(reading(page).getByRole("heading", { name: "Delivery Tuesday — Northside Market" })).toBeVisible();
  await expect(list(page)).toBeHidden();
  const extra = await page.evaluate(() => {
    const m = document.getElementById("main");
    const doc = document.scrollingElement as HTMLElement;
    return Math.max(doc.scrollWidth - doc.clientWidth, m ? m.scrollWidth - m.clientWidth : 0);
  });
  expect(extra).toBeLessThanOrEqual(1);
  await reading(page).getByRole("button", { name: "Back to the list" }).click();
  await expect(list(page)).toBeVisible();
  await noDeleteAnywhere(page);
});
