import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// The animated scanner, end to end against the real API and the real Celery worker (whatever OCR
// engine the dev server runs). A fresh synthetic company with synthetic PDFs: 1 page, 10 pages, a
// broken one, plus three scans set up in states that can't be produced on demand on a shared worker
// (an engine outage that gave up after retries, a reading nobody picked up, an empty feeder).
// Every state asserted here is what the server reported; nothing in the UI is timed.
const dir = mkdtempSync(join(tmpdir(), "pt-scan-ui-"));
const fx = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/scan_ui_fixture.py", "--dir", dir, "--scenarios"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
) as {
  password: string;
  users: { sales: string };
  files: { one_page: string; ten_page: string; broken: string };
  scans: { outage: string; stalled: string; missing: string; waiting: string; scanning: string; matching: string; retrying: string };
};
const onePage = readFileSync(fx.files.one_page);
const tenPage = readFileSync(fx.files.ten_page);
const broken = readFileSync(fx.files.broken);
const SHOTS = process.env.SCAN_UI_SHOTS ?? "screenshots/scan-ui";
mkdirSync(SHOTS, { recursive: true });

test.describe.configure({ mode: "serial" });
test.afterAll(() => rmSync(dir, { recursive: true, force: true }));

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.users.sales);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}

const hero = (page: Page) => page.getByTestId("scan-processing").first();
const shot = (page: Page, name: string) => hero(page).screenshot({ path: `${SHOTS}/${name}.png`, animations: "disabled" });

/** Remember every picture the scanner showed (scene + detail line), in order. */
async function record(page: Page) {
  await page.evaluate(() => {
    const w = window as unknown as { __scenes: string[] };
    w.__scenes = [];
    const look = () => {
      const el = document.querySelector("[data-testid=scan-processing]");
      if (!el) return;
      const s = `${el.getAttribute("data-scene")}|${el.querySelector("[data-testid=scan-detail]")?.textContent ?? ""}`;
      if (w.__scenes[w.__scenes.length - 1] !== s) w.__scenes.push(s);
    };
    new MutationObserver(look).observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
  });
}
const scenes = (page: Page) => page.evaluate(() => (window as unknown as { __scenes: string[] }).__scenes);
// "connecting" (waiting for the server's first answer) is not a step, so it is left out.
const ORDER = ["uploading", "queued", "reading", "matching", "ready"];

async function upload(page: Page, name: string, buffer: Buffer) {
  await page.goto("/scan");
  await expect(page.getByRole("heading", { name: "Scan Center" })).toBeVisible();
  await record(page);
  await page.getByTestId("scan-upload").setInputFiles({ name, mimeType: "application/pdf", buffer });
}

async function stepStates(page: Page) {
  return hero(page)
    .locator("li[data-step]")
    .evaluateAll((lis) => lis.map((li) => `${li.getAttribute("data-step")}:${li.getAttribute("data-state")}`));
}

