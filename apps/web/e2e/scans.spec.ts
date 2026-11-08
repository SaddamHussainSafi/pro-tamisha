import { execFileSync, spawn, type ChildProcess } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { totp } from "./totp";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// Fresh synthetic company. The scanner PC is simulated by the real bridge with its FAKE driver.
const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

const bridgeDir = resolve(import.meta.dirname, "../../bridge");
const stateDir = mkdtempSync(join(tmpdir(), "pt-bridge-"));
const bridgeEnv = {
  ...process.env,
  PT_API_URL: "http://127.0.0.1:8000",
  PT_BRIDGE_STATE: join(stateDir, "state.json"),
  PT_ALLOWED_ORIGINS: "http://localhost:5173",
  PT_SCAN_DRIVER: "fake",
  PT_FAKE_MODE: "ok",
};
let bridge: ChildProcess | null = null;

test.describe.configure({ mode: "serial" });
test.afterAll(() => {
  bridge?.kill();
  rmSync(stateDir, { recursive: true, force: true });
});

async function signIn(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}

async function waitForBridge() {
  for (let i = 0; i < 50; i++) {
    try {
      const r = await fetch("http://127.0.0.1:47615/v1/status", { headers: { Origin: "http://localhost:5173" } });
      if (r.ok) return;
    } catch {
      /* not up yet */
    }
    await new Promise((ok) => setTimeout(ok, 200));
  }
  throw new Error("bridge did not start");
}

test("owner pairs the office scanner PC and approves it with the confirmation code", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.getByRole("button", { name: "Set up authenticator" }).click();
  const secret = (await page.getByTestId("mfa-secret").textContent()) ?? "";
  await page.getByLabel("6-digit code").fill(totp(secret));
  await page.getByRole("button", { name: "Verify" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();

  await page.getByRole("link", { name: /Scan Center/ }).first().click();
  await expect(page.getByRole("heading", { name: "Scan Center" })).toBeVisible();
  await page.getByRole("tab", { name: "Scanners" }).click();
  const add = page.getByRole("form", { name: "Add a scanner PC" });
  // No location field any more (owner request): the scanner is paired to the first warehouse.
  await expect(add.getByLabel("Location")).toHaveCount(0);
  await add.getByLabel("Name", { exact: true }).fill("Front office scanner");
  await add.getByRole("button", { name: "Get pairing code" }).click();
  const cmd = (await page.getByTestId("pair-command").textContent()) ?? "";
  const code = cmd.split(" ").pop() ?? "";
  expect(code).toMatch(/\S{6,}/);

  // On the "scanner PC": pair the bridge, which prints the confirmation code.
  const out = execFileSync("python3", ["-m", "pro_trading_bridge", "pair", code], { cwd: bridgeDir, env: bridgeEnv, encoding: "utf8" });
  const fingerprint = (out.match(/Scanners: (\S+)/) ?? [])[1] ?? "";
  expect(fingerprint).not.toBe("");

  await page.reload();
  const row = page.getByRole("row", { name: /Front office scanner/ });
  await expect(row).toContainText("Waiting for approval");
  await row.getByRole("button", { name: "Approve" }).click();
  const dlg = page.getByRole("dialog");
  await dlg.getByLabel(/Confirmation code/).fill("WRONG-CODE");
  await dlg.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await row.getByRole("button", { name: "Approve" }).click();
  await dlg.getByLabel(/Confirmation code/).fill(fingerprint);
  await dlg.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("status")).toContainText("approved");

  bridge = spawn("python3", ["-m", "pro_trading_bridge", "run"], { cwd: bridgeDir, env: bridgeEnv, stdio: "ignore" });
  await waitForBridge();
  await page.reload();
  await expect(page.getByRole("row", { name: /Front office scanner/ })).toContainText("Online");
  await axe(page);
  await page.screenshot({ path: "screenshots/scanners-1440.png", fullPage: true });
});

test("sales scans a paper order through the bridge and views it", async ({ page }) => {
  await signIn(page, fx.users.sales);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.getByRole("link", { name: /Scan Center/ }).first().click();
  await expect(page.getByRole("heading", { name: "Scan Center" })).toBeVisible();
  // Sales may scan but not manage scanners.
  await expect(page.getByRole("tab", { name: "Scanners" })).toHaveCount(0);
  await expect(page.getByLabel("Scanner")).toContainText("Front office scanner");
  await page.getByLabel(/Note/).fill("Island Grocers fax");
  await page.getByRole("button", { name: "Scan now" }).click();
  await expect(page.getByRole("status")).toContainText("received");
  await expect(page.getByRole("table", { name: /Scans/ })).toContainText("Received");
  await axe(page);
  await page.screenshot({ path: "screenshots/scans-1440.png", fullPage: true });

  await page.getByRole("status").getByRole("link", { name: "Open" }).click();
  await expect(page.getByRole("heading", { name: /^Scan SCN-/ })).toBeVisible();
  await expect(page.getByText("Scanner · Front office scanner")).toBeVisible();
  await expect(page.locator("iframe[title^='Scanned document']")).toBeVisible();
  await axe(page);
  await page.screenshot({ path: "screenshots/scan-detail-1440.png", fullPage: true });
});

test("upload fallback works, a duplicate is recognised, and a scanner fault explains what to do", async ({ page }) => {
  await signIn(page, fx.users.sales);
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await page.getByRole("link", { name: /Scan Center/ }).first().click();
  const png = Buffer.from(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
    "base64",
  );
  const file = { name: "phone-photo.png", mimeType: "image/png", buffer: png };
  // Let the scan list finish its first load so its loading placeholder isn't a second role=status.
  await expect(page.getByRole("heading", { name: "Scan Center" })).toBeVisible();
  await expect(page.getByRole("status", { name: "Loading" })).toHaveCount(0);
  await page.getByTestId("scan-upload").setInputFiles(file);
  await expect(page.getByRole("status")).toContainText("uploaded");
  await page.getByTestId("scan-upload").setInputFiles(file);
  await expect(page.getByRole("status")).toContainText("already uploaded");
  await page.getByTestId("scan-upload").setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hello") });
  await expect(page.getByRole("alert")).toBeVisible();

  // Scanner unplugged: restart the bridge in fault mode.
  bridge?.kill();
  bridge = spawn("python3", ["-m", "pro_trading_bridge", "run"], {
    cwd: bridgeDir,
    env: { ...bridgeEnv, PT_FAKE_MODE: "unplugged" },
    stdio: "ignore",
  });
  await new Promise((ok) => setTimeout(ok, 300));
  await waitForBridge();
  // After an upload the scan card shows the finished scan; start a new one.
  await page.getByRole("button", { name: "New scan" }).click();
  await page.getByRole("button", { name: "Scan now" }).click();
  await expect(page.getByRole("alert")).toContainText(/scanner/i);
  await page.getByRole("button", { name: "Failed" }).click();
  await expect(page.getByRole("table", { name: /Scans/ })).toContainText("Failed");
});
