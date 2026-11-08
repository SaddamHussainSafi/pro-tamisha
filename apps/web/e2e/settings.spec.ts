import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { totp } from "./totp";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

test.describe.configure({ mode: "serial" });

const hire = `hire.${Date.now()}@example.test`;
let tempPw = "";

async function signIn(page: Page, email: string, password = fx.password) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}

test("owner adds a user, changes the role, disables and enables them", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.getByRole("button", { name: "Set up authenticator" }).click();
  const secret = (await page.getByTestId("mfa-secret").textContent()) ?? "";
  await page.getByLabel("6-digit code").fill(totp(secret));
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

  await page.getByRole("link", { name: /Settings/ }).click();
  await expect(page.getByRole("heading", { name: "My account" })).toBeVisible();
  // My account: wrong current password is refused.
  await page.getByLabel("Current password").fill("not-my-password");
  await page.getByLabel("New password", { exact: true }).fill("Another-Long-Pass-77");
  await page.getByLabel("New password again").fill("Another-Long-Pass-77");
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByRole("alert")).toHaveText("Your current password is not right.");
  await axe(page);

  await page.getByRole("navigation", { name: "Settings sections" }).getByRole("link", { name: "Users" }).click();
  const grid = page.getByRole("table", { name: /^Users/ });
  await expect(grid.getByRole("row", { name: /\(you\)/ })).toBeVisible();
  await page.getByRole("button", { name: "Add user" }).click();
  const form = page.getByRole("form", { name: "Add user" });
  await form.getByLabel("Name").fill("Nadia New");
  await form.getByLabel("Email (they sign in with it)").fill(hire);
  await form.getByLabel("Role").selectOption({ label: "Sales" });
  tempPw = await form.getByLabel("Temporary password").inputValue();
  expect(tempPw).toHaveLength(16);
  await form.getByRole("button", { name: "Add user" }).click();
  await expect(page.getByRole("status")).toHaveText("Nadia New can now sign in.");
  await expect(page.getByTestId("temp-password")).toContainText(tempPw);
  const row = grid.getByRole("row", { name: /Nadia New/ });
  await expect(row).toContainText("Sales");
  await axe(page);

  await row.getByRole("button", { name: "Edit Nadia New" }).click();
  await page.getByRole("form", { name: "Edit Nadia New" }).getByLabel("Role").selectOption({ label: "Warehouse" });
  await page.getByRole("form", { name: "Edit Nadia New" }).getByRole("button", { name: "Save" }).click();
  await expect(page.getByRole("status")).toContainText("signed out so the new role applies");
  await expect(row).toContainText("Warehouse");

  await row.getByRole("button", { name: "Disable Nadia New" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Disable" }).click();
  await expect(row).toContainText("Disabled");
  await row.getByRole("button", { name: "Enable Nadia New" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Enable" }).click();
  await expect(row).toContainText("Active");
});

test("the new user signs in and changes their own password", async ({ page }) => {
  await signIn(page, hire, tempPw);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.getByRole("link", { name: /Settings/ }).click();
  await expect(page.getByRole("navigation", { name: "Settings sections" })).toHaveCount(0); // no Users tab
  await page.getByLabel("Current password").fill(tempPw);
  await page.getByLabel("New password", { exact: true }).fill("Nadia-Own-Pass-2026");
  await page.getByLabel("New password again").fill("Nadia-Own-Pass-2026");
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByRole("status")).toHaveText("Password changed. Other devices were signed out.");
  await page.goto("/settings/users"); // not allowed: shows My account
  await expect(page.getByRole("heading", { name: "My account" })).toBeVisible();
  await expect(page.getByRole("table", { name: /^Users/ })).toHaveCount(0);
  await axe(page);
});
