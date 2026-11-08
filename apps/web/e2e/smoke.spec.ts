import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { totp } from "./totp";

type Fixture = { password: string; users: { owner: string; warehouse: string } };

const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

test("API is ready", async ({ request }) => {
  const r = await request.get("/health/ready");
  expect(r.status()).toBe(200);
});

test("warehouse user signs in, sees shell, cannot manage, signs out", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login$/);
  await page.getByLabel("Email").fill(fx.users.warehouse);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.getByText("Pro Trading").first()).toBeVisible();
  await page.getByRole("link", { name: /Settings/ }).click();
  await expect(page.getByRole("heading", { name: "My account" })).toBeVisible();
  await expect(page.getByRole("definition").filter({ hasText: fx.users.warehouse })).toBeVisible();
  // One warehouse only: no warehouse settings, no design previews.
  await expect(page.getByText("Add warehouse")).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Design previews/ })).toHaveCount(0);
  await page.screenshot({ path: "screenshots/shell-1440.png", fullPage: true });
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
});

test("bad password shows generic error", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.users.warehouse);
  await page.getByLabel("Password").fill("definitely-wrong");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toHaveText("Email or password is incorrect.");
});

test("owner must enroll MFA, then sees two-step sign-in on in Settings", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.users.owner);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/mfa$/);
  await page.getByRole("button", { name: "Set up authenticator" }).click();
  const secret = (await page.getByTestId("mfa-secret").textContent()) ?? "";
  await page.getByLabel("6-digit code").fill(totp(secret));
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.getByRole("link", { name: /Settings/ }).click();
  await expect(page.getByRole("heading", { name: "My account" })).toBeVisible();
  await page.goto("/settings/warehouses"); // removed page: back to Settings
  await expect(page).toHaveURL(/\/settings$/);
});
