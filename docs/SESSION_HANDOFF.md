# Session handoff — Pro Trading ERP

Written 2026-11-08 for the next Claude Code session (updated after the owner menu changes). Read this, `CLAUDE.md`, `docs/PROGRESS.md` and `docs/BLOCKERS.md`; do not re-read the whole documentation pack.

## Where things are

- **Repo root:** this folder. The remote is `https://github.com/SaddamHussainSafi/pro-tamisha` (public), branch `main`.
- **Pushing:** over HTTPS with the macOS keychain credential. There is no SSH key and no `gh` CLI.
- **Commits:** author is Saddam Hussain Safi `<109510206+SaddamHussainSafi@users.noreply.github.com>`, set in the repo-local git config.
- **Never add any Claude attribution**: no `Co-Authored-By` and no "Generated with Claude Code", in commits or PRs.
- **Never commit:** `apps/api/.env`, `dump.rdb`, `celerybeat-schedule*` or `apps/api/var/`. All are git-ignored.

## Exact next task

Modules 10 Payments, 12 Reports and the Customers UI are **done** (evidence: `docs/operations/BUILD_LOG.md`, 2026-11-08 entries). Next:

- **Module 11 Focus migration is deferred by the owner (2026-11-08):** skip it until every other part of the app is functional. It is also blocked on D-13/D-17 (no Focus V7 backup sample or scope decision).
- **Owner menu changes of 2026-11-08 are done** (BUILD_LOG "Owner menu changes" parts 1–3 and the entries after): Vendors → Suppliers (UI wording only; API keeps `vendor_*`), Pricing folded into Products (`/products/prices`, `/products/case-sizes`), design previews and Settings › Warehouses removed (one warehouse; EAST inactive in `tami`), customer Invoices/Orders tabs, Settings (`/settings`, `/settings/users`; `routes/employees.py`), supplier ledger + payments (`suppliers.py`, `routes/suppliers.py`, `pages/Suppliers.tsx`), Create invoice (`/invoices/new`; direct path `POST /invoices/direct` reuses `invoicing.issue`), Overview panels (`pages/OverviewPanels.tsx`), scanner discovery (`POST /scan-devices/{id}/discover`, bridge `list_devices`).
- **Unclear owner request:** "also update to review" (Scan Center › To review?) — ask what should change.
- **Build queue until the app is fully functional:**
  1. **Global search box** in the header (placeholder today).
  2. **OCR of vendor documents into declared shipment lines** (declared lines are typed by hand today).
- **Known bug to schedule:** a fresh browser tab has no CSRF token and is signed out on its first save (`docs/BLOCKERS.md`, Known issues).
- **Owner questions from Module 10** (recorded under D-16 in `docs/BLOCKERS.md`): reminder days and wording, pause reminders while a reported payment awaits confirmation, separate recorder/confirmer, overpayment/refund policy.
- **Owner questions from Module 12 and Customers** (`docs/BLOCKERS.md`, "Open questions from Module 12"): revenue before/after tax and by issue vs payment date, how voids/credits count, what "origin" means, cases on lb lines, whether inactive customers block new orders, whether sales staff see reports.

### Module 12 map (for follow-ups)
- API: `apps/api/src/pro_trading/reports.py` (read-only SQL aggregates, `Scope` = company + `report.read` warehouses + company-timezone day range), `routes/reports.py` (5 `GET /reports/*`), schemas at the end of `schemas.py`. No migration.
- Rules in code: sales = ISSUED/PARTIALLY_PAID/PAID, net = line amounts before tax; quantities grouped per unit; no cost/margin fields (D-04). Receivables stay at `GET /receivables`.
- Web: `apps/web/src/pages/Reports.tsx` (`/reports`), `lib/reports.ts` (ranges, chart grouping, CSV). CSV is built in the browser from the rows on screen (≤ 50 per table).
- Tests: `apps/api/tests/test_reports.py`, `apps/web/src/lib/reports.test.ts`, `apps/web/e2e/reports.spec.ts`.

