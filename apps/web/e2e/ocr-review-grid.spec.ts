import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Route } from "@playwright/test";

type Fixture = { password: string; users: { owner: string; warehouse: string; sales: string } };

// The review grid and "Confirm selected", independent of how a reading engine happens to read a
// sheet: the reading itself is served by route mocks (synthetic), everything else is the real app.
const fx: Fixture = JSON.parse(
  execFileSync("uv", ["run", "python", "scripts/e2e_fixture.py"], {
    cwd: resolve(import.meta.dirname, "../../api"),
    encoding: "utf8",
    env: { ...process.env, PYTHONPATH: "src" },
  }),
);

const EX = "00000000-0000-4000-8000-0000000000e1";
const field = (seq: number, sku: string | null, name: string, qty: string | null, unit: string | null, status: string) => ({
  id: `00000000-0000-4000-8000-00000000f00${seq}`,
  seq,
  kind: "LINE",
  label: sku ? `${sku} ${name}` : name,
  raw_text: `${name.toUpperCase()} | Q: ${qty ?? "?"}`,
  read_product_id: sku ? `00000000-0000-4000-8000-0000000000${seq}${seq}` : null,
  read_sku: sku,
  read_unit_code: unit,
  read_qty: qty,
  confidence: status === "OK" ? "0.950" : "0.500",
  status,
  bbox: null,
  detail: null,
  resolution: "NONE",
  final_product_id: null,
  final_sku: null,
  final_product_name: null,
  final_unit_code: null,
  final_qty: null,
  resolved_by_name: null,
  resolved_at: null,
  corrections: [] as unknown[],
});

function reading() {
  const fields = [
    field(1, "CFZ", "Cassava frozen", "12.0000", "BOX", "OK"),
    field(2, "PLG", "Plantain green", "24.0000", "PIECE", "OK"),
    field(3, "RJS", "Jasmine rice", "2.0000", "BOX", "OK"),
    field(4, "PLG", "Plantain green", "3.0000", "BOX", "LOW_CONFIDENCE"),
    field(5, null, "Mango pulp", "4.0000", null, "UNMAPPED"),
  ];
  return {
    id: EX,
    scan_job_id: "00000000-0000-4000-8000-0000000000a1",
    scan_job_no: "SCN-SYNTH-0001",
    document_id: "00000000-0000-4000-8000-0000000000d1",
    document_mime: "application/pdf",
    warehouse_id: "00000000-0000-4000-8000-0000000000b1",
    warehouse_code: "MAIN",
    run_no: 1,
    status: "NEEDS_REVIEW",
    provider: "fake",
    template: "PT-OS-1",
    customer_id: null,
    customer_name: null,
    warnings: [] as string[],
    page_count: 1,
    error_code: null,
    error_detail: null,
    sales_order_id: null,
    sales_order_no: null,
    requested_by_name: null,
    reviewed_by_name: null,
    reviewed_at: null,
    review_note: null,
    created_at: "2026-11-11T12:00:00Z",
    completed_at: "2026-11-11T12:00:20Z",
    field_count: fields.length,
    open_count: fields.length,
    version: 7,
    fields,
    text_blocks: [],
  };
}

async function signIn(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(fx.users.owner);
  await page.getByLabel("Password").fill(fx.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
}

test("review lines in a grid: tick the clear ones, Confirm selected; corrections need no reason", async ({ page }) => {
  const ex = reading();
  const sent: { confirm?: { version: number; field_ids: string[] }; resolve?: Record<string, unknown> } = {};
  const json = (route: Route, body: unknown) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  await page.route(`**/api/v1/ocr/extractions/${EX}`, (route) => json(route, ex));
  await page.route(`**/api/v1/ocr/extractions/${EX}/fields/confirm`, (route) => {
    sent.confirm = route.request().postDataJSON();
    for (const f of ex.fields) {
      if (!sent.confirm?.field_ids.includes(f.id)) continue;
      Object.assign(f, { resolution: "CONFIRMED", final_product_id: f.read_product_id, final_sku: f.read_sku, final_product_name: f.label.slice(4), final_unit_code: f.read_unit_code, final_qty: f.read_qty });
    }
    ex.version += 1;
    ex.open_count = ex.fields.filter((f) => f.resolution === "NONE").length;
    return json(route, ex);
  });
  await page.route(`**/api/v1/ocr/extractions/${EX}/fields/*/resolve`, (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    sent.resolve = body;
    const f = ex.fields.find((x) => route.request().url().includes(x.id));
    if (f) Object.assign(f, { resolution: "CORRECTED", final_product_id: body.product_id, final_sku: "PLG", final_product_name: "Plantain green", final_unit_code: body.unit_code, final_qty: body.qty });
    ex.version += 1;
    return json(route, ex);
  });
  await page.route("**/api/v1/scans/documents/**", (route) => route.fulfill({ status: 404, body: "" }));

  await signIn(page);
  await page.goto(`/scan/review/${EX}`);
  const grid = page.getByRole("table", { name: "Recognized lines" });
  await expect(grid.getByRole("columnheader", { name: "S/N" })).toBeVisible();
  const lines = page.locator("[data-testid^='ocr-line-']");
  await expect(lines).toHaveCount(5);
  // Only clear lines still waiting can be ticked
  await expect(lines.nth(3).getByRole("checkbox")).toHaveCount(0);
  await expect(lines.nth(4).getByRole("checkbox")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Confirm selected" })).toBeDisabled();
  await page.getByRole("checkbox", { name: "Select all clear lines" }).check();
  await page.getByRole("button", { name: "Confirm selected (3)" }).click();
  await expect(page.getByText("3 lines confirmed.")).toBeVisible();
  expect(sent.confirm).toEqual({ version: 7, field_ids: ex.fields.slice(0, 3).map((f) => f.id) });
  for (const i of [0, 1, 2]) await expect(lines.nth(i)).toContainText("Confirmed");
  await expect(page.getByRole("checkbox", { name: "Select all clear lines" })).toBeDisabled();

  // Correct the unclear line: no "why" asked
  await lines.nth(3).getByRole("button", { name: "Enter line 4" }).click();
  const form = page.getByRole("form", { name: "Correct line" });
  await expect(form.getByLabel(/Why/)).toHaveCount(0);
  await form.getByLabel("Product").selectOption({ label: "PLG — Plantain green" });
  await form.getByLabel("Unit").selectOption("BOX");
  await form.getByLabel("Quantity").fill("5");
  await form.getByRole("button", { name: "Save line" }).click();
  await expect(lines.nth(3)).toContainText("Corrected");
  expect(sent.resolve).toMatchObject({ version: 8, action: "CORRECT", unit_code: "BOX", qty: "5" });
  expect(sent.resolve?.reason).toBeUndefined();

  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.html).join(" | ")}`)).toEqual([]);
  await page.screenshot({ path: "screenshots/ocr-review-grid-1440.png" });
});
