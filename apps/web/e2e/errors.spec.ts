import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect, test, type Page } from "@playwright/test";

// App-wide error handling (docs/operations/ERROR_CATALOG.md): every failure says what happened,
// whether anything was saved and what to do next; nothing is a dead end.
type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };
const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

const ORDERS = /\/api\/v1\/sales-orders(\?|$)/;

async function signIn(page: Page, email = fx.users.owner) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

test("a server error explains itself, shows a support reference and recovers on Try again", async ({ page }) => {
  await signIn(page);
  await page.route(ORDERS, (r) =>
    r.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ error: { code: "INTERNAL", message: "x", request_id: "abcdef12-3456-7890" } }) }),
  );
  await page.goto("/orders");
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("Couldn't load orders");
  await expect(alert).toContainText("unexpected problem");
  await expect(alert).toContainText("abcdef12");
  await expect(alert).not.toContainText("INTERNAL");
  await page.unroute(ORDERS);
  await alert.getByRole("button", { name: "Try again" }).click();
  await expect(page.getByText("Couldn't load orders")).toHaveCount(0);
});

test("the API being down (no answer) says so in plain words", async ({ page }) => {
  await signIn(page);
  await page.route(ORDERS, (r) => r.fulfill({ status: 502, contentType: "text/html", body: "<html>Bad Gateway</html>" }));
  await page.goto("/orders");
  await expect(page.getByRole("alert")).toContainText("server is down or restarting");
  await expect(page.getByRole("alert")).not.toContainText("Bad Gateway");
});

test("no permission: says to ask an admin, no pointless retry", async ({ page }) => {
  await signIn(page);
  await page.route(ORDERS, (r) =>
    r.fulfill({ status: 403, contentType: "application/json", body: JSON.stringify({ error: { code: "FORBIDDEN", message: "You do not have permission for this action", request_id: "r-403" } }) }),
  );
  await page.goto("/orders");
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("Ask an owner or admin");
  await expect(alert.getByRole("button", { name: "Try again" })).toHaveCount(0);
});

test("offline: a banner says so, loads explain it, and everything resumes when back", async ({ page, context }) => {
  await signIn(page);
  await page.goto("/customers");
  await expect(page.getByRole("heading", { level: 1, name: "Customers" })).toBeVisible();
  await context.setOffline(true);
  await expect(page.getByTestId("offline-banner")).toContainText("You're offline");
  await page.getByLabel("Search name or reference").fill("Island");
  await expect(page.getByRole("alert")).toContainText("isn't connected to the internet");
  await expect(page.getByLabel("Search name or reference")).toHaveValue("Island");
  await context.setOffline(false);
  await expect(page.getByText("Back online")).toBeVisible();
  await page.getByRole("alert").getByRole("button", { name: "Try again" }).click();
  await expect(page.getByRole("list", { name: /^Customers/ }).getByRole("link", { name: /Island Grocers/ })).toBeVisible();
});

test("session expiry: sign in again over the page, typed text kept, same page", async ({ page }) => {
  await signIn(page);
  await page.goto("/customers");
  await expect(page.getByRole("heading", { level: 1, name: "Customers" })).toBeVisible();
  // The sign-in ends elsewhere (another tab signs out, or it times out).
  const status = await page.evaluate(async () => {
    const csrf = sessionStorage.getItem("pt.csrf") ?? "";
    return (await fetch("/api/v1/auth/logout", { method: "POST", headers: { "X-CSRF-Token": csrf } })).status;
  });
  expect(status).toBeLessThan(300);
  await page.getByLabel("Search name or reference").fill("Kingston");
  const dialog = page.getByTestId("session-expired");
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("Your sign-in has ended");
  await expect(dialog).toContainText(fx.users.owner);
  // wrong password: told plainly, dialog stays
  await dialog.getByLabel("Password").fill("not-the-password");
  await dialog.getByRole("button", { name: "Sign in" }).click();
  await expect(dialog.getByRole("alert")).toContainText("incorrect");
  await dialog.getByLabel("Password").fill(fx.password);
  await dialog.getByRole("button", { name: "Sign in" }).click();
  await expect(dialog).toBeHidden();
  expect(new URL(page.url()).pathname).toBe("/customers");
  await expect(page.getByLabel("Search name or reference")).toHaveValue("Kingston");
});

test("a version conflict on save explains it and the latest loads", async ({ page }) => {
  await signIn(page);
  await page.goto("/customers");
  await page.getByRole("list", { name: /^Customers/ }).getByRole("link", { name: /Island Grocers/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Island Grocers" })).toBeVisible();
  await page.route(/\/api\/v1\/customers\/[^/?]+$/, (r) =>
    r.request().method() === "PATCH"
      ? r.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ error: { code: "VERSION_CONFLICT", message: "Customer changed; reload and retry", request_id: "r-409" } }) })
      : r.fallback(),
  );
  await page.getByLabel("Phone").fill("416-555-0199");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByRole("alert")).toContainText("Someone else changed this customer");
  await expect(page.getByRole("alert")).toContainText("nothing was saved yet");
  await expect(page.getByLabel("Phone")).toHaveValue("416-555-0199");
});

test("unknown addresses inside the app never show a blank page", async ({ page }) => {
  await signIn(page);
  await page.goto("/orders/00000000-0000-0000-0000-000000000000");
  await expect(page.getByRole("alert")).toContainText("Couldn't load this order");
  await expect(page.getByRole("alert")).toContainText(/doesn't exist any more|deleted|Trash/);
});

test("a screen whose code can't be downloaded shows a reload button, not a blank page", async ({ page }) => {
  await page.route(/\/src\/pages\/Reports\.tsx/, (r) => r.abort());
  await signIn(page);
  await page.goto("/reports");
  // one automatic reload is tried first (an update may have been installed); then it explains
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("This screen couldn't be loaded", { timeout: 15_000 });
  await expect(alert.getByRole("button", { name: "Reload page" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeVisible();
});
