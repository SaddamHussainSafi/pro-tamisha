import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Owner, 2026-11-11: "change Picking sheet to packing sheet … like this [the paper PACKING LIST] in
// preview and while printing … it should print all the available pages … 2 prints by default".
type Sheet = { id: string; code: string; lines?: number };
type Fixture = { password: string; users: { owner: string; warehouse: string }; one: Sheet; many: Sheet };

const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/packing_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);
const SHOTS = "screenshots";
mkdirSync(SHOTS, { recursive: true });

async function signIn(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

async function axe(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
}

/** Pages in a PDF: ours (/Type /Pages ... /Count n) or Chrome's own print output (one /Type /Page per page). */
const pageCount = (pdf: Buffer) => {
  const t = pdf.toString("latin1");
  const n = /\/Type\s*\/Pages\b[^>]*?\/Count\s+(\d+)/.exec(t)?.[1] ?? /\/Count\s+(\d+)[^>]*?\/Type\s*\/Pages\b/.exec(t)?.[1];
  return n ? Number(n) : (t.match(/\/Type\s*\/Page(?![s\w])/g) ?? []).length;
};

test("old /picking links open the packing sheet; the sidebar says Packing", async ({ page }) => {
  await signIn(page, fx.users.warehouse);
  await page.goto(`/picking/${fx.one.id}`);
  await expect(page).toHaveURL(new RegExp(`/packing/${fx.one.id}$`));
  await expect(page.getByRole("heading", { name: `Packing sheet ${fx.one.code}` })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Packing", exact: true })).toHaveAttribute("href", "/packing");
  await page.goto("/picking");
  await expect(page).toHaveURL(/\/packing$/);
  await expect(page.getByRole("heading", { name: "Packing", exact: true })).toBeVisible();
  await expect(page.getByRole("radio", { name: "To pack" }).or(page.getByRole("button", { name: "To pack" })).first()).toBeVisible();
});

test("the paper sheet looks like the office PACKING LIST", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.goto(`/packing/${fx.many.id}`);
  await page.getByRole("group", { name: "Sheet view" }).getByRole("button", { name: "Paper sheet" }).click();
  await expect(page).toHaveURL(/view=paper/);
  const paper = page.getByRole("article", { name: `Packing sheet ${fx.many.code}, paper layout` });
  await expect(paper.getByText("TAMISHA TRADING INC.")).toBeVisible();
  await expect(paper.getByText("PACKING LIST", { exact: true })).toBeVisible();
  await expect(paper.getByRole("columnheader")).toHaveText(["Sales Order Number", "Date", "CHK", "QTY", "UNITS", "PRODUCT", "Batch Number", "REMARKS"]);
  await expect(paper.getByRole("cell", { name: fx.many.code.replace(/-P1$/, "") })).toBeVisible();
  await expect(paper.getByText("CUSTOMER NAME :")).toBeVisible();
  await expect(paper.getByText("Synthetic Grocer No. 7934 - Mississauga")).toBeVisible();
  await expect(paper.getByText("EMPLOYEE NAME :")).toBeVisible();
  await expect(paper.getByText("MISSING GOODS :")).toBeVisible();
  await expect(paper.getByRole("img", { name: `QR code ${fx.many.code}` })).toBeVisible();
  await expect(paper.getByRole("table", { name: "Packing list lines" }).locator("tbody tr")).toHaveCount(fx.many.lines ?? 0);
  await expect(paper.getByTestId("paper-BFR")).toContainText("CASE-30");
  await expect(paper.getByText("coming")).toBeVisible();
  // 45 lines: 44 counted (boxes + pieces) and one by weight
  await expect(paper.getByTestId("paper-total")).not.toHaveText("");
  await expect(paper.getByText("+ 12.5 LB by weight")).toBeVisible();
  await axe(page);
  await page.screenshot({ path: `${SHOTS}/packing-sheet-paper-45-lines-1440.png`, fullPage: true });

  await page.goto(`/packing/${fx.one.id}?view=paper`);
  await expect(page.getByRole("article", { name: /paper layout/ }).getByRole("table", { name: "Packing list lines" }).locator("tbody tr")).toHaveCount(1);
  await page.screenshot({ path: `${SHOTS}/packing-sheet-paper-1-line-1440.png`, fullPage: true });
});

test("Print gets the PDF with 2 copies of every page", async ({ page }) => {
  await signIn(page, fx.users.warehouse);
  await page.goto(`/packing/${fx.many.id}`);
  const pdf = page.waitForResponse((r) => r.url().includes(`/pick-attempts/${fx.many.id}/sheet.pdf?copies=2`));
  await page.getByRole("button", { name: "Print (2 copies)" }).click();
  const res = await pdf;
  expect(res.status()).toBe(200);
  expect(res.headers()["content-type"]).toBe("application/pdf");
  const body = await res.body();
  expect(pageCount(body)).toBe(4); // 45 lines + total = 2 pages per copy, 2 copies
  await expect(page.locator("iframe#packing-print-frame")).toHaveAttribute("src", /^blob:/);
  await expect(page.getByRole("alert")).toHaveCount(0);

  const one = await page.request.get(`/api/v1/pick-attempts/${fx.one.id}/sheet.pdf?copies=2`);
  expect(pageCount(await one.body())).toBe(2); // 1 page per copy
});

test("Ctrl/Cmd+P prints only the sheet, twice, on every page it needs", async ({ page }) => {
  await signIn(page, fx.users.owner);
  await page.goto(`/packing/${fx.many.id}`);
  await expect(page.getByRole("heading", { name: `Packing sheet ${fx.many.code}` })).toBeVisible();
  await page.emulateMedia({ media: "print" });
  await expect(page.getByRole("navigation", { name: "Primary" })).toBeHidden();
  await expect(page.getByRole("heading", { name: `Packing sheet ${fx.many.code}` })).toBeHidden();
  const copies = page.getByTestId("packing-print").getByTestId("packing-paper");
  await expect(copies).toHaveCount(2);
  await expect(copies.first()).toBeVisible();
  await expect(copies.nth(1).locator(".pk-lines tbody tr")).toHaveCount(fx.many.lines ?? 0);
  const printed = await page.pdf({ format: "Letter", preferCSSPageSize: true, path: "test-results/cmdp-45-lines.pdf" });
  expect(pageCount(printed)).toBeGreaterThanOrEqual(4); // more than one page per copy, 2 copies
  await page.goto(`/packing/${fx.one.id}`);
  await expect(page.getByTestId("packing-print").getByText(fx.one.code, { exact: true })).toHaveCount(2); // print media: only the copies show
  const short = await page.pdf({ format: "Letter", preferCSSPageSize: true, path: "test-results/cmdp-1-line.pdf" });
  expect(pageCount(short)).toBe(2);
});

test("the warehouse still records packed quantities", async ({ page }) => {
  await signIn(page, fx.users.warehouse);
  await page.goto(`/packing/${fx.one.id}?view=paper`);
  await page.getByRole("group", { name: "Sheet view" }).getByRole("button", { name: "Record packing" }).click();
  await expect(page).not.toHaveURL(/view=paper/);
  const row = page.getByTestId("pick-BFR");
  await row.getByLabel("Packed BFR").fill("2");
  await expect(row.getByText("Packed", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Save quantities" }).click();
  await expect(page.getByText("Quantities saved.")).toBeVisible();
  await axe(page);
});

test("at 390 px the paper sheet scrolls inside its frame, not the page", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, fx.users.owner);
  await page.goto(`/packing/${fx.one.id}?view=paper`);
  await expect(page.getByTestId("packing-paper").first()).toBeVisible();
  const extra = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(extra).toBeLessThanOrEqual(0);
});
