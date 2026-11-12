import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { totp } from "./totp";

type Fixture = {
  password: string;
  users: { owner: string; warehouse: string; sales: string };
  scan: { id: string; job_no: string };
};

// Fresh company: customers Island Grocers (C-1001, confirmed order) and Kingston Kitchen
// (C-1002, draft order), a warehouse user and a sales user, and an order-sheet scan of Island
// Grocers that was read twice (two OCR reads use it).
const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py", "--scan-with-reads"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

test.describe.configure({ mode: "serial" });

let secret = ""; // the owner's authenticator, once two-step sign-in is on
let lastStep = 0; // last TOTP time step used to sign in

async function signIn(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  if (email === fx.users.owner && secret) {
    await expect(page).toHaveURL(/\/mfa$/);
    // Each 30 s code works once: use the next step, waiting for a fresh one if it was just used.
    const step = () => Math.floor((Date.now() + 30_000) / 30_000);
    while (step() <= lastStep) await page.waitForTimeout(1_000);
    lastStep = step();
    await page.getByLabel("6-digit code").fill(totp(secret, Date.now() + 30_000));
    await page.getByRole("button", { name: "Verify" }).click();
  }
  // Sign-in returns to the page last opened (e.g. /orders after signing out there).
  await expect(page).not.toHaveURL(/\/login$/);
  await expect(page.getByRole("button", { name: "Sign out" }).first()).toBeVisible();
}

async function signOut(page: Page) {
  await page.getByRole("button", { name: "Sign out" }).first().click();
  await expect(page).toHaveURL(/\/login$/);
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}

async function turnOnTwoStep(page: Page) {
  await page.goto("/settings");
  const card = page.getByRole("region", { name: "Two-step sign-in" });
  await card.getByRole("button", { name: "Turn on two-step sign-in" }).click();
  await card.getByLabel("Your password", { exact: true }).fill(fx.password);
  await card.getByRole("button", { name: "Continue" }).click();
  secret = (await card.getByTestId("mfa-secret").textContent()) ?? "";
  await card.getByLabel("6-digit code").fill(totp(secret));
  await card.getByRole("button", { name: "Turn on" }).click();
  await card.getByLabel("I stored these codes somewhere safe").check();
  await card.getByRole("button", { name: "I saved my codes" }).click();
  await expect(card.getByText("On", { exact: true })).toBeVisible();
  await page.reload();
}

const DELETE_ANY = /^(Delete|Request delete)\b/;
let draftOrder = "";

