import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { totp } from "./totp";

// User management & authentication (2026-11-09): invitations, reset links, optional two-step
// sign-in, roles matrix, security & sessions, responsive settings screens.

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };
const API_DIR = resolve(import.meta.dirname, "../../api");
const env = { ...process.env, PYTHONPATH: "src" };

const fx: Fixture = JSON.parse(execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], { cwd: API_DIR, encoding: "utf8", env }));

/** The latest link token emailed to `email` (the dev mailbox is the email_messages table). */
function mailedToken(email: string, kind: "accept-invite" | "reset-password"): string {
  const out = execFileSync("uv", ["run", "python", "scripts/e2e_mailbox.py", email, kind], { cwd: API_DIR, encoding: "utf8", env });
  return (JSON.parse(out) as { token: string }).token;
}

test.describe.configure({ mode: "serial" });

const hire = `hire.${Date.now()}@example.test`;
const firstPw = "Nadia-First-Pass-2026";
const resetPw = "Nadia-Reset-Pass-2026";
let secret = "";
let recovery = "";

async function signIn(page: Page, email: string, password = fx.password) {
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

async function signOut(page: Page) {
  await page.getByRole("button", { name: "Sign out" }).first().click();
  await expect(page).toHaveURL(/\/login$/);
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}

/** The visible notice (the page also has an empty live region for toasts). */
const status = (page: Page) => page.getByRole("status").filter({ hasText: /\S/ });
const sections = (page: Page) => page.getByRole("navigation", { name: "Settings sections" });

test("Super Admin invites a user, changes the role, suspends and reactivates them", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible(); // no forced two-step
  await page.getByRole("link", { name: /Settings/ }).click();
  await expect(page.getByRole("heading", { name: "My account" })).toBeVisible();
  // My account: wrong current password is refused.
  await page.getByLabel("Current password").fill("not-my-password");
  await page.getByLabel("New password", { exact: true }).fill("Another-Long-Pass-77");
  await page.getByLabel("New password again").fill("Another-Long-Pass-77");
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByRole("alert")).toHaveText("Your current password is not right.");
  await axe(page);

  await sections(page).getByRole("link", { name: "User management" }).click();
  await page.getByRole("group", { name: "View" }).getByRole("button", { name: "List" }).click();
  const grid = page.getByRole("table", { name: /^Users/ });
  await expect(grid.getByRole("row", { name: /\(you\)/ })).toBeVisible();

  await page.getByRole("button", { name: "Add user" }).click();
  const form = page.getByRole("form", { name: "Add user" });
  await form.getByRole("button", { name: "Send invitation" }).click();
  await expect(form.getByRole("alert")).toHaveText("Enter the person's name.");
  await form.getByLabel("Name").fill("Nadia New");
  await form.getByLabel("Email (they sign in with it)").fill(hire);
  await form.getByLabel("Role").selectOption({ label: "Sales" });
  await expect(form.getByText("What Sales can do")).toBeVisible();
  await axe(page);
  await form.getByRole("button", { name: "Send invitation" }).click();
  await expect(status(page)).toContainText(`Invitation sent to ${hire}`);
  const row = grid.getByRole("row", { name: /Nadia New/ });
  await expect(row).toContainText("Sales");
  await expect(row).toContainText("Invited");

  // Search and filters.
  await page.getByRole("searchbox", { name: "Search name or email" }).fill(hire);
  await expect(grid.getByRole("row")).toHaveCount(2); // header + Nadia
  await page.getByRole("group", { name: "Status filter" }).getByRole("button", { name: /Suspended/ }).click();
  await expect(grid.getByRole("row", { name: /Nadia New/ })).toHaveCount(0);
  await page.getByRole("group", { name: "Status filter" }).getByRole("button", { name: /Invited/ }).click();
  await expect(row).toBeVisible();

  await row.getByRole("button", { name: "Manage Nadia New" }).click();
  const edit = page.getByRole("form", { name: "Edit Nadia New" });
  await edit.getByLabel("Role").selectOption({ label: "Warehouse" });
  await edit.getByRole("button", { name: "Save" }).click();
  await page.getByRole("dialog", { name: /Change Nadia New's role to Warehouse/ }).getByRole("button", { name: "Save" }).click();
  await expect(status(page)).toContainText("signed out so the new role applies");
  await expect(row).toContainText("Warehouse");

  await row.getByRole("button", { name: "Manage Nadia New" }).click();
  await page.getByRole("button", { name: "Suspend", exact: true }).click();
  await page.getByRole("dialog", { name: "Suspend Nadia New?" }).getByRole("button", { name: "Suspend" }).click();
  await page.getByRole("group", { name: "Status filter" }).getByRole("button", { name: /All/ }).click();
  await expect(row).toContainText("Suspended");
  await row.getByRole("button", { name: "Manage Nadia New" }).click();
  await page.getByRole("button", { name: "Reactivate", exact: true }).click();
  await page.getByRole("dialog", { name: "Reactivate Nadia New?" }).getByRole("button", { name: "Reactivate" }).click();
  await expect(row).toContainText("Invited");
  // Suspending killed the first link: send a fresh one.
  await row.getByRole("button", { name: "Manage Nadia New" }).click();
  await page.getByRole("button", { name: "Resend invitation" }).click();
  await page.getByRole("dialog", { name: "Send Nadia New a new invitation?" }).getByRole("button", { name: "Send invitation" }).click();
  await expect(status(page)).toContainText(`A new invitation was emailed to ${hire}`);
});

test("the invited person sets a password from the email link and signs in", async ({ page }) => {
  const token = mailedToken(hire, "accept-invite");
  await page.goto("/accept-invite#token=bad-token-that-is-long-enough-xyz");
  await expect(page.getByRole("alert")).toContainText("invalid, already used or expired");
  await page.goto(`/accept-invite#token=${token}`);
  await expect(page.getByRole("heading", { name: "Welcome to Pro Trading" })).toBeVisible();
  await expect(page.getByText(hire)).toBeVisible();
  await expect(page).not.toHaveURL(/token=/); // dropped from the address bar
  await page.getByLabel("Password", { exact: true }).fill("short");
  await page.getByLabel("Password again").fill("short");
  await page.getByRole("button", { name: "Set password" }).click();
  await expect(page.getByRole("alert")).toHaveText("Use at least 12 characters.");
  await page.getByLabel("Password", { exact: true }).fill(firstPw);
  await page.getByLabel("Password again").fill(firstPw);
  await axe(page);
  await page.getByRole("button", { name: "Set password" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(status(page)).toHaveText("Your password is set. Sign in to start.");
  await signIn(page, hire, firstPw);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.getByRole("link", { name: /Settings/ }).click();
  for (const admin of ["User management", "Roles & permissions", "Security & sessions"])
    await expect(page.getByRole("link", { name: admin })).toHaveCount(0); // Warehouse: no admin tabs
  await expect(page.getByRole("definition").filter({ hasText: "Warehouse" }).first()).toBeVisible();
  await page.goto("/settings/users"); // not allowed: back to My account
  await expect(page).toHaveURL(/\/settings$/);
  await expect(page.getByRole("table", { name: /^Users/ })).toHaveCount(0);
  await axe(page);
});

test("forgot password: a generic answer, then a single-use reset link", async ({ page }) => {
  await page.goto("/login");
  await page.getByRole("link", { name: "Forgot password?" }).click();
  await expect(page.getByRole("heading", { name: "Forgot your password?" })).toBeVisible();
  await page.getByLabel("Email").fill("nobody.at.all@example.test");
  await page.getByRole("button", { name: "Send reset link" }).click();
  const generic = await status(page).textContent();
  await page.goto("/forgot-password");
  await expect(page.getByRole("heading", { name: "Forgot your password?" })).toBeVisible();
  await page.getByLabel("Email").fill(hire);
  await page.getByRole("button", { name: "Send reset link" }).click();
  await expect(status(page)).toHaveText(generic ?? ""); // same words: no account enumeration
  await axe(page);
  const token = mailedToken(hire, "reset-password");
  await page.goto(`/reset-password#token=${token}`);
  await expect(page.getByRole("heading", { name: "Choose a new password" })).toBeVisible();
  await page.getByLabel("New password", { exact: true }).fill(resetPw);
  await page.getByLabel("New password again").fill(resetPw);
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(status(page)).toHaveText("Password changed. Sign in with your new password.");
  await page.goto(`/reset-password#token=${token}`); // used once already
  await expect(page.getByRole("alert")).toContainText("invalid, already used or expired");
  await signIn(page, hire, firstPw);
  await expect(page.getByRole("alert")).toHaveText("Email or password is incorrect.");
  await signIn(page, hire, resetPw);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
});

test("optional two-step sign-in: turn on, sign in with a code, then with a recovery code", async ({ page }) => {
  await signIn(page, hire, resetPw);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.goto("/settings");
  const card = page.getByRole("region", { name: "Two-step sign-in" });
  await expect(card.getByText("Off", { exact: true })).toBeVisible();
  await card.getByRole("button", { name: "Turn on two-step sign-in" }).click();
  await card.getByLabel("Your password", { exact: true }).fill(resetPw);
  await card.getByRole("button", { name: "Continue" }).click();
  secret = (await card.getByTestId("mfa-secret").textContent()) ?? "";
  await card.getByLabel("6-digit code").fill(totp(secret));
  await card.getByRole("button", { name: "Turn on" }).click();
  const codes = card.getByRole("list", { name: "Recovery codes" }).getByRole("listitem");
  await expect(codes).toHaveCount(10);
  recovery = ((await codes.first().textContent()) ?? "").trim();
  await axe(page);
  await card.getByLabel("I stored these codes somewhere safe").check();
  await card.getByRole("button", { name: "I saved my codes" }).click();
  await expect(card.getByText("On", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("list", { name: "Active sessions" })).toContainText("This device");
  await expect(page.getByRole("list", { name: "Your recent sign-in activity" })).toContainText("Turned two-step sign-in on");
  await signOut(page);

  await signIn(page, hire, resetPw);
  await expect(page).toHaveURL(/\/mfa$/);
  await page.getByLabel("6-digit code").fill("000000");
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("alert")).toHaveText("That code is wrong or was already used.");
  await page.getByLabel("6-digit code").fill(totp(secret, Date.now() + 30_000)); // next step: no replay
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible(); // signed in
  await expect(page).not.toHaveURL(/\/mfa$/);
  await signOut(page);

  await signIn(page, hire, resetPw);
  await page.getByRole("button", { name: "Lost your phone? Use a recovery code" }).click();
  await page.getByLabel("Recovery code").fill(recovery);
  await axe(page);
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
  await expect(page).not.toHaveURL(/\/mfa$/);
});

test("Super Admin reads the permission matrix and security events, and signs a session out", async ({ page, browser }) => {
  const other = await browser.newPage();
  await signIn(other, fx.users.sales);
  await expect(other.getByRole("heading", { name: "Overview" })).toBeVisible();

  await signIn(page, fx.users.owner);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.goto("/settings/roles");
  await expect(page.getByRole("list", { name: "Roles" }).getByRole("listitem")).toHaveCount(6);
  const matrix = page.getByRole("table", { name: /Which role holds which permission/ });
  await expect(matrix.getByRole("columnheader", { name: "Super Admin" })).toBeVisible();
  await expect(matrix.getByRole("columnheader", { name: "Viewer" })).toBeVisible();
  await expect(matrix.getByRole("row", { name: /Create\/disable employees/ }).getByRole("img", { name: "Super Admin: yes" })).toBeVisible();
  await expect(matrix.getByRole("row", { name: /Create\/disable employees/ }).getByText("Admin: no")).toBeAttached();
  await axe(page);

  await sections(page).getByRole("link", { name: "Security & sessions" }).click();
  await expect(page.getByText("Optional: each person can turn it on in My account.")).toBeVisible();
  const sessions = page.getByRole("list", { name: "Active sessions" });
  await expect(sessions).toContainText("E2E sales");
  await sessions.getByRole("button", { name: /Sign out E2E sales/ }).first().click();
  await page.getByRole("dialog", { name: "Sign out E2E sales?" }).getByRole("button", { name: "Sign out" }).click();
  await expect(status(page)).toContainText("was signed out of that device");
  const events = page.getByRole("list", { name: "Security events" });
  await expect(events).toContainText("Signed in");
  await page.getByRole("group", { name: "Event type" }).getByRole("button", { name: "Account changes" }).click();
  await expect(events).toContainText("Invited a user");
  await axe(page);

  await other.reload();
  await expect(other).toHaveURL(/\/login$/); // that session was revoked
  await other.close();
});

test("settings and sign-in screens fit a phone (no sideways scroll)", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of ["/login", "/forgot-password"]) {
    await page.goto(path);
    await expect(page.getByRole("heading").first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  }
  await signIn(page, fx.users.owner);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  for (const path of ["/settings", "/settings/users", "/settings/roles", "/settings/security"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: "Settings" })).toBeVisible();
    await expect(page.getByRole("status", { name: "Loading" })).toHaveCount(0);
    await expect(page.getByRole("region").first()).toBeVisible();
    const extra = await page.evaluate(() => {
      const main = document.getElementById("main");
      const doc = document.scrollingElement as HTMLElement;
      return Math.max(doc.scrollWidth - doc.clientWidth, main ? main.scrollWidth - main.clientWidth : 0);
    });
    expect(extra, `${path} scrolls sideways`).toBeLessThanOrEqual(1);
  }
  await page.goto("/settings/users");
  await page.getByRole("button", { name: "Add user" }).click();
  const box = await page.getByRole("dialog", { name: "Add user" }).boundingBox();
  expect(box && box.x >= 0 && box.x + box.width <= 390).toBeTruthy();
});