### Customers UI map (for follow-ups)
- Web: `apps/web/src/pages/Customers.tsx` (`/customers`, `/customers/:id`), `lib/customers.ts` (form ↔ PATCH of changed fields only). Editing needs **company-wide** `catalog.manage`; reading needs `catalog.read` or `price.read`.
- API: `routes/customers.py`; empty email/phone on PATCH clears it; `external_ref` is fixed after create.
- Tests: `apps/api/tests/test_customers.py`, `apps/web/src/lib/customers.test.ts`, `apps/web/e2e/customers.spec.ts`. In e2e, use `{ exact: true }` for the sidebar "Customers" link (the detail page also has "All customers").

### Module 10 map (for follow-ups)
- API: `apps/api/src/pro_trading/payments.py` (service, aging, reminders), `routes/payments.py`, schemas at the end of `schemas.py`, worker beat `pro_trading.schedule_reminders` (hourly).
- Lock order: payment row → invoices sorted by id. Allocations append-only (DB trigger); reversal = negative row with `reverses_id`.
- Web: `apps/web/src/pages/Payments.tsx` (`/payments`, `/payments/:id`, `/receivables`), `lib/payments.ts`. The reject/reverse dialogs must stay **outside** `<form>` (ConfirmDialog buttons have no `type`, so inside a form they submit it).
- Tests: `apps/api/tests/test_payments.py`, `apps/web/src/lib/payments.test.ts`, `apps/web/e2e/payments.spec.ts`.

## How to run (local)

PostgreSQL 16 (`tami` dev, `tami_test` for pytest) and Redis run locally on the default ports.

```bash
cd apps/api && uv run uvicorn --app-dir src pro_trading.main:app --port 8000 --reload --reload-dir src
```

```bash
cd apps/api && uv run celery -A pro_trading.worker worker --beat --loglevel=info --concurrency=1
```

```bash
cd apps/web && npm run dev
```

- **Web app:** http://localhost:5173.
- **Dev auto sign-in:** with `DEV_AUTO_LOGIN_EMAIL=owner@example.test` in `apps/api/.env`, it opens as the demo owner with no password or MFA. Remove that line to get the login screen back. Playwright skips the auto sign-in (`navigator.webdriver`).

## Checks to run before claiming anything works

```bash
cd apps/api && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy src && PYTHONPATH=src uv run pytest -q
```

```bash
cd apps/web && npx eslint src && npx tsc -b --noEmit && npx vitest run && npx playwright test
```

Playwright reuses the running API (:8000) and Vite (:5173) dev servers.

## Conventions learned this session

- **Look:**
  - Green `#28A745` = `brand-accent` / `apple-400`, for fills, highlights and icons.
  - `#1B7A30` = `brand-primary` / `apple-600`, for text, links, focus and white-text buttons.
  - Grey canvas, borderless 24px cards, black active pills, striped charts.
  - Bigger type: `--text-sm` is 15px and `--spacing` is 0.275rem.
- **Shared UI:**
  - Pieces in `apps/web/src/ui/primitives.tsx`: `KpiCard`, `CardTitle`, `Trend`, `Stat`, `BarChart`, `DonutChart`, `Progress`, `Avatar`.
  - Chart series colours `--color-series-1..4` were validated with the dataviz palette checker.
- **No warehouse UI:** never add a warehouse picker or show warehouse codes. Use the first warehouse the user is permitted in.
- **e2e tests and the UI:**
  - Tests rely on unique link names in the sidebar: `/Inventory/`, `/Products/`, `/Pricing/` and `/Settings/` are clicked without `.first()`.
  - Only one `role="status"` or `role="alert"` may exist at a time on the Scan Center, so embedded review notices are quiet.
  - The Overview `h1` must contain "Overview" (it is screen-reader-only text).
- **Lint rule:** no hex colours in TSX. Use token classes such as `fill-card` and `stroke-line`.

## Working tree at handoff

Everything is committed on `main` (see `git log`); push only after the owner says so.