test("staff without edit rights see no Delete; sales can only ask for one", async ({ page }) => {
  await signIn(page, fx.users.warehouse);
  await expect(page.getByRole("link", { name: "Trash" })).toHaveCount(0);
  await page.goto("/orders");
  await expect(page.getByRole("heading", { level: 1, name: "Orders" })).toBeVisible();
  await expect(page.getByText("Kingston Kitchen").first()).toBeVisible();
  await expect(page.getByRole("button", { name: DELETE_ANY })).toHaveCount(0);
  await signOut(page);

  await signIn(page, fx.users.sales);
  await expect(page.getByRole("link", { name: "Trash" })).toHaveCount(0);
  await page.getByRole("link", { name: "Customers", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Customers" })).toBeVisible();
  await expect(page.getByRole("link", { name: /Island Grocers/ })).toBeVisible();
  await expect(page.getByRole("button", { name: DELETE_ANY })).toHaveCount(0);

  // The draft order: sales may ask the Super Admin to delete it.
  await page.goto("/orders");
  await page.getByRole("button", { name: "All", exact: true }).click();
  await expect(page.getByRole("cell", { name: "Draft" })).toHaveCount(1);
  const ask = page.getByRole("button", { name: /^Request delete Order SO-/ });
  await expect(ask).toHaveCount(1); // only the draft, not the confirmed or packed ones
  draftOrder = ((await ask.getAttribute("aria-label")) ?? "").replace("Request delete ", "");
  await ask.click();
  const dialog = page.getByRole("dialog", { name: `Ask to delete “${draftOrder}”?` });
  await dialog.getByLabel("Reason (optional)").fill("Customer called to cancel");
  await axe(page);
  await dialog.getByRole("button", { name: "Send request" }).click();
  const sent = page.getByRole("dialog", { name: "Request sent" });
  await expect(sent).toBeVisible();
  await sent.getByRole("button", { name: "Close", exact: true }).click();
  await signOut(page);
});

test("Super Admin moves a customer to Trash, undoes, restores and deletes forever", async ({ page }) => {
  await signIn(page, fx.users.owner);

  // Without two-step sign-in the Delete dialog only explains how to turn it on.
  await page.getByRole("link", { name: "Customers", exact: true }).click();
  await page.getByRole("button", { name: "Delete Island Grocers" }).click();
  const twoStep = page.getByRole("dialog", { name: "Turn on two-step sign-in" });
  await expect(twoStep).toBeVisible();
  await twoStep.getByRole("button", { name: "Close", exact: true }).click();

  await turnOnTwoStep(page);
  await page.getByRole("link", { name: "Customers", exact: true }).click();

  // Before confirming, the dialog names what goes with it and what only loses its link.
  await page.getByRole("button", { name: "Delete Island Grocers" }).click();
  let dialog = page.getByRole("dialog", { name: "Move “Island Grocers (synthetic)” to Trash?" });
  await expect(dialog.getByTestId("delete-goes-with")).toContainText("1 sales order (SO-");
  await expect(dialog.getByTestId("delete-unlinked")).toContainText(`2 OCR reads (${fx.scan.job_no})`);
  await expect(dialog.getByText("Restoring it brings all of this back.")).toBeVisible();
  await axe(page);
  await dialog.getByRole("button", { name: "Cancel" }).click();

  // A new, unused customer goes to the Trash from its page; Undo brings it back.
  await page.getByRole("button", { name: "New customer" }).click();
  await page.getByLabel("Name", { exact: true }).fill("Trash Test Market");
  await page.getByRole("button", { name: "Add customer" }).click();
  await expect(page).toHaveURL(/\/customers\/[0-9a-f-]{36}$/);
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "Move “Trash Test Market” to Trash?" });
  await dialog.getByRole("button", { name: "Move to Trash" }).click();
  await expect(page).toHaveURL(/\/customers$/);
  await expect(page.getByText("moved to Trash.")).toBeVisible();
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(page.getByText("restored.")).toBeVisible();
  await expect(page.getByRole("link", { name: /Trash Test Market/ })).toBeVisible();

  // From the list this time, then restore it from the Trash.
  await page.getByRole("button", { name: "Delete Trash Test Market" }).click();
  await page.getByRole("dialog", { name: "Move “Trash Test Market” to Trash?" }).getByRole("button", { name: "Move to Trash" }).click();
  await expect(page.getByRole("link", { name: /Trash Test Market/ })).toHaveCount(0);
  await page.getByRole("link", { name: "Trash", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Trash" })).toBeVisible();
  const row = page.getByRole("row", { name: /Trash Test Market/ });
  await expect(row).toBeVisible();
  await expect(row.getByRole("cell", { name: "30 days" })).toBeVisible();
  await axe(page);
  await page.getByRole("button", { name: "Restore Trash Test Market" }).click();
  await expect(page.getByText("“Trash Test Market” is back.")).toBeVisible();
  await expect(page.getByText("Trash is empty")).toBeVisible();

  // Delete forever.
  await page.goto("/customers");
  await page.getByRole("button", { name: "Delete Trash Test Market" }).click();
  await page.getByRole("dialog", { name: "Move “Trash Test Market” to Trash?" }).getByRole("button", { name: "Move to Trash" }).click();
  await page.getByRole("link", { name: "Trash", exact: true }).click();
  await page.getByRole("button", { name: "Delete Trash Test Market forever" }).click();
  await page.getByRole("dialog", { name: "Delete “Trash Test Market” forever?" }).getByRole("button", { name: "Delete forever" }).click();
  await expect(page.getByText("“Trash Test Market” was deleted forever.")).toBeVisible();
  await expect(page.getByText("Trash is empty")).toBeVisible();
});

test("Super Admin deletes a scan that OCR reads use, and restores it whole", async ({ page }) => {
  test.setTimeout(90_000); // may wait for a fresh two-step code
  await signIn(page, fx.users.owner);
  const label = `Scan ${fx.scan.job_no}`;
  const reads = async () => {
    const r = await page.request.get("/api/v1/ocr/extractions?status=NEEDS_REVIEW");
    expect(r.ok()).toBe(true);
    const body = (await r.json()) as { items: { scan_job_id: string }[] };
    return body.items.filter((x) => x.scan_job_id === fx.scan.id).length;
  };
  expect(await reads()).toBe(1); // the live read; the first one was superseded

  await page.goto(`/scan/${fx.scan.id}`);
  await expect(page.getByRole("heading", { level: 1, name: label })).toBeVisible();
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: `Move “${label}” to Trash?` });
  const goes = dialog.getByTestId("delete-goes-with");
  await expect(goes).toContainText(`2 OCR reads (${fx.scan.job_no})`);
  await expect(goes).toContainText("1 scanned file");
  await axe(page);
  await page.screenshot({ path: "screenshots/trash-cascade-scan-1440.png" });
  await dialog.getByRole("button", { name: "Move to Trash" }).click();
  await expect(page).toHaveURL(/\/scan$/);
  await expect(page.getByText("moved to Trash.")).toBeVisible();
  expect(await reads()).toBe(0);

  await page.getByRole("link", { name: "Trash", exact: true }).click();
  await page.getByRole("button", { name: `Restore ${label}` }).click();
  await expect(page.getByText(`“${label}” is back.`)).toBeVisible();
  expect(await reads()).toBe(1);
  await page.goto(`/scan/${fx.scan.id}`);
  await expect(page.getByRole("heading", { level: 1, name: label })).toBeVisible();
});

test("Super Admin sees the delete request and dismisses it", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.getByRole("link", { name: "Trash", exact: true }).click();
  await page.getByRole("button", { name: /^Delete requests/ }).click();
  await expect(page.getByText("Customer called to cancel")).toBeVisible();
  await axe(page);
  await page.getByRole("button", { name: `Dismiss request for ${draftOrder}` }).click();
  await page.getByRole("dialog", { name: `Dismiss the request for “${draftOrder}”?` }).getByRole("button", { name: "Dismiss" }).click();
  await expect(page.getByText("Nothing was deleted.")).toBeVisible();
  await expect(page.getByText("No delete requests")).toBeVisible();
});

test("Super Admin deletes a notification and brings it back with Undo", async ({ page }) => {
  test.setTimeout(90_000); // may wait for a fresh two-step code
  await signIn(page, fx.users.owner);
  await page.goto("/activity?view=notifications");
  const title = `Delete requested: ${draftOrder}`;
  const card = page.getByRole("article", { name: new RegExp(title) });
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: `Delete “${title}”` }).click();
  const dialog = page.getByRole("dialog", { name: `Move “${title}” to Trash?` });
  await axe(page);
  await dialog.getByRole("button", { name: "Move to Trash" }).click();
  await expect(card).toHaveCount(0);
  await page.getByRole("button", { name: "Undo", exact: true }).click();
  await expect(page.getByText("restored.")).toBeVisible();
  await expect(card).toBeVisible();
});