// First: these states were set up by the fixture moments ago (the stall check would close the
// queued ones after OCR_STALL_SECONDS).
test("in-between states read from the server: waiting for the scanner, scanning, matching, retrying", async ({ page }) => {
  await signIn(page);
  const cases: [keyof typeof fx.scans, string, string, RegExp][] = [
    ["waiting", "retrieving", "Waiting for the scanner", /picks up the request/],
    ["scanning", "retrieving", "Retrieving PDF", /feeding the pages/],
    ["matching", "matching", "Matching products", /^3 pages read$/],
    ["retrying", "queued", "Trying again", /Attempt 2 of 5/],
  ];
  for (const [key, scene, title, detail] of cases) {
    await page.goto(`/scan/${fx.scans[key]}`);
    await expect(hero(page)).toHaveAttribute("data-scene", scene);
    await expect(hero(page).getByTestId("scan-title")).toHaveText(title);
    await expect(hero(page).getByTestId("scan-detail")).toHaveText(detail);
    await axe(page);
    await shot(page, `00-${key}`);
  }
  expect(await stepStates(page)).toEqual(["RETRIEVING:done", "UPLOADING:done", "EXTRACTING:active", "MATCHING:waiting", "REVIEW:waiting"]);
  // Narrow screens: the steps stack, the picture scales.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/scan/${fx.scans.matching}`);
  await expect(hero(page)).toHaveAttribute("data-scene", "matching");
  await page.screenshot({ path: `${SHOTS}/00-matching-390.png`, fullPage: false, animations: "disabled" });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("uploading shows the browser's own upload progress", async ({ page }) => {
  test.setTimeout(150_000);
  await signIn(page);
  await page.goto("/scan");
  await expect(page.getByRole("heading", { name: "Scan Center" })).toBeVisible();
  // Hold the upload at the network layer so the state can be seen (localhost is too fast).
  let release = () => {};
  const held = new Promise<void>((ok) => (release = ok));
  await page.route("**/api/v1/scans/uploads**", async (route) => {
    await held;
    await route.continue();
  });
  const copy = Buffer.concat([tenPage, Buffer.from(`\n% upload check ${Date.now()}\n`)]);
  await page.getByTestId("scan-upload").setInputFiles({ name: "slow-upload.pdf", mimeType: "application/pdf", buffer: copy });
  await expect(hero(page)).toHaveAttribute("data-scene", "uploading");
  await expect(hero(page).getByTestId("scan-detail")).toHaveText(/^\d{1,3}% · slow-upload\.pdf$/);
  expect(await stepStates(page)).toEqual(["RETRIEVING:done", "UPLOADING:active", "EXTRACTING:waiting", "MATCHING:waiting", "REVIEW:waiting"]);
  await shot(page, "00-uploading");
  release();
  await expect(hero(page)).toHaveAttribute("data-scene", "ready", { timeout: 120_000 });
});

test("a 1-page PDF goes through every step to review, and the review shows the original with uncertain items marked", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page);
  await upload(page, "one-page.pdf", onePage);
  await expect(hero(page)).toHaveAttribute("data-scene", "ready", { timeout: 90_000 });
  await expect(hero(page).getByTestId("scan-title")).toHaveText("Review ready");
  await expect(hero(page).getByTestId("scan-detail")).toHaveText(/^\d+ lines? · \d+ to check$/);
  expect(await stepStates(page)).toEqual(["RETRIEVING:done", "UPLOADING:done", "EXTRACTING:done", "MATCHING:done", "REVIEW:done"]);
  // The scanner went forward through the steps the server reported, never backwards.
  const seen = (await scenes(page)).map((s) => s.split("|")[0] ?? "");
  const ranks = seen.map((s) => ORDER.indexOf(s)).filter((r) => r >= 0);
  expect(ranks).toEqual([...ranks].sort((a, b) => a - b));
  expect(seen.at(-1)).toBe("ready");
  expect(seen.some((s) => s === "queued" || s === "reading" || s === "matching")).toBe(true);
  await expect(page.getByRole("heading", { name: "Scan completed successfully" })).toBeVisible();
  await axe(page);
  await shot(page, "01-one-page-review-ready");

  // Review screen: the lines use the full width (no full-page scan, owner 2026-11-11); hovering a
  // line shows its row of the original in the magnifier; unclear quantity and unknown product are
  // marked.
  await page.getByRole("link", { name: "Scan details" }).first().click();
  await hero(page).getByRole("link", { name: "Review now" }).click();
  await expect(page.getByRole("heading", { name: /Review scan/ })).toBeVisible();
  await expect(page.getByRole("img", { name: /^Original scan .*page 1$/ })).toHaveCount(0);
  const lines = page.locator("[data-testid^='ocr-line-']");
  await expect(lines).toHaveCount(3);
  const plantain = lines.filter({ hasText: "PLG" });
  await plantain.hover();
  await expect(page.getByTestId("scan-lens")).toBeVisible();
  await expect(page.getByTestId("scan-lens-spot")).toBeVisible();
  const mango = lines.filter({ hasText: /Mango/ });
  await expect(plantain.locator("[data-uncertain=quantity]")).toBeVisible();
  await expect(mango.locator("[data-uncertain=product]").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Create draft order" })).toBeDisabled(); // approval unchanged
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/02-review-original-and-fields.png`, fullPage: true, animations: "disabled" });
  // Fields are editable: enter the unclear quantity from the sheet.
  await plantain.getByRole("button", { name: /^Enter line/ }).click();
  const form = page.getByRole("form", { name: "Correct line" });
  await form.getByLabel("Quantity").fill("3");
  await form.getByRole("button", { name: "Save line" }).click();
  await expect(plantain).toContainText("Corrected");
  await expect(page.getByTestId("doc-flag")).toHaveCount(1);
});

test("a 10-page PDF shows page n of 10 from the reader's own events, page by page", async ({ page }) => {
  test.setTimeout(240_000);
  await signIn(page);
  await upload(page, "ten-page.pdf", tenPage);
  await expect(hero(page)).toHaveAttribute("data-scene", "reading", { timeout: 60_000 });
  // Reduced motion (the suite's default): the beam is drawn still, nothing loops.
  const beam = page.locator(".sc-beam-line").first();
  await expect(beam).toBeVisible();
  expect(await beam.evaluate((el) => getComputedStyle(el).animationName)).toBe("none");
  await expect(hero(page).getByRole("group", { name: "Pages" }).getByRole("button")).toHaveCount(10);
  await axe(page);
  await shot(page, "03-ten-page-reading");
  await expect(hero(page)).toHaveAttribute("data-scene", "ready", { timeout: 180_000 });

  const seen = await scenes(page);
  const pagesSeen = seen.map((s) => /^reading\|Page (\d+) of 10$/.exec(s)?.[1]).filter(Boolean).map(Number);
  expect(pagesSeen.length).toBeGreaterThan(1);
  expect(pagesSeen).toEqual([...pagesSeen].sort((a, b) => a - b));
  expect(seen.some((s) => s.startsWith("matching|"))).toBe(true);

  // Page-level results: quantities were written on pages 1, 4, 7 and 10 only.
  const strip = hero(page).getByRole("group", { name: "Pages" });
  for (const n of [1, 4, 7, 10]) await expect(strip.getByRole("button", { name: new RegExp(`^Page ${n}: read, 1 line`) })).toBeVisible();
  await expect(strip.getByRole("button", { name: /^Page 2: read, 0 lines$/ })).toBeVisible();
  await strip.getByRole("button", { name: /^Page 7:/ }).click();
  await expect(strip.getByRole("button", { name: /^Page 7:/ })).toHaveAttribute("aria-pressed", "true");
  await expect(hero(page).getByTestId("page-detail")).toContainText("Page 7");
  await expect(hero(page).getByTestId("page-detail")).toContainText("1 to check");
  await axe(page);
  await shot(page, "04-ten-page-review-ready-page-7");
});

test.describe("with motion allowed", () => {
  test.use({ reducedMotion: "no-preference" });
  test("the beam sweeps only while a page is being read", async ({ page }) => {
    test.setTimeout(240_000);
    await signIn(page);
    const copy = Buffer.concat([tenPage, Buffer.from(`\n% motion check ${Date.now()}\n`)]);
    await upload(page, "ten-page-motion.pdf", copy);
    await expect(hero(page)).toHaveAttribute("data-scene", "reading", { timeout: 60_000 });
    expect(await page.locator(".sc-beam-line").first().evaluate((el) => getComputedStyle(el).animationName)).toBe("sc-beam-local");
    await expect(hero(page)).toHaveAttribute("data-scene", "ready", { timeout: 180_000 });
    await expect(page.locator(".sc-beam-line")).toHaveCount(0);
  });
});

test("an engine outage shows the failure, and Read again reads it for real", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page);
  await page.goto(`/scan/${fx.scans.outage}`);
  await expect(hero(page)).toHaveAttribute("data-scene", "failed");
  await expect(hero(page).getByTestId("scan-title")).toHaveText("Couldn't read the scan");
  await expect(hero(page).getByTestId("scan-detail")).toContainText("couldn't be reached");
  expect(await stepStates(page)).toEqual(["RETRIEVING:done", "UPLOADING:done", "EXTRACTING:failed", "MATCHING:waiting", "REVIEW:waiting"]);
  await axe(page);
  await shot(page, "05-failed-engine-outage");
  await hero(page).getByRole("button", { name: "Read again" }).click();
  await expect(hero(page)).toHaveAttribute("data-scene", /queued|reading|matching|ready/);
  await expect(hero(page)).toHaveAttribute("data-scene", "ready", { timeout: 90_000 });
  await shot(page, "06-failed-then-read-again-ready");
});

test("a broken PDF fails in the real engine and says what to do", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page);
  await upload(page, "broken.pdf", broken);
  await expect(hero(page)).toHaveAttribute("data-scene", "failed", { timeout: 90_000 });
  await expect(hero(page).getByTestId("scan-detail")).toContainText("couldn't be opened");
  await expect(hero(page).getByRole("button", { name: "Read again" })).toBeVisible();
});

test("a reading nobody picked up times out, and can be read again", async ({ page }) => {
  test.setTimeout(120_000);
  await signIn(page);
  await page.goto(`/scan/${fx.scans.stalled}`);
  await expect(hero(page)).toHaveAttribute("data-scene", "timeout");
  await expect(hero(page).getByTestId("scan-title")).toHaveText("Timed out");
  await expect(hero(page).getByTestId("scan-detail")).toContainText("No reader picked this scan up");
  await axe(page);
  await shot(page, "07-timeout");
  await hero(page).getByRole("button", { name: "Read again" }).click();
  await expect(hero(page)).toHaveAttribute("data-scene", "ready", { timeout: 90_000 });
});

test("the same file again is recognised as already on file", async ({ page }) => {
  test.setTimeout(60_000);
  await signIn(page);
  await upload(page, "one-page-again.pdf", onePage);
  await expect(page.getByRole("heading", { name: "Already on file" })).toBeVisible({ timeout: 30_000 });
  await expect(hero(page).getByTestId("scan-title")).toHaveText("Already scanned");
  await expect(hero(page)).toHaveAttribute("data-scene", "ready");
  await axe(page);
  await shot(page, "08-duplicate");
});

test("an empty feeder shows the missing document and how to start over", async ({ page }) => {
  await signIn(page);
  await page.goto(`/scan/${fx.scans.missing}`);
  await expect(hero(page)).toHaveAttribute("data-scene", "missing");
  await expect(hero(page).getByTestId("scan-title")).toHaveText("No document received");
  await expect(hero(page).getByTestId("scan-detail")).toContainText("Put the pages in the feeder");
  await expect(hero(page).getByRole("button", { name: "New scan" })).toBeVisible();
  expect(await stepStates(page)).toEqual(["RETRIEVING:failed", "UPLOADING:waiting", "EXTRACTING:waiting", "MATCHING:waiting", "REVIEW:waiting"]);
  await axe(page);
  await shot(page, "09-missing-document");
});
