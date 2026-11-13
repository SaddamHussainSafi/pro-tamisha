# Build log

No application code has been generated or executed by this planning pack. This log should be appended by Claude Code after each completed task.

## Template entry
- Date/build commit:
- Task/module:
- Files/migrations created:
- Decisions needed:
- Tests executed and exact results:
- Manual verification evidence:
- Risks/known issues:
- Next task:

---

## 2026-11-07 — Task 00 Foundation
- **Date/build commit:** 2026-11-07. The project is not a git repository yet, so there is no commit hash.
- **Task/module:** `claude/tasks/00-foundation.md` (Module 00).
- **Files/migrations created:**
  - `apps/api`: FastAPI package `pro_trading`:
    - core modules: config, db, models, permissions, security, deps, audit, outbox, idempotency, errors
    - routes: health, auth, warehouses, admin
    - entry points: seed, worker
  - Alembic migration `0001_foundation`.
  - API tests: `apps/api/tests` (37 tests).
  - API scripts: `scripts/e2e_fixture.py`, `scripts/export_openapi.py`.
  - `apps/web`: React 19 + Vite 8 + Tailwind 4 shell:
    - auth, MFA enrolment, warehouses
    - token test, Playwright smoke
  - `apps/worker/README.md`
  - `.github/workflows/ci.yml`
  - `contracts/openapi.implemented.json` (generated; drift test)
  - `docs/decisions/ADR-0001-foundation.md`
  - `examples/.env.example` (Module 00 keys added)
- **Decisions needed:**
  - ADR-0001 sign-off.
  - Default role matrix: MANAGER currently lacks `warehouse.manage`.
  - Official logo/colour verification (`design/BRAND_VERIFICATION.md`).
  - No D-01..D-20 decision was made or assumed.
- **Tests executed and exact results:**
  - API checks (`apps/api`, Postgres 16 `tami_test` + Redis local):
    - `uv run pytest` → `37 passed in 3.12s`
    - `uv run ruff check src tests migrations scripts` → `All checks passed!`
    - `uv run ruff format --check src tests migrations` → `30 files already formatted`
    - `uv run mypy` → `Success: no issues found in 21 source files`
  - Web checks (`apps/web`):
    - `npm run lint` → clean
    - `npm run typecheck` → clean
    - `npm test` → `Tests 14 passed (14)`
    - `npm run build` → OK (~292 kB JS)
    - `npm run e2e` (Playwright, real API + DB) → `4 passed`
- **Manual verification evidence:**
  - `apps/web/screenshots/shell-1440.png` was inspected: branded sidebar, warehouse switcher, sign-out.
  - `/health/ready` → `{"status":"ok","checks":{"database":{"ok":true,"migration":"0001"},"redis":{"ok":true}}}`.
- **Risks/known issues:**
  1. On macOS, uv can create a *hidden* editable `.pth` that Python skips. The workarounds are already applied:
     - `chflags nohidden .venv/lib/python3.12/site-packages/*.pth`
     - pytest `pythonpath`
     - `uvicorn --app-dir src`
  2. CI has not run yet (no remote repository).
  3. Visual checks at 1366/1024/390 are deferred to task 00b.
  4. shadcn/ui is not installed yet.
  5. The logo is text-only until authorized assets exist.
- **Next task:** `00b-design-system`, then Module 01 catalog, pending approval.

## 2026-11-07 — Task 00b Design system (preview)
- **Scope:** UI tokens plus components, and four mock screens on synthetic data only (`/preview/*`, labelled "Design preview — sample data").
  - No backend or table changes.
  - No invoice PDF layout (D-12).
- **Built (apps/web):**
  - `lib/decimal.ts`: BigInt decimal math for UI money (half-up).
  - Components:
    - `ui/Icon.tsx`
    - `ui/primitives.tsx`: StatusChip, KpiCard, FilterBar, PageHeader, Empty/Error/Loading states, ConfirmDialog
    - `ui/DataGrid.tsx`: sortable, sticky header, arrow/Enter keyboard
    - `ui/NumericInput.tsx`: decimal-only, unit label, OCR-confidence flag
  - `components/Shell.tsx` rewritten: collapsible icon sidebar, mobile drawer, skip link, topbar with search "/", warehouse, scanner status, user, sign-out.
  - Screens:
    - Overview
    - Orders (data/loading/empty/error states)
    - Invoice editor: finalize disabled pending D-01/D-03; tax pending D-03
    - Vendor receiving split review: approve blocked while low-confidence lines exist; freight pending D-04
- **Brand/accessibility finding:**
  - White text on #198C43 is 4.3:1, which fails WCAG AA.
  - Text-bearing green surfaces use `brand.primaryHover` #116C33. #198C43 is kept for bars, icons and borders.
  - Warning-coloured text is replaced by ink text plus a warning icon or border.
  - Needs brand-owner acknowledgement (`design/BRAND_VERIFICATION.md`).
- **Tests executed and exact results (`apps/web`):**
  - `npm run lint` → clean
  - `npm run typecheck` → clean
  - `npm test` → `Tests 20 passed (20)`
  - `npm run build` → OK (328.59 kB JS)
  - `npx playwright test` → `11 passed`. Covers:
    - 4 screens × 1440/1366/1024/390: no horizontal overflow, screenshots in `apps/web/screenshots/`
    - axe WCAG 2.2 AA on all screens
    - invoice math 41.35 × 1.15 → CAD 47.55
    - remove-line confirm and Esc
    - orders filter and keyboard open
- **Fixes found by tests:**
  - Absolutely positioned sr-only labels escaped table scroll containers; wrappers are now `relative`.
  - Bar chart heights collapsed to 0.
  - `<dl>` contained a non-dt/dd child.
- **Not done / blockers:**
  1. Logo download/verification needs permission. A text wordmark is used.
  2. No visual sign-off is claimed; screenshots await owner review.
  3. At 1024px the topbar warehouse select is cramped.
- **Next:** Module 01 (catalog), pending explicit approval.

## 2026-11-07 — Module 01 Catalog, units and aliases
- **Scope:**
  - Products, categories, aliases and unit profiles (variable case weights).
  - Weight calculator.
  - Products UI.
- **Actors and invariants:**
  - Owner/manager manages; any role with `catalog.read` views.
  - SKU and stock unit are immutable. Conversions are immutable (add new, deactivate old).
  - Unknown piece/box weight is never guessed. Decimal only.
- **Built (apps/api):**
  - Migration `0002_catalog`: 4 tables and the immutability trigger.
  - `units.py`: Decimal conversion engine.
  - `routes/catalog.py`: 11 endpoints, each with idempotency, authz + MFA, audit and optimistic version.
  - Synthetic Cassava/Plantain/Rice seed.
  - No outbox: the catalog has no external IO.
- **Built (apps/web):**
  - `/products` list: search, status and category filters, cursor paging.
  - Create form.
  - Detail page: edit with 409 conflict state, profiles, aliases.
  - Weight calculator with an "Estimated — confirm on scale" badge and a CAD amount.
- **Contract:**
  - `contracts/openapi.yaml` updated with the catalog paths and schemas.
  - `contracts/openapi.implemented.json` regenerated; the drift test passes.
- **Acceptance:**
  - 7 boxes (2×20 + 2×30 + 2×40 + 1×25 lb) → **205.0000 lb / 92.9864 kg**, × 1.50 → **CAD 307.50**. Verified in unit, integration and E2E tests.
  - A PIECE with no weight → 422 `UNKNOWN_CONVERSION` with the group index.
  - Note: 205 cannot be reached with 20/30/40 lb boxes alone (every sum is a multiple of 10), so the INV-006 25 lb case is used.
- **Tests executed and exact results:**
  - `apps/api`:
    - `ruff check` clean, `ruff format` clean, `mypy src` clean (23 files).
    - `pytest` → **78 passed**: 11 units, 30 catalog integration (authz, MFA, cross-company isolation, idempotency replay/reuse, concurrent patch [200, 409], trigger, audit, failure injection), plus the earlier suites.
  - `apps/web`:
    - `lint` clean, `typecheck` clean.
    - `vitest` 23 passed.
    - `build` OK.
    - `playwright` **13 passed**, including the new `e2e/catalog.spec.ts`: owner MFA → create Cassava → 205 lb / CAD 307.50 → unknown-piece rejection; warehouse user read-only; axe WCAG 2.2 AA clean.
- **Fixes found by tests:**
  - The trigger referenced `NEW.product_id` on `products` ("record new has no field product_id"); fixed by splitting it per table.
  - LIKE wildcards in search are now escaped.
  - The loading skeleton had `aria-label` without a role (axe `aria-prohibited-attr`); `role="status"` added.
- **Failure recovery:** documented in `docs/modules/01-catalog.md`. Failure injection: an audit crash mid-create rolls everything back, and a retry with the same key succeeds once.
- **Screenshots:** `apps/web/screenshots/catalog-weight-1440.png`, `catalog-unknown-1440.png`.
- **Deferred:** lots, stock per warehouse and expiry dates go to Module 03. Bulk operations are not built yet.
- **Next:** Module 02 (pricing), pending approval.

## 2026-11-07 — Module 02 Pricing and versioning
- **Built (apps/api):** migration `0003_pricing.py` (customer_groups, customers, price_book_versions, price_entries, trigger `price_reject_published_change`); `pricing.py` (CSV parse/validate, canonical hash, diff, resolve with CUSTOMER > GROUP > STANDARD); routes `customers.py`, `pricing.py` (versions, import/preview, entries, activate/cancel/discard, diff, template/export CSV, `/prices/resolve`); seed `seed_pricing` (PB-2026-01, synthetic customers C-1001..3).
- **Built (apps/web):** `/pricing` list + price check + CSV import (preview, diff, row errors, duplicate warning); `/pricing/:id` grid with filters, set/remove on drafts, activate/cancel/discard dialogs, stale banner, 409 states; `alert-success` utility.
- **Contract:** `contracts/openapi.yaml` adds customers, customer-groups, price-book and resolve paths; `openapi.implemented.json` regenerated.
- **Acceptance:**
  - Two versions differ: PB-2026-01 CAS-FZ LB 1.50 → new version 1.60; resolve at old time = 1.50, now = 1.60 (API + E2E).
  - Old invoice rate unchanged: published entries cannot be updated/deleted (trigger tested directly); superseded version still shows 1.50.
  - Duplicate CSV (reordered, reformatted) → 409 DUPLICATE_UPLOAD, no new version; identical content → NO_CHANGES.
  - Invalid unit (TON) → INVALID_UNIT; unit without profile (RICE-JAS PIECE) → row error.
- **Tests executed:**
  - `apps/api`: ruff clean, mypy clean (27 files), pytest **94 passed** (12 pricing integration incl. permissions, cross-tenant, concurrent activation ×2, idempotent replay, failure injection; 3 pricing unit).
  - `apps/web`: lint, typecheck clean; vitest **28 passed**; build OK; Playwright **15 passed** incl. `e2e/pricing.spec.ts` (owner import → diff → activate → price check; sales read-only) with axe WCAG 2.2 AA clean.
- **Fixes found by tests:** link contrast (text-brand-primary → -hover), seed needed an approver for activation trigger, catalog E2E alias collided with new seed data (renamed).
- **Screenshots:** `apps/web/screenshots/pricing-import-1440.png`, `pricing-version-1440.png`.
- **Deferred:** invoice snapshot storage (Module 05); bulk percentage changes.
- **Next:** Module 03 inventory (autonomous per user instruction).

## 2026-11-07 — Module 03 Transactional inventory engine
- **Built (apps/api):** migration `0004_inventory.py` (lots, balances, append-only stock_movements, reservations, counts, count lines; trigger `inventory_reject_change`); service `inventory.py` (single `post()` path with sorted row locks, unique posting keys, no-negative rule, damaged companion lots, reservations, reconcile/rebuild); routes `routes/inventory.py` (stock, availability, movements, lots, opening balances, adjustments, damage, returns, reverse, lot status, reservations, counts submit/post/reject, reconciliation/rebuild, SSE `/inventory/events`); permissions inventory.count/reserve/reverse/reconcile; `seed_inventory` (4 synthetic lots in MAIN).
- **Built (apps/web):** `/inventory` (stock grid, ledger, counts, reconciliation tabs; opening balance + count panels; live SSE badge) and `/inventory/lots/:id` (balances, adjust/damage/status, ledger with reverse dialog); `lib/inventory.ts` helpers + tests.
- **Contract:** `contracts/openapi.yaml` adds 20 inventory paths; `openapi.implemented.json` regenerated.
- **Acceptance:**
  - Concurrent oversell rejected: 10 parallel −6 on stock 8 → exactly 1 success, 9× 409, final 2 (INV-004); concurrent reservations likewise.
  - 100 repeated requests with one key → identical response, one movement.
  - Rebuild after corrupting cached balances → equals ledger; reconciliation then shows no mismatches.
- **Tests executed:**
  - `apps/api`: ruff clean, mypy clean, pytest **109 passed** (15 inventory integration: units/whole-qty, replay ×100, concurrency ×3, damage/return/reverse, refused reversal, count + stale count, rebuild, append-only triggers, lot status/expiry, permissions/warehouse scope/cross-tenant, failure injection, SSE ready).
  - `apps/web`: lint, typecheck clean; vitest **31 passed**; build OK; Playwright **18 passed** incl. `e2e/inventory.spec.ts` (warehouse count read-only; owner adjust → oversell refused → damage → reverse → post count → reconciliation all match; sales read-only), axe WCAG 2.2 AA clean.
- **Fixes found by tests:** trigger referenced `NEW.lot_code` on balances (PL/pgSQL evaluates both AND operands) → nested IF per table; error envelope assertions nested under `error`.
- **Screenshots:** `apps/web/screenshots/inventory-stock-1440.png`, `inventory-lot-1440.png`.
- **Deferred / open:** D-04 costing method (landed cost column only); SSE thread-per-stream (documented); GRN and invoice postings come in Modules 04/05.
- **Next:** Module 04 (autonomous).

## 2026-11-07 — Module 04 Sales order drafts and picking
- **Built (apps/api):** migration `0005_sales_orders.py` (orders, lines, append-only amendments, picking attempts/lines; guard triggers; one-open-sheet partial unique index); service `orders.py` (line quantities, derived status, validation); routes `routes/orders.py` (list/get/create/update draft, confirm with frozen prices, cancel, cancel line qty, close with shortage, open/list/get sheets, record, submit, verify with dispositions, return, abort); `seed_orders`.
- **Built (apps/web):** `/orders`, `/orders/new`, `/orders/:id`, `/picking`, `/picking/:id` (QR code, print, record/NO/shortage reasons, verify BACKORDER/CANCEL); `lib/orders.ts` + tests.
- **Contract:** `contracts/openapi.yaml` sales-order and pick-attempt paths (14); `openapi.implemented.json` regenerated.
- **Acceptance:**
  - 15 requested, 10 supplied → 5 pending, flagged backorder; next sheet requests 5 (API + E2E).
  - NO (0 packed) stays pending, never auto-cancelled; cancel needs close permission + reason.
  - Warehouse cannot change price, confirm, close or verify; confirmed rate change blocked by trigger.
- **Tests executed:**
  - `apps/api`: ruff clean, mypy clean, pytest **120 passed** (11 order tests: acceptance, frozen price/triggers, role limits, validation/version conflict, pick rules, shortage cancel/close, abort, replay, concurrent pick open + numbering, scope/tenancy, failure injection).
  - `apps/web`: lint, typecheck clean; vitest **35 passed**; build OK; Playwright **21 passed** incl. `e2e/orders.spec.ts` (sales create→confirm→sheet; warehouse packs 10/15 with reason→submit; owner verifies→5 backorder→cancel qty), axe WCAG 2.2 AA clean.
- **Fixes found by tests:** verify sent dispositions keyed by pick line instead of order line (422 INVALID_LINE); failed save wiped picker's typed quantities (now reload only on 409); `<dl>` without dt/dd (axe).
- **Screenshots:** `apps/web/screenshots/orders-detail-1440.png`, `orders-list-1440.png`, `picking-sheet-1440.png`.
- **Deferred:** scanned order source (Module 06/07), lot/weight capture on sheet UI (API accepts `lot_id`, `weight_lb`).
- **Next:** Module 05 invoicing (autonomous).

## 2026-11-07 — Module 05 Final invoices, partial dispatch and PDF
- **Built (apps/api):** migration `0006_invoicing.py` (sequences, invoices, lines, stored documents, email messages; immutability triggers); `invoicing.py` (compute/allocate/issue, gapless numbering, catch weight, email outbox + dev sink); `invoice_pdf.py` (deterministic provisional PDF); routes `routes/invoices.py` (preview, issue, list, detail, PDF with ETag, resend email); permission `invoice.read`; settings `invoice_tax_verified`, `invoice_prefix`; worker handler `invoice.email`; seed verified sheet for C-1002 (7 boxes, bill by weight).
- **Built (apps/web):** `/invoices`, `/invoices/:id`, `/picking/:id/invoice`; pick sheet Prepare/View invoice; order page invoices list; `lib/invoices.ts` + tests.
- **Contract:** `contracts/openapi.yaml` invoice paths (preview, issue, list, detail, pdf, email); `openapi.implemented.json` regenerated.
- **Acceptance:** INV-001 (10 of 15 → invoice A, stock −10, 5 remaining); INV-002 (next 5 → invoice B, total −15); INV-003 (10 concurrent retries → one invoice, one movement per line); INV-004 (stock 8, two picks of 6 → one issues, other 409, no number consumed); INV-005 (price change → amounts + PDF bytes identical); INV-006 (205 lb × 1.50 = CAD 307.50, API + E2E).
- **Tests executed:**
  - `apps/api`: ruff clean, mypy clean, pytest **132 passed** (12 invoicing: INV-001…006, weight rules, gates, tax unverified, permissions/scope/tenancy, failure injection with gapless retry, email once/resend/no email).
  - `apps/web`: lint, typecheck clean; vitest **39 passed**; build OK; Playwright **23 passed** ×3 consecutive runs incl. `e2e/invoices.spec.ts` (sales preview read-only; owner weights → 307.50 → issue → PDF served → sheet links invoice → list), axe WCAG 2.2 AA clean.
- **Fixes found by tests:** success chip text failed contrast on canvas (now ink text + green icon); pick sheet double initial load could wipe a typed quantity (stale response now ignored) — was the source of an intermittent orders E2E failure.
- **Screenshots:** `apps/web/screenshots/invoices-issue-1440.png`, `invoices-detail-1440.png`, `invoices-list-1440.png`.
- **Deferred / open:** D-02 credit/void, D-03 tax rules, D-12 final PDF layout, D-15 email provider.
- **Next:** Module 06 (autonomous).

## 2026-11-08 — Module 06 Scanner bridge and intake
- **Built (apps/api):** migration `0007_scanner.py` (scan devices, source documents, scan jobs; single-flight partial unique index; append-only triggers); service `scanning.py` (file validation, signed single-use capabilities, device auth, numbering, sweep); routes `routes/scans.py` (devices, jobs, uploads, document content, bridge pair/heartbeat/claim/upload/fail); settings `scan_max_bytes`, `scan_capability_key`, TTLs. `seed.py` now re-syncs default role grants for an existing company (fixed live 403s on orders/invoices after new permissions were added).
- **Built (apps/bridge):** stdlib localhost bridge (`pair`, `run`), Host/Origin gate, NAPS2 + fake drivers, heartbeat; `README.md`.
- **Built (apps/web):** `/scan` Scan Center (Scan now via bridge, upload fallback, status views, Scanners tab with pair/approve/revoke), `/scan/:id` detail with inline original; nav enabled; `api.ts` raw-body upload; `lib/scans.ts` + tests.
- **Contract:** `contracts/openapi.yaml` scan-device, scan job, upload, document and bridge paths (15); `openapi.implemented.json` regenerated.
- **Acceptance:** no website can scan unauthenticated (SEC-002: foreign origin/host rejected, capability required, single use, device-bound); unplugged scanner → FAILED with hint and manual upload works (API + E2E); repeat upload → one source document.
- **Tests executed:**
  - `apps/api`: ruff clean, mypy clean (incl. bridge), pytest **145 passed** (13 scanner: pairing + fingerprint, end-to-end bridge scan + capability single use, SEC-002, expiry + sweep, single flight, unplugged fallback, dedupe, file validation, permissions/scope/tenancy, revoke, cancel, failure injection, append-only).
  - `apps/web`: lint, typecheck clean; vitest **49 passed**; build OK; Playwright **26 passed** incl. `e2e/scans.spec.ts` (owner pairs the real bridge process and approves by confirmation code, wrong code refused; sales scans through the bridge → received → inline PDF; upload, duplicate, rejected .txt; unplugged scanner → Failed view), axe WCAG 2.2 AA clean.
- **Fixes found by tests:** claim checked nonce before expiry/used (wrong error code); audit query used non-existent `detail` column; bridge exception renamed `BridgeApiError` (N818); E2E sign-in race before navigation; screenshot review: PDF preview blank because Chrome will not run its PDF viewer under CSP `sandbox` (PDFs now get `default-src 'none'; frame-ancestors 'self'` without sandbox, images/TIFF stay sandboxed — tested); header "Scanner: not connected" was hard-coded (now from device heartbeats, no localhost probing on every page).
- **Screenshots:** `apps/web/screenshots/scanners-1440.png`, `scans-1440.png`, `scan-detail-1440.png`.
- **Deferred / open:** office PoC with the Toshiba 409CS + NAPS2 profile; Windows service installer for the bridge; OCR (Module 07).
- **Next:** Module 07 OCR (autonomous).

## 2026-11-08 — Module 07 OCR reading and review
- **Built (apps/api):** migration `0008_ocr.py` (extractions, fields, corrections; append-only / immutable-read / final-state triggers); `ocr.py` (fake + Document AI providers, PT-OS-1 template mapping via SKU/alias/name, confidence threshold, outbox-driven reading with transient retry, printable order-sheet PDF); routes `routes/ocr.py` (list/detail, read again, resolve CONFIRM/CORRECT/REMOVE, add missed line, accept → DRAFT order, reject, accuracy, order sheet); `insert_order` extracted from order creation; worker handler `ocr.extract`; scans auto-queue reading for sales-order scans; settings `ocr_provider`, `ocr_confidence_threshold`, Document AI endpoint/credentials, timeout. `scripts/e2e_fixture.py --sheet` writes a synthetic hand-filled sheet.
- **Built (apps/web):** Scan Center **To review** tab (accuracy KPI, lines checked, printable order sheet download, status views); `/scan/review/:id` side-by-side review (original + lines, confirm/correct/remove/add, customer, "checked every line", create draft / reject / read again); reading panel on scan detail; `lib/ocr.ts` + tests.
- **Contract:** `contracts/openapi.yaml` `/ocr/*` (8 paths, replaces placeholder `/scans/jobs/{jobId}/review`); `openapi.implemented.json` regenerated.
- **Acceptance:** OCR never creates an issued invoice, reservation or stock movement (counts asserted unchanged; accept → DRAFT only); unclear/unknown lines cannot be confirmed and need manual correction; original kept and shown beside the lines; accuracy measured as exact-field and only claims the 95% target with ≥50 samples (real-handwriting measurement pending office PoC — D-18 full review stays).
- **Tests executed:**
  - `apps/api`: ruff clean, mypy clean, pytest **157 passed** (12 OCR: read→review→accept draft-only, DB immutability, photo/no-text + manual line, accuracy maths, reject + re-read supersedes, provider outage retries → FAILED → re-read, engine disabled, vendor docs not auto-read, permissions/scope, worker registration, order-sheet round trip).
  - `apps/web`: lint, typecheck clean; vitest **55 passed**; build OK; Playwright **27 passed** incl. `e2e/ocr.spec.ts` (sales uploads synthetic sheet → worker reads → queue → confirm clear line, correct unclear line, remove unknown write-in → draft order with 2 lines), axe WCAG 2.2 AA clean.
- **Fixes found by tests:** review queue loaded before the worker finished and never refreshed (now polls); quantities displayed as `12.0000` (trimmed).
- **Screenshots:** `apps/web/screenshots/ocr-queue-1440.png`, `ocr-review-1440.png`, `ocr-draft-order-1440.png` (headless Chromium does not render the PDF in the iframe; verified in real Chrome in Module 06).
- **Deferred / open:** 50-sample real handwriting accuracy run; Document AI provider live verification (needs credentials); QR/template alignment for skewed scans.
- **Next:** Module 08 (autonomous).

## 2026-11-08 — Module 08 Vendor shipments and GRN
- **Built (apps/api):** migration `0009_receiving.py` (vendors, shipments, shipment documents, declared lines, GRN lines, landed costs; append-only / final-state / withdraw-only triggers); `receiving.py` (numbering, declared-vs-counted comparison incl. document conflicts, exactly-once GRN posting into SALEABLE + DAMAGED lots); routes `routes/receiving.py` (vendors, shipments, documents attach/upload, declared lines with vendor alias memory, count lines, costs, submit/reopen/post/cancel); vendor documents readable by receiving roles; seed vendors V-ACC / V-KIN / V-LIM (synthetic).
- **Built (apps/web):** `/receiving` list, `/receiving/:id` shipment workspace (comparison, original documents, declared, count, costs, approve dialog with acknowledgement), `/vendors`; nav enabled; `lib/receiving.ts` + tests. Shared `ConfirmDialog` now uses a unique title id (two dialogs on one page shared `confirm-title`, so the accessible name was wrong).
- **Contract:** `contracts/openapi.yaml` `/vendors*` + `/shipments*` (17 paths, replaces `/grns/{grnId}/post` placeholder); `openapi.implemented.json` regenerated.
- **Acceptance:** supplier declares 25, warehouse accepts 23 + damaged 2 → only 23 saleable posted (damaged in separate lot); retry/concurrent posts cannot double-add (API + E2E).
- **Tests executed:**
  - `apps/api`: ruff clean, mypy clean, pytest **166 passed** (9 receiving).
  - `apps/web`: lint, typecheck clean; vitest **61 passed**; build OK; Playwright **30 passed** incl. `e2e/receiving.spec.ts`, axe WCAG 2.2 AA clean.
- **Fixes found by tests:** horizontally scrolling tables were not keyboard-focusable (axe); comparison table cramped in side column (moved full width after screenshot review); name clash `submitCount` (inventory) vs receiving.
- **Screenshots:** `apps/web/screenshots/receiving-count-1440.png`, `receiving-list-1440.png`, `receiving-posted-1440.png`, `vendors-1440.png`.
- **Deferred / open:** landed-cost allocation rule (decision needed); OCR of vendor documents into declared lines (manual entry for now); currency conversion of vendor costs.
- **Next:** Module 09 (autonomous).

## 2026-11-08 — Module 09 Documents, Drive archive, POD
- **Built (apps/api):** migration `0010_documents.py` (`document_files` archive index + `document_file_guard` trigger); `documents.py` (filing for invoice PDFs / scans / vendor docs / POD, folder layout, local/google/disabled Drive providers with fault injection, outbox handler `document.drive_sync`, retry); hooks in invoice issue, scan uploads (manual + bridge) and shipment documents; routes `routes/documents.py` (list/detail/content/retry-sync, `POST /invoices/{id}/pod`); permissions `document.read` / `document.manage` (manager, sales, finance; owner); settings `DRIVE_PROVIDER`, `DRIVE_LOCAL_ROOT`, `DRIVE_ROOT_FOLDER_ID`, `DRIVE_CREDENTIALS_FILE`, `DRIVE_TIMEOUT_SECONDS`, `DRIVE_FAULT`; worker registers the Drive handler.
- **Built (apps/web):** `/documents` archive page (sync KPIs, filters, Retry), invoice-page Archive panel with POD upload; nav enabled; `lib/documents.ts` + tests.
- **Contract:** `contracts/openapi.yaml` 5 paths; `openapi.implemented.json` regenerated.
- **Acceptance:** 50-line invoice paginates (2 pages, totals once); Drive timeout leaves the invoice committed with Pending sync → retries → Sync failed → Retry → In Drive; reprint returns the same issued bytes (invoice + archive endpoints, ETag).
- **Tests executed:**
  - `apps/api`: ruff clean, mypy clean, pytest **180 passed** (13 documents + 1 receiving filing: pagination, sync once + stable id, timeout failure injection + retry/replay/version conflict, denied → immediate fail + audit, disabled → local only, DB guards, POD append-only, scan filing, permissions/scope/tenancy).
  - `apps/web`: lint, typecheck clean; vitest **65 passed**; build OK; Playwright **32 passed** incl. `e2e/documents.spec.ts` (owner issues → archive panel → real worker copies → In Drive; POD upload; archive page; warehouse denied), axe WCAG 2.2 AA clean.
- **Fixes found by tests:** test helpers (fixture import), folder assertion included "(synthetic)" suffix; size column wrapped.
- **Screenshots:** `apps/web/screenshots/documents-1440.png`.
- **Deferred / open:** live Google Drive verification (credentials + folder decision); real invoice template (D-12); POD in emails; kiosk printing.
- **Next:** Module 10 Payments (autonomous).

## Demo data loader (user request: "prefill everything with demo data")
- `apps/api/scripts/demo_data.py` fills the synthetic demo company through the real API (in-process client, signed in as the seeded demo employees; sessions revoked at end). Refuses production-like envs; idempotent (stops if C-1004 exists).
- Loads: 12 products, 6 customers, price book PB-2026-02 active + PB-2026-03 draft, 5 vendor shipments (3 posted incl. EAST, 1 counted awaiting approval, 1 open), opening balances, a damage write-off, a pending cycle count, 14 sales orders across every stage (7 invoiced, 1 with a shortage), 3 PODs, 3 scans (2 OCR order sheets).
- Verified on dev DB `tami`: demo company now 8 invoices / 16 orders / 5 shipments / 18 archived documents / 8 scans; all outbox events PROCESSED. Payments demo is added with Module 10.

## 2026-11-08 — UI restyle to the reference dashboards (owner request)
- **Request:** "make it look like this, everything" with the three reference images (Invowise, dosage, Finexy), using the ui-ux-pro-max skill. This overrides the Tamisha green palette (note added at top of `design/UI_DESIGN_REFERENCE.md`).
- **Tokens:** `design/tokens.json` schemaVersion 2 — orange primary `#D2441A` (4.5:1 on white), bright accent `#F26430` (decoration and large text only), near-black `#18181B` for active pills/primary buttons, grey canvas `#EFEFF1`, new `surface.subtle` and `state.info`. `theme.css` and `tokens.test.ts` updated to match.
- **Shell:** header with logo pill, top pill navigation (Overview, Orders, Picking, Invoices, Inventory, Receiving), search, warehouse switcher, bell and avatar; icon rail on the left for the other sections, design previews and sign-out; full-label drawer below 1280px. Inter loaded from Google Fonts.
- **Components:** borderless 20px cards, pill buttons/chips/inputs, soft status chips, grey table headers, new `CardTitle`, `Trend`, `Stat`, `Avatar`, `BarChart` (striped bars, one orange highlighted bar, dashed average, accessible table), accent `KpiCard`.
- **Overview (real):** greeting, figures from the API only (invoiced this month by `issued_at`, open orders, shipments to receive), 6-month invoiced chart, open orders, recent invoices table; cards hide when the role can't read that data. Nothing claims paid/overdue until Module 10.
- **Tests executed (apps/web):** typecheck, lint clean; vitest **66 passed**; build OK; Playwright **32 passed** incl. axe WCAG 2.2 AA and no horizontal overflow at 1440/1366/1024/390.
- **Fixes found by tests:** header too wide at 1366 (name hidden until 2xl); single-column grid overflow on phones; screen-reader text escaping table scroll boxes; rail label colliding with the OCR "Customer" field lookup; colour transitions caught mid-fade by axe (state changes on nav pills, rail and filter chips are now instant).
- **Flaky (pre-existing, not changed):** `scans.spec.ts` upload-fallback can see two `role=status` elements while the list reloads; passed on rerun.
- **Follow-up (same day, owner: "too text heavy, bigger text, more whitespace, bigger icons, proper dashboard"):** type scale up (`--text-sm` 15px, `--text-base` 17px) and spacing up 10% (`--spacing` 0.275rem) app-wide; 24px card radius; bigger nav pills, 48px rail icons, design previews moved into one rail popover. Overview rebuilt as a dashboard: big KPI cards (56px icon tiles, 40px figures, accent gradient card), large striped bar chart, new `DonutChart` (orders by stage; chart series tokens validated with the dataviz CVD/contrast checker) and `Progress` bars on striped tracks; fewer words. Playwright **32 passed**, axe clean; screenshots `apps/web/screenshots/dashboard-1440.png`, `dashboard-390.png`, `overview-live-1440.png`.
- **Follow-up (owner: "remove main warehouse or option to choose warehouse completely"):** removed the header/drawer warehouse picker and its saved choice (`pt.warehouse`); removed "Main warehouse" from preview screens. Scanner hint uses the first visible warehouse. Inventory page warehouse fields left in place (stock counts and opening balances need a warehouse). Playwright **32 passed**.
- **Follow-up (owner: "remove them too"):** Inventory page has no warehouse fields any more — removed the stock and ledger warehouse filters (lists show all warehouses the user can see), the Warehouse column, warehouse codes in count and lot headers, and the warehouse selects in Record count / Opening balance (they now use the first warehouse the user is allowed to count / adjust in). Playwright **32 passed** incl. inventory counts and opening balance.
- **Follow-up (owner: "remove all"):** warehouse/location codes, columns and pickers removed from Orders (list column, subtitle, New order field), Picking, Receiving (column, subtitle, "Receiving at" field, posting text), Scan Center (Location columns, New scan and Add scanner fields, job subtitle), OCR review (column, order-sheet location, subtitle) and invoice details. New records use the first warehouse the user is allowed to act in. Settings › Warehouses (admin list) kept. `e2e/scans.spec.ts` updated: Add-scanner has no Location field; the upload test waits for the list to finish loading (fixes the two-`role=status` flake). Playwright **32 passed** twice in a row; a third run had one timing flake in `documents.spec.ts` (warehouse role access), which passed 3/3 when rerun alone.

## 2026-11-08 — Full health check, Modules 00–09 (owner: "check if all modules are working fine")
- **Environment:** local Postgres 16 (`tami` dev, `tami_test`), Redis, uvicorn :8000, Vite :5173, Celery worker. `/health/ready` → database ok (migration `0011`), redis ok.
- **apps/api:** `uv run pytest` → **180 passed in 45.87s**; `mypy` → no issues in 42 files.
  - `ruff check` initially **failed**: 1 × E501 in `models.py`, in the unfinished Module 10 payment constraint.
  - `ruff format --check` initially **failed**: 4 files (`config.py`, `models.py`, `receiving.py`, `tests/test_documents.py`).
  - Fixed both with a string split and `ruff format`. Both checks are now clean. No behaviour change.
- **apps/web:** lint and typecheck clean; vitest **70 passed**; build OK (chunk-size warning only).
  - Playwright **32 passed (26.4s)**.
  - The first run had 1 failure (`inventory.spec.ts` warehouse count; nav link not found) and 2 skipped. Cause: the API files were reformatted mid-run and the `--reload` dev server restarted. The spec passes in isolation, and the full rerun was clean.
- **Scanner bridge:** imports OK; covered by API scan tests with the `fake` driver. A real Toshiba/NAPS2 run has still **not been verified**.
- **Module 10 Payments:** only migration `0011_payments.py` and its models exist. There is no service, routes, tests or UI, so the module is **not working yet**.

## 2026-11-08 — Development auto sign-in (owner: "disable password and login for now")
- **API:** setting `DEV_AUTO_LOGIN_EMAIL` (default empty = off) and hidden route `POST /api/v1/auth/dev-login` (not in the OpenAPI contract) that opens a normal server-side session for that employee with MFA marked verified, audited as `auth.dev_login`. Returns 404 when unset or when `APP_ENV` is staging/production; `Settings` refuses to load if it is set in staging/production. Shared `_start_session` helper with `/auth/login`.
- **Web:** when there is no session, dev builds (`import.meta.env.DEV`) call `/auth/dev-login` before showing the login page; automated browsers (`navigator.webdriver`) skip it so Playwright still exercises the real login.
- **Local switch:** `apps/api/.env` (git-ignored) sets `DEV_AUTO_LOGIN_EMAIL=owner@example.test` (demo owner). Remove the line to bring the login back.
- **Tests:** API ruff/mypy clean, pytest all passed (7 new in `tests/test_dev_login.py`: off by default, signs in owner without password/MFA + audit, unknown email 404, refused when production-like, settings refuse staging/production, absent from contract). Web typecheck/lint clean; Playwright **32 passed** with auto sign-in enabled on the dev API.
- **Follow-up (owner: Tamisha main colour #28A745, "apple" shade ramp):** `tokens.json` schemaVersion 3 — accent apple-400 `#28A745` (fills, chart highlight, progress, icons), primary apple-600 `#1B7A30` (text, links, focus, white-text buttons; 5.4:1), hover apple-700 `#125E23`; full ramp exposed as `--color-apple-50…950` and checked by `tokens.test.ts`. Chart series re-validated (green, blue, orange, violet). Note in `UI_DESIGN_REFERENCE.md` updated. Vitest **81 passed**, Playwright **32 passed** incl. axe.

## 2026-11-08 — Sidebar, Scan Center flow and recognized lines (owner mock-ups)
- **Sidebar (all pages):** grouped, collapsible left sidebar replacing the top pill bar + icon rail — Overview; Sales (Orders, Invoices, Payments*); Warehouse (Picking, Inventory ▸ Stock on hand / Stock ledger / Counts, Receiving, Scan Center); Master data (Products, Customers*, Vendors, Pricing); Analytics (Reports*, Documents); Settings; Design previews; user card with sign-out. Collapse state remembered per browser (`pt.sidebar.collapsed`). Drawer below 1024px. (*module not built: shown as "Soon".)
- **Scan Center:** new-scan card in three states — start (illustration, document type, scanner/device with online dot, note, Scan now / Upload a file), in progress (step list and step-based progress; no fake page counts), completed (preview, document details, OCR summary, Review lines / Scan details / Download original / Upload another / New scan). Tabs "New scan · To review · Scanners".
- **Auto-open recognized lines:** after a sales-order scan or upload the card polls the reading; when it reaches Needs review the **Recognized lines** editor (the review page, embedded) appears under the card and scrolls into view, so OCR mistakes are fixed in place and the draft order is created from there.
- **Recognized lines look** (review page, embedded review, receiving preview, real receiving comparison): icon header, low-confidence / needs-fixing chips, product tiles, "OCR n% · check against original" flags, Matched/Discrepancy chips, green summary bar. Product photos are not in the catalog, so tiles show an icon.
- **Tests:** `e2e/scans.spec.ts` — the fault-mode scan now starts with "New scan" (the card shows the finished upload first). Vitest **81 passed**; Playwright **32 passed** incl. axe WCAG 2.2 AA.
- **Screenshots:** `apps/web/screenshots/scan-center-new-1440.png`, `scan-center-done-1440.png`, `scan-center-lines-1440.png`, `receiving-recognized-1440.png`, `sidebar-collapsed-1440.png`.

## 2026-11-08 — Module 10 Payments: manual ledger, receivables, reminders
- **Built (apps/api):** `payments.py` service — record (REPORTED, `PAY-{year}-{n:06d}`), verify (bank/cheque/receipt reference, employee, time), allocate, reverse (append-only negative row), reject (REPORTED only), aging, reminder scheduler + outbox handler `payment.reminder`. Lock order payment → invoices sorted by id. Invoice `paid_amount`/`state` (ISSUED → PARTIALLY_PAID → PAID, and back on reversal) maintained in the same transaction. Routes `routes/payments.py` (8 paths, all commands via `mutate()`: idempotency + audit + one transaction). Invoice list gains `open` filter and `paid_amount`/`balance`. Worker: hourly beat `pro_trading.schedule_reminders` and the reminder handler.
- **Built (apps/web):** `/payments` (KPIs, status tabs with counts, search, Record payment form), `/payments/:id` (Confirm at the bank with per-invoice amounts and "left after", Apply, Reject dialog, allocation history with Reverse dialog), `/receivables` (aging buckets per customer, unapplied credit, reported-not-confirmed, D-16 placeholder notice). Invoices list/detail show Balance and payment state. Sidebar Payments enabled. `lib/payments.ts` + tests.
- **Contract:** `contracts/openapi.yaml` — `/payments*` (6 paths) and `/receivables` replace the `/invoices/{id}/payments` placeholder; `openapi.implemented.json` regenerated.
- **Acceptance:** 700 on a 1,250 invoice → 550 open, PARTIALLY_PAID (API + UI); duplicate confirmation blocked (same payment → `ALREADY_VERIFIED`; same bank reference on another payment → `DUPLICATE_CONFIRMATION`; same key → replay); no bank login / bank API secret exists (settings test).
- **Fix found by e2e:** the reject dialog sat inside the confirm form, so "Reject payment" also submitted the confirmation (→ VERSION_CONFLICT). Dialog moved outside the form.
- **Tests executed:**
  - `apps/api`: `ruff check` + `ruff format --check` clean, `mypy src` clean (44 files), `pytest` **197 passed** (10 in `tests/test_payments.py`: acceptance, duplicate confirmation + replay, 8 concurrent same-key retries, two payments racing for one invoice, allocation rules, reversal + DB append-only/immutability guards, reject, permissions/MFA/tenancy + denial audit, aging buckets with clock injection, reminders once per offset with skip-if-paid).
  - `apps/web`: eslint + `tsc -b` clean, vitest **86 passed**, Playwright **34 passed** (new `e2e/payments.spec.ts`: record → confirm + apply → invoice balance → receivables → duplicate bank reference refused → reject; sales can record but not confirm; axe WCAG 2.2 AA on each page).
  - Demo data: `scripts/demo_data.py` payments step run against `tami_test` (seed + demo): 2 applied, 1 reported, 1 confirmed-unapplied cheque, 1 rejected. Not loaded into the dev DB `tami` (it already holds demo activity and the script stops when loaded).
- **Not verified:** Celery beat running the hourly reminder job live (scheduler and handler tested by direct call); real email provider (dev sink only, D-15).
- **Screenshots:** `apps/web/screenshots/payment-confirm-1440.png`, `payment-detail-1440.png`, `receivables-1440.png`.

## 2026-11-08 — Module 12 Analytics / Reports (read-only)
- **Built (apps/api):** `reports.py` service (SQL aggregates, nothing written) and `routes/reports.py`: `GET /reports/sales` (totals, quantities per unit, zero-filled day/month series), `/reports/sales-by-product` (per product + unit: qty, net, average realised rate, invoices, customers; `sort=top|least`, `limit` ≤ 500; with `customer_id` = per-customer rate/volume), `/reports/sales-by-customer`, `/reports/stock-flow` (opening + received + dispatched + other = closing from `stock_movements`), `/reports/receiving` (posted GRNs per vendor + unit: declared vs accepted/damaged/rejected). Filters: `date_from`/`date_to` (company-timezone days, default last 30, ≤ 731 days → `INVALID_RANGE` / `RANGE_TOO_LONG`), `customer_id`, `product_id`, `vendor_id`, `warehouse_id` (API only). Permission `report.read` (already in the catalog: OWNER_ADMIN, MANAGER, FINANCE); rows limited to warehouses where the caller holds it, like `invoice.read` on invoice lists. Sales = invoices in ISSUED/PARTIALLY_PAID/PAID, money before tax from line amounts; boxes/pieces/lb/kg never summed together; no cost/margin/profit fields (D-04). Receivables are not duplicated: the page reads `GET /receivables`. No migration (no new tables; existing indexes `ix_invoices_company_issued`, `ix_stock_company_ts` cover the date filters).
- **Built (apps/web):** `/reports` (`pages/Reports.tsx`): period pills + custom From/To, customer and product filters, KPI cards (net sales, invoices, with tax, owed now from `/receivables` when the user may read it), striped bar chart (days grouped per week over 14 days; months per 3 months over 12), quantities per unit, Products (best sellers / least sold), Customers, Stock movement and Receiving-by-vendor tables, CSV download per table (formula-injection guarded), product drill-down links. Sidebar "Reports" enabled (`nav.ts` `module: null`). `lib/reports.ts` + tests.
- **Contract:** `contracts/openapi.yaml` — `/reports/*` (5 paths, shared `ReportDateFrom/To/Warehouse` parameters, `ReportRange` 422 response) replace the `/analytics/sales` placeholder; `openapi.implemented.json` regenerated (`PYTHONPATH=src uv run python scripts/export_openapi.py`).
- **Acceptance:** revenue totals reconcile to issued invoices (sum of `subtotal`) across summary, by-customer, by-product and the series; stock flow closing = sum of `inventory_balances` per product/unit and each row satisfies opening + received + dispatched + other = closing; filters reproducible (same call twice is identical; per-customer parts add to the whole). Credits: no void/credit-note flow exists yet, so "issued less credits" = issued (open question in `docs/BLOCKERS.md`).
- **Tests executed:**
  - `apps/api`: `uv run ruff check src tests` + `uv run ruff format --check src tests` clean, `uv run mypy src` clean (46 files), `PYTHONPATH=src uv run pytest -q` **204 passed** (7 new in `tests/test_reports.py`: reconcile to invoices, reproducible filters + per-customer rate, range validation, stock flow vs ledger and balances, receiving by vendor incl. unposted shipment excluded, permissions/MFA/warehouse scope/tenancy + denial audit, reports are read-only).
  - `apps/web`: `npx eslint src` + `npx tsc -b --noEmit` clean, `npx vitest run` **95 passed** (9 new in `src/lib/reports.test.ts`), `npx playwright test` **36 passed** (new `e2e/reports.spec.ts`: issue an invoice → customer "with tax" equals the invoice total, products/stock rows update, CSV download, customer filter, bad custom range caught; sales role sees no access; axe WCAG 2.2 AA on each page).
- **Checked by eye:** the page in the in-app browser at 800 px and 1440 px (chart labels shortened after the first look). Screenshot: `apps/web/screenshots/reports-1440.png` (git-ignored).
- **Not built:** SSE invalidation (`/events` stays a contract placeholder; the page reloads on filter change), materialized caches (live queries are enough at current volume), origin filter (products have no origin field), customer → invoice drill-down (the invoice list does not read URL filters yet).

## 2026-11-08 — Customers management UI
- **Built (apps/web):** `/customers` (`pages/Customers.tsx`): search by name or reference, Active / Inactive / All, group filter, sortable grid (avatar, reference, group, email, phone, terms, status), "New customer" form (company-wide `catalog.manage` only) with field-level checks, customer groups list + "Add group". `/customers/:id`: edit form (name, email, phone, group, payment terms, active) that sends only changed fields with the version, "Undo changes", 409 → plain message + reload; read-only details for users without `catalog.manage`; Balance (from `GET /receivables`), recent invoices, recent orders, "Sales report" link (`/reports?customer_id=`). Sidebar "Customers" enabled (`nav.ts` `module: null`). Reports customer names link to the customer page. `lib/customers.ts` + tests.
- **API (apps/api):** `routes/customers.py` already had list/get/create/patch and groups. Changed: email must look like an address (invoices and reminders are sent there) and an empty email or phone now clears it (before, PATCH could not remove an email). Contract `contracts/openapi.yaml` documents the create/patch bodies; `openapi.implemented.json` regenerated.
- **Tests executed:**
  - `apps/api`: `uv run ruff check src tests` + `uv run ruff format --check src tests` clean, `uv run mypy src` clean, `PYTHONPATH=src uv run pytest -q` **207 passed** (3 new in `tests/test_customers.py`: create rules incl. duplicate reference and bad email, edit/clear/version conflict/idempotent retry/audit, permissions incl. warehouse-scoped `catalog.manage` refused and tenancy).
  - `apps/web`: `npx eslint src` + `npx tsc -b --noEmit` clean, `npx vitest run` **100 passed** (5 new in `src/lib/customers.test.ts`), `npx playwright test` **38 passed** (new `e2e/customers.spec.ts`: search, add group, add customer with a bad email first, stale-version save refused and reloaded, clear email + deactivate, inactive filter; sales can look up but not edit; axe WCAG 2.2 AA on list and detail).
- **Found, not fixed (outside this change):** a brand-new browser tab has no CSRF token (it lives in per-tab `sessionStorage`), so its first save signs the user out. Logged in `docs/BLOCKERS.md`.
- **Screenshots:** `apps/web/screenshots/customers-1440.png`, `customer-detail-1440.png` (git-ignored).

## 2026-11-08 — Owner menu changes, part 1: Suppliers, Prices under Products, one warehouse
- **Owner direction (2026-11-08):** rename Vendors to Suppliers; fold the Pricing menu into Products; remove Design previews (Overview/Orders/Invoice editor/Receiving mock screens); "there is no multiple warehouse, everything is one" — remove Settings › Warehouses.
- **Web:** sidebar Master data = Products (Product list, Prices), Customers, Suppliers. Prices pages moved to `/products/prices` and `/products/prices/:id` (old `/pricing` links redirect). `/vendors` → `/suppliers`; supplier wording everywhere a user reads it (API field names unchanged). Design-preview pages, `src/mock` and `e2e/design.spec.ts` deleted. `/settings` is now Settings › My account (name, email, two-step status); `/settings/warehouses` redirects there; `pages/Warehouses.tsx` deleted. Sidebar child links highlight by path.
- **Data:** `seed.py` creates only MAIN; `scripts/demo_data.py` receives the former EAST shipment into MAIN. Dev DB `tami`: EAST set inactive through `PATCH /warehouses/{id}` (audited, reversible). Its synthetic stock (COCO-400 30, GARI-4KG 15, MANGO-850 20 boxes) stays on EAST lots; nothing open references EAST.
- **Tests executed:** `apps/api` ruff/format/mypy clean, pytest **207 passed**. `apps/web` eslint/tsc clean, vitest **100 passed**, Playwright **31 passed** (7 preview tests removed with the previews; smoke, pricing and receiving specs updated for the new menus).

## 2026-11-08 — Owner menu changes, part 2: case sizes and prices inside Products
- **Owner direction:** "when I click on products we can change the box weight, product size, product prices"; a Products sub-menu to create case sizes (e.g. Cassava 30 lb, 35 lb); the default case size fills in everywhere.
- **API:** `GET /unit-profiles` lists every case size (unit profile) across products with SKU/name, default first (`catalog.read`; `include_inactive`, `q`). `GET /price-books/versions/{id}/entries` gains `product_id`. Contract + `openapi.implemented.json` updated. Case-size weights stay immutable (invoices depend on them): change = add a size, make it default, retire the old one.
- **Web:** Products › Case sizes (`/products/case-sizes`): list, search, add (weight → suggested name such as `35LB`, optional pieces, default checkbox), make default, retire. Product page: "Unit profiles" is now "Case sizes"; new **Prices** card shows the prices in force for the product and "Change price", which writes into a price-book draft (created from the current book when none is open); users with `price.approve` can activate it there, others see it waiting. Invoice "Bill by weight" pre-fills the default case-size weight (labelled; to be replaced by the scale weight, D-08).
- **Tests executed:** `apps/api` ruff/format/mypy clean, pytest **209 passed** (2 new in `tests/test_case_sizes.py`). `apps/web` eslint/tsc clean, vitest **105 passed** (5 new in `src/lib/caseSizes.test.ts`), Playwright **33 passed** (new `e2e/products.spec.ts`: add a 35 lb size, make it default, change the box price through a draft and activate it; sales read-only; axe on each page).

## 2026-11-08 — Owner menu changes, part 3: customer invoices and orders on their own tabs
- **Owner direction:** a customer's invoices and orders shown on another page.
- **Web:** `/customers/:id` has Details / Invoices / Orders tabs (`?tab=invoices|orders`, shown only with `invoice.read` / `sales_order.read`). Invoices tab: every invoice newest first (order, issued, due, total, balance, state), 50 at a time with "Show more". Orders tab: every order (created, wanted for, lines, status). Details keeps the edit form and balance.
- **Tests executed:** `apps/web` eslint/tsc clean, vitest **105 passed**, Playwright **33 passed** (`e2e/customers.spec.ts` now opens both tabs, with axe).

## 2026-11-08 — Settings: My account and Users
- **Owner direction:** user settings "and everything"; chosen scope: My account + staff users.
- **API (`routes/employees.py`):** `GET/POST /employees`, `PATCH /employees/{id}` (name, one company-wide role, active), `POST /employees/{id}/reset-password` (temporary password, clears lockout), `POST /employees/{id}/reset-mfa` (lost phone); all need company-wide `employee.manage` (Owner/Admin, MFA). `PATCH /auth/me` (own name) and `POST /auth/password` (current + new; other sessions revoked, this one kept). Rules: passwords 12–128 chars, not the email, ≥4 distinct characters; never written to audit details or idempotency hashes (create hashes a password-free copy); nobody disables, re-roles or resets themselves (`SELF_CHANGE`); at least one active Owner/Admin kept (`LAST_OWNER`); role, active, password and two-step changes revoke that person's sessions. Wrong-current-password attempts are audited in their own transaction (the first version deadlocked on the employee row lock; fixed before commit). Contract + `openapi.implemented.json` updated.
- **Web:** `/settings` = My account (name, two-step status, change password); `/settings/users` (Owner/Admin only) = list with role, two-step, status, last seen; Add user with a generated 16-character temporary password shown once; Edit name/role; New password; Reset two-step; Disable/Enable (confirm dialogs). `lib/staff.ts` + tests.
- **Tests executed:** `apps/api` ruff/format/mypy clean, pytest **214 passed** (5 new in `tests/test_staff.py`). `apps/web` eslint/tsc clean, vitest **108 passed** (3 new), Playwright **35 passed** (new `e2e/settings.spec.ts`: add user, change role, disable/enable, wrong current password, the new user signs in with the temporary password and changes it; axe).

## 2026-11-08 — Suppliers: ledger and supplier payments
- **Owner direction:** a supplier page with a ledger of what we ordered from them, prices, how much we paid and returned, plus a sub-menu; chosen: add supplier payments.
- **DB:** migration `0012_supplier_payments` — `supplier_payments` (SPAY-YYYY-NNNNNN, supplier, optional shipment, method WIRE/EFT/CHEQUE/CASH/OTHER, amount > 0, ISO currency, paid_on, reference, note, void fields). Trigger `supplier_payment_guard`: no DELETE, recorded details immutable, a void can't be undone. Dev DB `tami` upgraded to 0012; the demo company's roles re-synced (`ensure_default_roles`).
- **Permissions:** `supplier.ledger.read` (Owner/Admin, Manager, Finance) and `supplier_payment.record` (Owner/Admin, Finance); both company-wide. Placeholders until the owner decides (BLOCKERS).
- **API:** `GET /suppliers/balances`, `GET /suppliers/{id}/ledger`, `GET/POST /supplier-payments`, `POST /supplier-payments/{id}/void`. Billed = declared qty × declared unit cost of POSTED shipments, rounded per line; lines without a cost are counted (`unpriced_lines`), never guessed. Balance = billed − non-voided payments per currency, no conversion (D-04). Damaged/rejected listed as returned, not deducted; landed costs listed per shipment, outside the supplier balance. Same reference for the same supplier refused while not voided (`DUPLICATE_REFERENCE`); future dates refused. Contract + `openapi.implemented.json` updated.
- **Web:** Suppliers sub-menu (Supplier list, Supplier payments). Supplier list shows each supplier's balance and links to `/suppliers/:id`: balance/billed/paid cards per currency, received/damaged/rejected per unit, tabs Ledger (running balance), Shipments (ordered → accepted, billed, unpriced lines, freight/duty) and Payments (void with reason). `/suppliers/payments`: all supplier payments + Record payment. `lib/suppliers.ts` + tests.
- **Tests executed:** `apps/api` ruff/format/mypy clean, pytest **217 passed** (3 new in `tests/test_supplier_ledger.py`: billed/paid/returned + running balance, duplicate reference, per-currency totals, void, DB guard, permissions, tenancy). `apps/web` eslint/tsc clean, vitest **112 passed** (4 new), Playwright **37 passed** (new `e2e/suppliers.spec.ts`; axe on supplier and payments pages).

## 2026-11-08 — Invoices: Create invoice (from a picking sheet, or direct)
- **Owner direction:** move the invoice editor into Invoices with a "Create invoice" button; allow invoicing from a picking sheet or directly ("sometimes we don't have any picking sheet for small orders").
- **API:** `POST /invoices/direct/preview` (invoice.prepare or invoice.issue; computes and rolls back) and `POST /invoices/direct` (invoice.issue, idempotent, D-01 hand-over confirmation). One transaction: a CONFIRMED order (source text "Direct invoice", prices frozen now through the same `orders.price_lines` the confirm step now uses), a VERIFIED sheet `<order>-D` ("Direct invoice: no picking sheet") with every line packed and no lot chosen, then the existing `invoicing.issue` (number, stock first-expiry-first-out under locks, PDF, document filing, email outbox). Refusals leave nothing behind (`INSUFFICIENT_STOCK`, `NO_PRICE`, `PRODUCT_INACTIVE`). Weighted billing stays on the picking-sheet path. Contract + `openapi.implemented.json` updated.
- **Web:** Invoices header "Create invoice" → `/invoices/new` with two tabs: picking sheets ready to invoice (→ existing editor) and Direct invoice (customer, products with the default case-size unit first, quantity; "Check prices and stock" preview; hand-over checkbox; confirm dialog; opens the issued invoice). Sales can preview but not issue. `lib/directInvoice.ts` + tests.
- **Test fix found on the way:** `tests/test_documents.py` counted archive PDFs of every company sharing the outbox; it now counts only its own company folder (it failed once the new direct-invoice tests ran before it).
- **Tests executed:** `apps/api` ruff/format/mypy clean, pytest **219 passed** (2 new in `tests/test_direct_invoice.py`: preview keeps nothing, issue once with idempotent replay, stock posted, refusals leave nothing, permissions). `apps/web` eslint/tsc clean, vitest **115 passed** (3 new), Playwright **39 passed** (new `e2e/new-invoice.spec.ts`; axe).

## 2026-11-08 — Overview work panels; Scan Center scanner picture and "Find scanners"
- **Owner direction:** Overview "with scrollable things … like reports"; a more accurate printer/scanner picture in the Scan Center; an option to find the scanners connected to the system.
- **Web (Overview):** new `OverviewPanels` under the existing dashboard: net sales for the last 30 days by week (Reports API), best sellers (30 days), orders waiting on the warehouse, customers paying late (receivables). Each panel shows only with its permission; list bodies scroll on their own (keyboard-focusable regions).
- **Web (Scan Center):** the new-scan picture is now an office multifunction scanner/printer (document feeder with pages, glass, touch panel, output tray, paper drawers; a light bar sweeps while scanning). Scanners tab: "Find scanners" per approved bridge; the page waits up to about a minute for the bridge's answer and lists "Scanners found on the office PC".
- **API / bridge:** migration `0013_scanner_discovery` (`scan_devices.discover_requested_at`, `detected_scanners` JSONB, `detected_at`). `POST /scan-devices/{id}/discover` (`scan.device.manage`, audited). Heartbeat answer carries `discover`; the bridge then sends `scanners[{name, driver}]` (max 20). `FakeDriver` lists a fake Toshiba; `Naps2Driver` runs `NAPS2.Console --listdevices --driver wia|twain` — **not verified with the office Toshiba (D-14)**. Dev DB `tami` upgraded to 0013. Contract + `openapi.implemented.json` updated.
- **Tests executed:** `apps/api` ruff (incl. `apps/bridge`)/format/mypy clean, pytest **220 passed** (new `test_find_scanners_on_the_office_pc`). `apps/web` eslint/tsc clean, vitest **115 passed**, Playwright **39 passed** (existing scans/overview specs with axe still pass). The "Find scanners" button itself has no browser test (needs a live bridge heartbeat); the API test covers the round trip.
- **Screenshots:** `apps/web/screenshots/overview-panels-1440.png`, `scan-center-1440.png` (git-ignored).

## 2026-11-08 — Card grid view for master data
- **Owner direction:** some list views replaced by a grid of cards; chosen: Products, Customers, Suppliers, Case sizes and Settings › Users open as cards, with a Grid / List switch (choice remembered per screen on the device). Ledgers and work queues stay tables.
- **Web:** `ui/CardGrid.tsx` (`CardGrid`, `EntityCard` with one stretched title link and separately focusable actions, `Tile`), `ui/ViewToggle.tsx`, `lib/viewMode.ts` (`useViewMode`; storage failures fall back to grid). Cards: Products (default case size, standard prices, status), Customers (terms, contact, owed/late from receivables), Suppliers (balance in words per currency, shipments, last received, Edit), Case sizes (one card per product with its sizes and Make default / Retire), Users (role, two-step, status, last seen, the same actions). Tables unchanged behind "List". No API change.
- **Tests executed:** `apps/web` eslint/tsc clean, vitest **117 passed** (2 new in `src/lib/viewMode.test.ts`), Playwright **40 passed** (new `e2e/grid-view.spec.ts`: each screen opens as cards + axe, card opens detail, List shows the table and survives a reload, keyboard Enter opens a card; six specs updated for the grid default).
- **API suite:** not re-run cleanly here: another session working in the same folder (OCR/Tesseract changes, uncommitted) was running pytest on the shared `tami_test` database at the same time, and the two runs deadlocked in `test_platform::test_migration_down_and_up` (+2 errors). This change has no API code; the last clean API run is 220 passed (`696b7c9`).
- **Screenshots:** `apps/web/screenshots/grid-*-1440.png` and `grid-*-390.png` (git-ignored). Seen at 390 px: the shared filter bar's search box runs past the right edge (existing `FilterBar` width, not new).

## 2026-11-08 — OCR module: real engine, supplier documents, review, Receiving
- **Engine:** Tesseract 5.5.4 (Homebrew) + poppler `pdftoppm`/`pdfinfo` + Pillow. `OCR_PROVIDER=tesseract` in the git-ignored `apps/api/.env`; tests pin `fake` and switch Tesseract on per test. PDF pages rasterized at 300 dpi; orientation (OSD) detected and the page straightened; greyscale + autocontrast; word confidences are Tesseract's own.
- **Document types (migration 0014):** Purchase order, Delivery note, Packing list, Receipt added (Supplier invoice = `VENDOR_DOCUMENT`). All supplier types are read automatically.
- **Supplier extraction (migration 0015, `supplier_ocr.py`):** header (supplier matched to your suppliers, document number, date, due date, currency, subtotal, tax, total) and lines (description, qty, unit, unit price, amount) — missing values stay null; ambiguous dd/mm flagged; qty×price≠amount, lines≠subtotal and subtotal+tax≠total flagged; same supplier + number on another reading or shipment → `POSSIBLE_DUPLICATE`. Header corrections keep the read value next to the fix (audited). `POST /ocr/extractions/{id}/to-receiving` (grn.record): new/open shipment + attached document + declared lines; no stock, cost or liability. Supplier readings can't become sales orders.
- **Scan Center:** several files at once with real byte progress (XHR); per-file stages Uploading → Reading text and extracting → Ready for review / failed with Read again, from server state; animated active step (still under reduced motion).
- **Review:** page images (`GET /scans/documents/{id}/pages/{n}`), page nav, zoom, view-only rotate, highlight of the hovered line; supplier header panel, prices as read, Send to Receiving.
- **Measured accuracy (synthetic typed documents rasterized to 300 dpi PNG — no text layer):** order sheet 3/3 lines exact upright and rotated 90° (one SKU misread `PLN-0O1` matched and sent for review); supplier invoice header 8/8 fields, lines 2/2 (sku, qty, unit price, amount). Not yet measured on real supplier paperwork.
- **Fixes on the way:** Homebrew's Tesseract install upgraded `simdjson`, which broke Node; `brew upgrade node` (v26.13.0) fixed it. The Celery worker must be started with `PYTHONPATH=src`.
- **Tests executed:** pytest **228 passed** (new `test_ocr_tesseract.py`, `test_supplier_ocr.py`); vitest **117 passed**; Playwright **41 passed** (new `e2e/scan-uploads.spec.ts`; `ocr.spec.ts` accepts the real engine's "Unreadable" and the new viewer); ruff/mypy/eslint/tsc clean.

## 2026-11-09 — Real product list; SKUs are exactly three letters
- **Owner direction:** remove the old products and everything built on them, load the products on the paper order sheet, and make SKUs three letters only.
- **Dev data (`tami`):** backup first in `checkpoints/pre-product-reset-2026-11-09/` (`tami.dump`, `api-var/`). Then all products were truncated, along with their history (orders, invoices, payments, shipments/receiving, stock, price books, scans/OCR, outbox, idempotency), and the ~1,450 leftover test-run companies were deleted. The demo company keeps its customers, vendors, staff, roles, warehouses, audit log and invoice numbering. The orphaned files in `apps/api/var/drive` are still on disk; removing them needs the owner's OK.
- **Catalog:** `scripts/load_catalog.py` loads the 108 order-sheet products (Vegetables 24, Peppers 11, Roots 17, Herbs 9, Others 11, Fruits 36), sold by the BOX. The sheet's own spelling is kept as an alias where the display name corrects it (e.g. "Tumeric" → Turmeric `TUR`). Idempotent.
- **SKU rule:** API `ProductIn.sku` must be 3 letters A–Z; lower case is accepted and stored upper case. Migration `0016_sku_three_letters` changes `products.sku` to `varchar(3)` with the check `^[A-Z]{3}$`, and refuses to run while any existing SKU breaks the rule. Synthetic seed, demo, test and Playwright SKUs renamed (CAS-FZ→CFZ, PLN-GR→PLG, RICE-JAS→RJS, CAS-01→CAS, …); lot codes are unchanged. Contract regenerated.
- **Tests executed:** ruff/format/mypy clean. pytest **286 passed** on a separate database (`tami_test_sku`, so as not to collide with another session's run on `tami_test`), including the new `tests/test_sku_rule.py`. Playwright not run: its fixture creates fresh test companies in the dev DB that was just cleaned, and the specs changed only in SKU strings. The web product form has no 3-letter hint yet (another session has uncommitted edits in `Products.tsx`); the API's error message is shown instead.

## 2026-11-09 — UX pass: smoother, steadier screens
- **Owner direction:** "optimize, enhance the ui, make it more smooth and professional with better ux/ui". Builds on the plain UI pass (`500f1e5`); palette, layout, radii and print layouts unchanged.
- **Motion (all off under `prefers-reduced-motion`):** each screen fades in once on arrival (200 ms), dialogs and the "Go to" list pop in, the phone menu slides in over a fading backdrop, and the sidebar width, chevrons, rows, chips and buttons ease their hover/press states. Tokens in `theme.css` (`--animate-enter/fade/drawer/pop`).
- **No jumps:** list screens keep their filters, table, loading, empty and error states in one white card. Loading shows a table-shaped skeleton at the real row height, card grids show card skeletons, detail screens and Overview show page-shaped skeletons, and sign-in shows the shell frame. `#main` keeps its scrollbar gutter, and every new screen starts at the top (it used to keep the previous screen's scroll position).
- **Tables:** on desktop, long tables scroll inside their card so the header row stays visible. Sort shows its direction. Rows use one tab stop (arrows, Home/End and Enter move and open), and keys pressed on a link inside a row no longer also open the row.
- **Keyboard:** Ctrl/⌘ K opens a "Go to" box in the top bar that lists every sidebar screen. `/` focuses the screen's own search; before, it targeted a header search that was hidden below 1536 px and wired to nothing. The phone menu moves focus in on open and back to the menu button on close. Fields show a soft green focus ring.
- **Hierarchy:** one black primary button per screen header (Overview, Invoices, Customers, Supplier payments and Users had green ones), a slightly smaller page title, empty states with an icon, and no "Active" chip on every product card (only Inactive or Expiry tracked).
- **390 px:** the filter bar wraps (search grows, selects share the row), card titles wrap their actions below, and figure cards drop the icon tile on phones. Products, Customers and Reports no longer scroll sideways (90, 107 and 72 px before).
- **Performance:** every screen except Overview and Login is lazy-loaded, and the other chunks are prefetched when the browser is idle. The OCR review queue search is debounced (`lib/useDebounced.ts`); it used to send one request per key.
- **Sign-in returns you to the page you opened.** A deep link opened while signed out used to land on Overview after sign-in; it now returns to that page, through two-step sign-in as well. Only in-app paths are accepted (`lib/returnTo.ts`).
- **Tests executed:** `apps/web` tsc clean; eslint clean on every changed file; vitest **120 passed** (3 new in `src/lib/returnTo.test.ts`); Playwright **44 passed** (41 existing + new `e2e/navigation.spec.ts`: Ctrl K / `/` shortcuts, no sideways scroll on 10 screens at 390 px, phone menu focus, return to the opened page after sign-in). `playwright.config.ts` now uses `reducedMotion: "reduce"`, so axe checks final colours (it flagged contrast mid-fade). Two specs still used the old `CAS-FZ` test ids from the SKU rename (`invoices.spec.ts`, `orders.spec.ts`). They were renamed to `CFZ` in the working tree but not committed here, because those files hold the SKU session's uncommitted edits. Each e2e run creates fixture companies in the dev DB.
- **Screenshots:** `apps/web/screenshots/ux/before/` and `ux/after/` (27 screens × 1440 and 390 px, git-ignored; `before` has no product detail, because the tour's deep link fell to Overview), made by `node scripts/ui-tour.mjs <dir>`. The script uses dev auto sign-in and exits 1 if anything scrolls sideways at 390 px.
- **Not done:** `CaseSizes.tsx` and `ProductPrices.tsx` (another session's uncommitted edits) still use `rounded-2xl`/green header buttons; they pick up the shared fixes but no page edits.

## 2026-11-09 — Notification Center
- **Scope:** a bell in the top bar (unread count, red when something critical is unread), a ~400 px panel under it (full-width sheet on phones), a history page `/notifications`, and Settings › Notifications. Design, guarantees and the event list: `docs/modules/14-notification-center.md`.
- **Real events only:** `audit.record` now hands every audit row to `notification_rules.on_audit` in the same transaction, so a notification exists exactly when the business change commits (each rule runs in a SAVEPOINT; a rule error is logged, never fails the command). Wired: order confirmed, pick submitted/verified, shortfalls, invoice issued, out of stock after an invoice, payment reported/confirmed/rejected, invoice overdue (reminder schedule), delivery counted, stock received (GRN), scan ready / OCR failed / scan failed, Drive copy failed, invoice email failed, any outbox job that gave up, and account/security events (lockouts, two-step paused/on/off, recovery code used/regenerated, password changed, reset by link or by an admin, sessions ended by an admin or by "sign out other devices", invitation accepted (to admins), scanner pairing mismatch; action names agreed with the user-management session). Website orders come from Order Connect calling `notify_safely`. Not wired: low stock against a reorder level (no threshold in the data model).
- **Data:** migration `0018_notifications` (numbered after `0017_user_management`; Order Connect is `0019`): `notifications` (unique `company_id, event_id`; in-app link check), `notification_recipients` (`seq` identity = stream cursor; unique per person; unread partial index), `notification_preferences`, `notification_delivery_attempts` (email via the outbox, unique per recipient and channel).
- **Live:** `GET /api/v1/notifications/stream` (SSE). Rows are read only after commit; the `after_commit` hook publishes a Redis wake-up, with a 10 s poll as a safety net. Event ids are the recipient `seq`, so a reconnect resumes from `Last-Event-ID`/`?after=` (max 50, then `resync`). In the browser one tab holds a Web Lock and owns the stream; other tabs get events over a BroadcastChannel (HTTP/1.1 allows ~6 connections per host).
- **Security:** every route is self-service (own rows only) and re-checks the stored permission + warehouse against the caller's effective grants on each read; company id checked on both tables; writes rate-limited (240/min/person); preferences use optimistic `version` + Idempotency-Key and are audited (`notification.preferences_update`); muting the security category needs `employee.manage`; critical alerts always reach the inbox. The route sweep's `SELF_SERVICE` list has the 11 new routes.
- **Preferences:** in-app, desktop (Web Notifications API, asked only when the person presses "Allow…"), email, sound (WebAudio chime), grouped stacks, quiet hours in company time (hold banners, desktop, sound and email, never the inbox), per-category in-app/email/desktop.
- **UI:** stacked tiles (`rounded-ctl`, priority tone on the icon tile: blue info, green success, amber warning, red critical, plus a "Critical"/"Warning" word), Today / Yesterday / Earlier, unread dot that toggles read, dismiss/restore, 3+ from one module fold into a stack. Panel: 180 ms pop in, 160 ms out (off under reduced motion), focus moves in, Escape / outside click / focus leaving closes it and focus returns to the bell. New-event banner slides in top right (not during quiet hours), screen readers hear a polite live-region line. History page: status chips, priority, category, search (URL-mirrored), infinite scroll with a "Load older" fallback.
- **Tests executed:** API ruff/format/mypy clean on all touched files. `pytest tests/test_notifications.py` **24 passed** (business event → notification; failed command and plain rollback → none, no wake-up; after-commit publish; duplicate event ids and DB unique constraints; idempotent replay; role/scope/company visibility incl. revoked access; deactivated staff; read/unread/archive/restore/read-all/bulk archive + counts; filters/search/keyset paging; SSE replay after reconnect and live delivery under 6 s; stream needs a session and is capped; quiet hours and muting rules; preferences API incl. version conflict and the security lock; email channel via outbox, sent once; lockouts by sign-in and by the password form; user-management security events; dead job alert; any-scope kinds). Full API suite on `tami_test_notify`: **434 passed, 1 failed** — `test_contract.py` (the committed `contracts/openapi.json` doesn't have this module's, user management's or Order Connect's new routes yet; regenerate with `scripts/export_openapi.py` when committing). Web: tsc clean, eslint clean on changed files, vitest **136 passed** (10 new in `src/lib/notifications.test.ts`), Playwright `e2e/notifications.spec.ts` **6 passed** (real events in a fresh fixture company; panel keyboard/Escape/outside-click; live arrival without reload + banner; read/unread/mark-all/dismiss/restore/filters/search in sync with the badge; preferences + quiet hours persist; desktop permission: blocked stays off, never asked on load, asked on "Allow…", then a real event shows a desktop notification while the app is in the background — via a recording stand-in, because headless Chromium can't grant the permission; 390 px sheet with no sideways scroll; axe WCAG 2.2 AA on the panel, page and settings). `node scripts/ui-tour.mjs`: all 27 screens, no horizontal overflow at 390 px with the bell in the header. `e2e/navigation.spec.ts` + `e2e/smoke.spec.ts` (shell, shortcuts, sign-in): **7 passed**. The rest of the Playwright suite was not re-run here (other sessions are mid-change in auth, Settings and Order Connect).
- **Screenshots (git-ignored):** `apps/web/screenshots/notifications-panel-1440.png`, `notifications-panel-390.png`, `notifications-center-1440.png`, `notifications-center-390.png`, `notification-settings-1440.png`.
- **Not done / open:** low-stock thresholds (owner decision); real email provider (D-15); the OpenAPI contract file (see above).

## 2026-11-09 — User management & authentication
- **Owner decisions (asked in session):** the six roles map onto the existing role codes with **unchanged access**: Owner / Admin → **Super Admin**, Manager → **Admin**, Sales / Operator → **Sales**, Warehouse, Finance → **Accounting**, plus a new read-only **Viewer** (products, customers, orders, stock only). The permission matrix is fixed (access changes by assigning a role). Two-step sign-in is **built for everyone but not required for anyone**: the company flag `require_two_step` exists and is off. Icons: the repo's own `ui/Icon.tsx` set (no new icon package).
- **Kept from the existing auth (no second system):** Argon2id hashes, opaque HttpOnly SameSite=Strict session cookies with absolute + idle expiry, CSRF double token + Origin check, per-account lockout, per-IP throttle, TOTP secrets encrypted with Fernet, append-only audit log, `employee.manage` scoping.
- **DB:** migration `0017_user_management`: `auth_tokens` (invite / password-reset links: SHA-256 only, unique, single use, expiry, revocation; partial index on open links), `mfa_recovery_codes` (SHA-256, single use, unique per person), `employees.last_login_at` / `password_changed_at`, `auth_sessions.ip_address`, index `audit_events(actor_id, created_at)`; roles renamed, `privileged=false`, Viewer added to every company. Non-destructive; the downgrade restores names and removes Viewer. Dev DB `tami` upgraded to `0017` (the Order Connect session later added `0018`/`0019` on top).
- **API:** two-step is optional: a session owes a code only if the person turned it on (or the flag is on), and until then it holds **no** permissions and self-service answers `MFA_REQUIRED`. New: `POST /auth/mfa/enroll` (password again from My account), `/mfa/enroll/confirm` (turns it on, returns 10 recovery codes once, signs out other sessions), `/mfa/verify` (code **or** recovery code), `/mfa/disable` and `/mfa/recovery-codes` (password + code); `POST /auth/password/forgot` (always 202, same text; 3 mails/account/hour; per-IP limit), `/auth/password/reset` and `/auth/invitation/accept` (single-use link token, signs out everywhere on reset), `/auth/token/inspect`; `GET /auth/sessions`, `POST /auth/sessions/{id}/revoke`, `/auth/sessions/revoke-others`, `GET /auth/activity`; Super Admin: `POST /employees` now **invites** (no password), `POST /employees/{id}/invite`, `GET /roles`, `GET /security/settings|sessions|events`, `POST /security/sessions/{id}/revoke`. Staff rows show status Active / Invited / Suspended and last login. Last Super Admin rule counts only active, signed-up, company-wide Super Admins. Security notices are emailed on password change/reset, two-step on/off and recovery-code use. Contract regenerated.
- **Bootstrap:** `PYTHONPATH=src uv run python -m pro_trading.create_super_admin --company … --email … --name …` (hidden prompt or `--password-stdin`; refuses when the company already has an active Super Admin; creates the company, six roles and a first warehouse if missing). No public sign-up route exists.
- **Email:** dev/test mailbox = `email_messages`. No provider in staging/production yet (D-15): links are withheld there and a warning is logged (BLOCKERS).
- **Web:** Login (Forgot password link, notices), `/forgot-password`, `/reset-password#token=…`, `/accept-invite#token=…` (token read from the fragment, removed from the address bar), two-step page with recovery-code option and policy setup. Settings: **My account** (profile, role, what you can work with, change password, two-step on/off with QR + recovery codes copy/download, new codes, where you're signed in, recent sign-in activity), **User management** (search, status + role filters, grid/list, Add user = invitation dialog with permission summary, Manage dialog: name/role with confirmation, resend invite, sign out everywhere, turn off two-step, temporary password, suspend/reactivate), **Roles & permissions** (role cards + permission matrix by area), **Security & sessions** (sign-in rules, company sessions with sign-out, security events with filters and paging). Sidebar shows the person's role name.
- **Tests executed:** checked on the committed tree alone (exported from the index, so other sessions' uncommitted work is not included), API suite on its own DB `tami_test_mine`: `ruff check` clean, `mypy src` clean, **pytest 352 passed** (33 new in `tests/test_user_management.py`: only Super Admin creates users / changes roles / reads security pages (13 routes × 5 roles → 403), Viewer is read-only, Argon2id hashing, invitation accept / single use / expiry / resend / suspend, forgot-password generic answers + per-account cap, reset single use / expiry / signs out everywhere, lockout, idle and absolute session expiry, my sessions + Super Admin session revocation, login history and security events, last Super Admin rules, bootstrap command; `tests/test_mfa.py` rewritten, 12: optional by default, turn on with password + code, code asked only when on, other sessions signed out, replay refused, recovery code single use, new codes replace old, turn off, policy flag forces setup, no secrets in audit). `ruff format --check` flags `tests/test_inventory.py:119` and `tests/test_ocr.py:179`; both are lines already in HEAD, not changed here. Web: `tsc -b` clean, eslint clean on every changed file, **vitest 126 passed** (6 new in `src/lib/staff.test.ts`). Playwright (shared dev servers, working tree): `settings.spec.ts` **6/6** (invite → accept, forgot/reset, optional two-step with code and recovery code, matrix + security events + session revocation, 390 px with no sideways scroll, axe on every screen) and `smoke.spec.ts` **4/4**. Of the 15 specs updated for optional two-step, 22 tests passed; 5 failed after sign-in at a `getByRole("status")` strict-mode clash with the Notification Center session's new live region (uncommitted, not part of this change; reported to that session) and 6 serial follow-ups did not run. Full-suite runs here were also disturbed by `uvicorn --reload` restarts from that session's edits.
- **Found on the way:** the matrix's `sr-only` "no" labels are absolutely positioned and stretched `#main` sideways at 390 px; the scroll region is now `relative`. The My account grid used an implicit column that grew to its longest no-wrap line; it now uses `minmax(0,1fr)` columns.

## 2026-11-09 — Order Connect (Module 13: website order forms)
- **Scope:** staff build customer order forms, embed them on the company WordPress site with a plain iframe (no plugin) and receive submissions in Pro Trading. Approved submissions become **one DRAFT sales order** only: no invoice, stock movement or reservation. Design, data, permissions and protections: `docs/modules/13-order-connect.md`; owner decisions D-21…D-30 in `docs/decisions/OPEN_DECISIONS.md`.
- **DB:** migration `0019_order_connect` (after `0017_user_management` and `0018_notifications`): `order_connect_settings`, `order_forms`, `order_form_versions` (DRAFT/PUBLISHED/RETIRED, published immutable by trigger), `order_form_fields` (frozen once published), `order_form_allowed_domains`, `order_form_logos`, `order_form_submissions` (unique `(form_id, client_token)`, unique `sales_order_id`, check `CONVERTED ⇔ sales_order_id`, values immutable except one-way erasure, no deletes), `order_form_submission_items` and `order_form_submission_events` (append-only); `sales_orders.source` accepts `WEB`. Up/down tested on `tami_test_oc`; dev DB `tami` upgraded to `0019`; the demo company's roles got the four new `order_connect.*` grants through `ensure_default_roles` (no reseed).
- **API:** staff `/api/v1/order-connect/*` (overview, badge, forms, draft/publish, preview config, embed info, logo upload, allowed websites, settings, submissions inbox/detail/matches/read/status/notes/convert/erase); public `/api/v1/public/order-forms/{public_id}` (+ `/submissions`) and `/api/v1/public/logos/{id}`; `/embed/{public_id}`, `/embed/preview`, `/embed/assets/*` (API-served page, vanilla JS/CSS). Staff alerts go through the Notification Center (`notify_safely`, kind `order_connect.submission`, company-wide holders only); outbox event `order_connect.submission_received` (optional receipt email); daily Celery beat `order_connect_retention`. New settings: `PUBLIC_APP_ORIGIN`, `ORDER_CONNECT_KEY`, `ORDER_CONNECT_MAX_BODY_BYTES` (production refuses the dev key or a non-https public origin). `main.py`: `/embed/*` gets no `X-Frame-Options` (its CSP `frame-ancestors` decides); the public API also accepts `PUBLIC_APP_ORIGIN` as Origin. Vite proxies `/embed` to the API in dev. Contract regenerated.
- **Web:** sidebar Sales › Order Connect (globe icon, live unread badge) with Overview, Form builder (fields with drag-and-drop and arrow reordering, inline editing, text, design, logo, product scope, standard prices, draft/publish, pause, live desktop/mobile preview over postMessage), Submissions (live inbox, filters, search, detail with timeline, notes, review actions, customer matching, convert to draft order, privacy erasure), Embed & share (iframe code, direct link, allowed websites, WordPress steps), Settings. Orders page names `WEB` orders "sent from the website, converted".
- **Tests executed (2026-11-09, on HEAD `5efe1d7` + this change):** API `ruff check src tests migrations` clean, `mypy src` clean (68 files); `pytest` on `tami_test_oc`: **436 passed, 0 failed** (57 new in `tests/test_order_connect.py`; `test_security_routes.py` treats `/api/v1/public/*` as anonymous by design; `test_security.py` production-settings cases for the new key/origin). `ruff format --check` still flags `migrations/versions/0015_ocr_supplier_fields.py`, `tests/test_inventory.py`, `tests/test_ocr.py` (already in HEAD, not touched here). Web: `tsc -b` clean; eslint clean on every changed file; **vitest 150 passed** (14 new in `src/lib/orderConnect.test.ts`); Playwright `e2e/order-connect.spec.ts` **6 passed** (build → preview → publish; settings + website validation; a non-allowed site (`evil.localhost`) refused by CSP `frame-ancestors`; a customer ordering inside a third-party page at 390 px with no sideways scroll while the staff inbox, badge and toast update live; review → approve → convert to a DRAFT order; axe WCAG 2.2 AA on staff screens). Full Playwright suite: **49 passed, 5 failed, 6 not run** — the 5 are `customers`, `payments`, `products`, `scans`, `suppliers` specs hitting a `getByRole("status")` strict-mode clash with the Notification Center's live region in HEAD; that session has a one-line fix in its working tree (17 passed there), not committed at the time of writing.
- **Screens:** `node scripts/order-connect-tour.mjs screenshots/order-connect` → 16 screenshots (8 screens × 1440 and 390 px, git-ignored), no horizontal overflow at 390 px. Manual browser check on the dev stack: form created and published, order sent from the embed page, alert/badge/bell updated live, submission reviewed, approved and converted to `SO-20261109-0001` (synthetic data in the demo company).
- **Not done / not verified:** a real WordPress page on a staging site (the e2e "website" is a local page); Celery worker not restarted, so the receipt-email handler and the retention beat load on its next restart; captcha (D-26); a reopened spam submission has no stored product lines, so it can't be converted from Order Connect (create the order in Orders). Found on the way (not changed): a new browser tab that reuses the cookie session has no CSRF token in sessionStorage, so its first save fails with `CSRF_FAILED` and signs the person out.

## 2026-11-09 — Owner product photos for all 108 products
- **Change:** the 108 photos supplied by the owner (`tamisha-all-product-images`, 1024 px PNG on white) replace the Wikimedia Commons stand-ins in `apps/web/public/products/<SKU>.webp`. Matched to SKUs by product name or order-sheet alias (108/108, one-to-one, e.g. `wierri-peppers.png` → WIR, `tamerind.png` → TAM); converted to 480 px square WebP, quality 82, metadata dropped (≈2 MB in all). Wiri Wiri Peppers (WIR) now has a photo too. `CREDITS.md` lists SKU → source file. Used unchanged by product cards, the product page and the Order Connect customer form (`/products/<SKU>.webp`).
- **Tests executed:** all 108 files served by the dev server as `image/webp`, 480 px, decodable (browser check); a contact sheet of all 108 with SKU labels checked by eye; Playwright `products.spec.ts`, `grid-view.spec.ts`, `order-connect.spec.ts`: **9 passed**.

## 2026-11-09 — New sign-in page; Notification Center becomes Activity
- **Owner direction:** a sign-in page like the supplied design (photo panel + "Welcome back"), a reset-password link on it, and the Notification Center changed into an Activity timeline like the supplied list (who did what, a tag per row, "Show my activities only").
- **Sign-in:** `components/AuthFrame.tsx` now has `AuthShell`: one large card with a brand-green panel ("Wholesale Produce Trading & Distribution", SOURCE · TRADE · SUPPLY) over a photo, and the form on the right; phones get a photo strip on top. Sign-in, two-step, forgot, reset and invitation screens all use it. The photo is cropped from the owner's design image (`public/brand/sign-in-hero.webp`, 560×412, WebP q84, no metadata). Fields have icons, the password has a show/hide button (named by its content so `getByLabel("Password")` still finds only the field), and "Forgot password? Reset it" sits under the password and opens `/forgot-password`. No gradients or decorative shadows (the card uses the dialog shadow); `e2e/settings.spec.ts` now expects the "Welcome back" heading.
- **Activity:** `GET /api/v1/activity` (`pro_trading/activity.py`, `routes/activity.py`) turns committed, successful audit rows for business actions into "Person verb — Object (detail)" with a tag and tone (green added/confirmed, blue created/recorded, purple price, orange stock, red removed/cancelled). Objects and amounts come from the audit row or the stored record; links are dropped when the record no longer exists. Everyone sees their own actions; `audit.read` adds colleagues' (company-wide, or the warehouses the grant covers); other users' rows by `actor_id` are refused (403) without it. Keyset paging plus a total count. Sign-ins, settings and other housekeeping never appear. Route sweep: `/api/v1/activity` added to `SELF_SERVICE`.
- **Web:** `/activity` (sidebar › Analytics › Activity) with tabs Activity / Notifications. Activity: "N Activities", "Show my activities only" switch (only for audit readers; others see "Showing your own activity"), date + time column, coloured dot on a rail, row card with initials, sentence, tag and a "…" menu (open the record, only this person's activity; arrow keys and Esc). Refreshes quietly when a notification arrives or the tab regains focus; loads older rows on scroll. Notifications tab = the former Notification Center (`/notifications` redirects there); the bell panel's footer links to both.
- **Tests executed:** API `tests/test_activity.py` **5 passed** (sentences from real flows, money/stock details from records, own-only + 403 for others' rows, warehouse-scoped audit reader and other company, stable paging) with `test_notifications.py` and the route sweep (32 passed together). Full API suite on `tami_test_notify`: **460 passed, 1 failed** — `test_supplier_ledger.py::test_ledger_billed_paid_returned` (entry order PAYMENT before SHIPMENT; no supplier code changed in this work, and the run was at ~22:45 EST, past midnight UTC). Web: tsc clean, eslint clean on changed files, vitest **154 passed** (4 new in `src/lib/activity.test.ts`). Playwright `activity` + `notifications` + `settings` + `smoke` + `navigation` specs: **23 passed** (new `e2e/activity.spec.ts`: sign-in photo panel, show/hide password, reset link, 390 px; activity rows from real API actions, my-only switch, row menu by keyboard, one-person filter, link to the order, own-only for sales, `/notifications` redirect, 390 px; axe WCAG 2.2 AA on sign-in and Activity). Playwright's Chromium had to be reinstalled (`npx playwright install chromium`) after the project moved to `~/Projects`.
- **Screenshots (git-ignored):** `apps/web/screenshots/sign-in-1440.png`, `sign-in-390.png`, `activity-1440.png`, `activity-390.png`.


## 2026-11-09 — Product cards to the owner's target design
- **Owner direction:** "i need ui just like this, icon, bg subtle color for the image bg, icons, smaller texts, proper box shadow, proper everything, professional grade ui for products" (target image in the shared notes, `product-cards-target.webp`).
- **Web:** new `ui/ProductCard.tsx` used by the Products grid. It has the photo on a soft green tile (`mix-blend-multiply`, so white photo backgrounds take the tint), the name with an "● Active"/"Inactive" pill and a "⋯" menu, then a divider and icon rows: Case size (box), Price (tag) and Sold by (stack). Empty values are muted and set values bold; text is smaller; every card is the same height. Product cards alone use a soft shadow and `p-4`; the other master-data grids keep `EntityCard`, unchanged.
- **"⋯" menu:** new `ui/MoreMenu.tsx` (Open product, Open in new tab, Copy SKU). It follows the menu-button keyboard pattern: arrows and Home/End move, Esc or Tab or a click outside closes, and focus returns to the button. New icons: `more`, `copy`, `external`.
- **Layout:** columns of at least 26rem, so 2 at 1440 px with the sidebar open, 3 with it collapsed or on wider screens, and 1 on phones. Three columns at 1440 with the sidebar open would cut most names. On narrow cards (container query) the photo shrinks, the pill moves under the "⋯" next to SKU · Category, and the name takes the full width.
- **Also:** `scripts/ui-tour.mjs` waits for skeletons instead of "networkidle", because the live notification and inventory streams keep the network busy.
- **Tests executed:** tsc clean; eslint clean on the changed files; vitest **162 passed**; Playwright `grid-view`, `products`, `catalog` and `navigation` specs **8 passed**. Screenshots: `apps/web/screenshots/products-cards-{1440,390}.png` and `products-cards-menu-*.png` (git-ignored).

## 2026-11-09 — Trash bin: Super Admin deletes, 30-day restore, delete requests
- **Owner decisions (asked in session):** the delete classification was approved, built as a Mac-style Trash. The Super Admin moves records to the Trash, where they wait 30 days and are then deleted forever automatically. Staff who can edit a record can only **ask** for a delete; the Super Admin approves or dismisses the request. Deleting needs two-step sign-in turned **on** and passed for the session. Note: the dev `user` account has not turned it on yet.
- **Deletable:**
  - Always: notifications and price-book drafts.
  - Only while nothing else references the record: products (with their aliases and case sizes), case sizes (not a product's default), customers, customer groups, suppliers, **draft** sales orders, **aborted** picking sheets, finished scans (with their filed copy, plus the scanned file when nothing else uses it), filed documents (not invoice PDFs), users with no history (unanswered invitations, unused accounts), and website order entries that weren't converted.
- **Never deleted:** issued invoices (and their lines and PDFs), the stock ledger, lots and balances, payments and allocations, supplier payments, the audit log, published price books, confirmed or later orders, verified or invoiced picks, shipments and GRNs, OCR reads, scanners and converted website entries. These keep their void, cancel, reverse, archive or deactivate actions.
- **How it works:**
  - Moving a record to the Trash removes it, and the rows that belong to it, from the live tables, so no other query or screen needs a filter. Every removed row is kept in `trash_items.payload` (in insert order). Restore re-inserts those rows; if something now clashes (the SKU was reused, or a parent record is gone) it answers 409 `RESTORE_CONFLICT`.
  - The reference check counts every non-cascading foreign key in the schema that points at a removed row. It also checks three references that aren't foreign keys: order-form product lists, a pick's stock movements and order amendments, and an order's source document. Anything found gives a 409 `IN_USE` that names each kind of record with a count and examples. Kind rules (draft orders only, and so on) give 409 `NOT_DELETABLE`.
- **DB:** migration `0020_trash`.
  - New tables: `trash_items` (unique per company/kind/item, `purge_after` set 30 days ahead) and `delete_requests` (one PENDING request per record).
  - Permission `trash.manage`, granted to the Super Admin role (OWNER_ADMIN) only, with an assertion in `permissions.py`.
  - The guard triggers on products, case sizes, sales orders, scan jobs, source documents, filed documents and order-form submissions (with their items and events) are split in two. The UPDATE trigger is unchanged. A new DELETE trigger skips the guard only while the transaction has `pro_trading.trash = on` **and** the row is in a deletable state; foreign keys still apply.
  - Invoice, stock, payment, supplier-payment and audit triggers are untouched.
  - Dev DB `tami` is upgraded to `0020`.
- **API:**
  - Moving to the Trash: `DELETE` on `/products/{id}`, `/unit-profiles/{id}`, `/customers/{id}`, `/customer-groups/{id}`, `/vendors/{id}`, `/price-books/versions/{id}`, `/sales-orders/{id}`, `/pick-attempts/{id}`, `/scans/jobs/{id}`, `/documents/{id}`, `/employees/{id}`, `/notifications/{id}` and `/order-connect/submissions/{id}`.
  - Trash: `GET /trash`, `POST /trash/{id}/restore`, `DELETE /trash/{id}` (delete forever) and `POST /trash/empty`.
  - Requests: `GET` and `POST /delete-requests`, `POST /delete-requests/{id}/approve` and `POST /delete-requests/{id}/dismiss`.
  - Rules: the Super Admin plus a two-step sign-in that is both enrolled and verified; otherwise 403 `TWO_STEP_REQUIRED`, and the denial is audited. Every mutation takes an Idempotency-Key, and repeating a delete on a record already in the Trash answers 200 with `already_in_trash`.
  - Audit actions: `trash.moved` (actor, target, label, a snapshot without secrets or file bytes, and counts of the rows removed with it), `trash.restored`, `trash.purged`, `trash.auto_purged` (no actor), `trash.delete_requested` (notifies everyone with `trash.manage`) and `trash.request_dismissed`.
  - Worker: hourly beat `pro_trading.purge_trash`. The OpenAPI contract was regenerated without the Activity routes, which another session hasn't committed yet.
- **Fixed on the way:**
  - Order numbers and scan numbers were "row count + 1", which reuses an existing number once a record has left the table. They are now 1 + the highest number used, by a live record or one in the Trash (`trash.next_number`).
  - Drive sync of a document that was trashed before it synced now ends quietly instead of retrying.
- **Web:**
  - `/trash` (Super Admin; sidebar entry above Settings) has two tabs. **In Trash** shows name, type, deleted by, when and days left, with Restore, Delete forever and Empty Trash. **Delete requests** has Open, Move to Trash and Dismiss.
  - `ui/DeleteAction.tsx` shows Delete, Request delete or nothing, depending on the person. Its confirm dialog names the item; a refusal lists what uses the record; with two-step sign-in off it explains how to turn it on. An Undo toast follows a delete.
  - Wired into list and detail pages: Products, Case sizes, Customers (and groups), Suppliers, Users, Orders, Picking, Prices, Scans, Documents and Order Connect submissions.
- **Tests executed** (on HEAD + this change only, exported to a scratch folder, so other sessions' uncommitted work is left out):
  - API: `ruff check src tests migrations` clean; `mypy src` clean (71 files). `ruff format --check` flags only `tests/test_inventory.py` and `tests/test_ocr.py`, both already in HEAD.
  - API pytest on `tami_test_trash`: **455 passed, 1 failed** (`test_supplier_ledger.py::test_ledger_billed_paid_returned`, see below; `test_contract.py` passes with the regenerated contract). New tests, 13 in all:
    - `tests/test_trash.py`: who may delete (Super Admin with two-step only; Admin, Sales, Accounting and a warehouse-scoped Super Admin get 403); move, restore and delete forever, with audit rows (actor, snapshot, removed rows); Idempotency-Key replay and `already_in_trash`; restore clash; 30-day purge; Empty Trash; 409 `IN_USE` naming prices and the order number; state rules (draft order, aborted pick, default case size); guard triggers still refuse outside the Trash; users (invited user restored with their role, self refused, signed-in user refused); delete requests (ask, de-duplication, refused up front, notification, approve, dismiss); order numbers not reused; notifications trashed and restored; scans and filed documents (OCR-read scan kept, removed rows counted, file bytes kept out of the audit).
    - `tests/test_trash_order_connect.py`: website entry trashed and restored; a converted entry is refused.
  - Web: `tsc -b` clean; eslint clean on every changed file; vitest **158 passed** on the export (8 new in `src/lib/trash.test.ts`; 162 in the working tree with the Activity session's tests).
  - Playwright (working tree, shared dev servers): `e2e/trash.spec.ts` **3 passed**: the warehouse user sees no Delete; sales sees no Delete on customers but can ask for a delete on the draft order; the Super Admin without two-step gets the turn-it-on dialog; a referenced customer is refused with its sales order named; a new customer is deleted from its page, brought back with Undo, deleted from the list, restored from Trash and deleted forever; the request is dismissed. Axe WCAG 2.2 AA passes on the dialogs, the Trash page and the requests tab.
  - Full Playwright suite: **59 passed, 2 failed, 6 not run**. `order-connect.spec.ts` (builder field detached mid-fill) passed on re-run. `reports.spec.ts` fails only after 19:00 EST and passes with the browser set to UTC, so it isn't related to this change.
- **Found on the way (not changed):** Order Connect `POST /order-connect/submissions/{id}/read` (204) sometimes raises "Response content longer than Content-Length" in `bodylimit.py` (seen in `logs/api.log`).
- **Not done / open:**
  - **Delete on notification rows (done in a follow-up commit):** on Activity › Notifications the Super Admin has a Delete button on each card (`NotificationCard` `onDelete`, page-level `DeleteAction`, list and unread count reload after a delete or Undo). It isn't in the bell popover, which closes on outside clicks. The Trash "Open" link for website entries now goes to `/online-orders/requests/{id}` (Order Connect renamed Online Orders). Tests: tsc clean, eslint clean, vitest **162 passed**; Playwright `trash.spec.ts` **4 passed** (new: delete a notification, Undo, axe) plus `notifications.spec.ts` and `activity.spec.ts` (13 passed with them). The e2e helper now waits for a fresh 30 s code, because each two-step code works only once.
  - **Products card ⋯ menu:** the Delete entry lives on top of another session's uncommitted product-card work, so this commit puts it in the card's actions slot instead.
  - **Celery worker not restarted:** the purge beat loads on the worker's next restart.
  - **Order and picking lists carry no `warehouse_id`:** "Request delete" can show on another warehouse's row, and the server then refuses (one warehouse today).
  - **Pre-existing evening failures (after 19:00 EST):** `test_supplier_ledger.py::test_ledger_billed_paid_returned` dates shipments in company UTC and payments by the server's local date; `e2e/reports.spec.ts` builds "today" in the browser's local time against company UTC.

## 2026-11-09 — Online Orders: rename, product-card cart, quote requests, Elementor embed
- **Rename (owner request):** "Order Connect" is now **Online Orders** everywhere people see it: sidebar (Overview, Form builder, Requests, Embed & share, Settings), page titles ("Order & quote requests"), alert titles ("New online order …" / "New quote request …"), receipt emails, permission descriptions and URLs `/online-orders/*`. Old `/order-connect/*` links (saved notifications, trash entries, bookmarks) redirect, `/submissions/` → `/requests/`. Code, tables, API paths and permission codes keep the `order_connect` name (no migration).
- **Customer page redesigned as a shop (owner's product-card design):** search + category filter, cards like the staff product grid (photo on a soft green tile, name, SKU · category, Sold by / Case size / Price rows; case size from the packing profiles only when recorded; price only when the form shows prices, now in every unit kept, e.g. "CAD 4.49 / lb"), "Add to order" → quantity stepper, cart panel beside the products (stays in view while the website page scrolls), phone: "View order" bar + bottom sheet, checkout with **Place an order / Request a quote**, details, summary, send. No header or footer inside the website (title/logo only on the direct link). New forms default to rounded corners.
- **Website code for Elementor:** iframe + a short script (HTML widget). The form posts its height; the script sizes the frame (page scrolls, not an inner box), posts the visible part of the frame so the cart follows, and scrolls to the form on step changes. Origin/frame checks both ways; numbers only. "Iframe only" fallback offered for sites that block scripts.
- **Quote requests:** `request_type` (`ORDER`/`QUOTE`) in the public body, kept in the submitted values (survives erasure; no migration); inbox label + `?type=` filter; conversion text says "Online quote request …". Quotes convert like orders (no quote document yet, D-31).
- **Tests executed:** API on `tami_test_oc` (HEAD + this change only, see the commit): `ruff check`, `mypy src` clean; `tests/test_order_connect.py` + `tests/test_security_routes.py` **62 passed** (new: quote request stored/alerted/filtered/kept after erasure, quote receipt email wording, case size + per-unit standard prices in the public config, website code checks origin and frame). Web: `tsc -b` clean, eslint clean on changed files, **vitest 165 passed**, Playwright `e2e/order-connect.spec.ts` **6 passed** (build/preview checkout/publish; settings + website validation; a non-allowed site refused by CSP; a customer at 390 px on a page with header and footer, using the copied website code: no form header, frame height equals content height, cart sheet, quote request, staff inbox/toast/badge live with the "Quote request" label; review → approve → convert; old `/order-connect/submissions/{id}` link opens `/online-orders/requests/{id}`). Screenshots: `node scripts/order-connect-tour.mjs screenshots/online-orders` (staff screens + customer page, 1440/390, no sideways scroll) plus shop/cart/checkout inside a stand-in website page (git-ignored).
- **Full API suite** on a clean worktree of HEAD + this change (other sessions' unfinished work excluded), DB `tami_test_oc2`: **462 passed, 1 failed** — `test_supplier_ledger.py::test_ledger_billed_paid_returned`, which fails the same way on HEAD alone: run after 19:00 Toronto time (already the next day in UTC) the ledger sorts the payment before the shipment. Pre-existing, not touched here. Contract regenerated from that worktree (only this change's `type` filter added).
- **Not verified:** the real tamishatrading.com Elementor page (the website here is a local stand-in page); the auto-height script needs the HTML widget to allow scripts.

## 2026-11-09 — Sales invoice template (D-12) and TT invoice numbers
- **Owner input:** new design image, paper invoice 086494 and a black-and-white logo (side-session notes `invoice-template/`).
- **Template `sales-invoice-1`** (`invoice_pdf.py`), replacing `provisional-1` for new invoices only (issued PDFs are stored bytes and don't change). Follows the design in black and white: greyscale logo embedded as a JPEG image (DCTDecode, DeviceGray), letterhead, SALES INVOICE banner, Invoice No./Date/Bus #/Vendor # box, Page n of N, Bill To / Ship To, Salesperson / Customer PO No. / Terms strip, Qty/Units/Description/LBS/KG/Unit Price/Amount table (no S/N column) with ruled blank rows, totals of quantity and weight, the three notes, Gross/Tax/Net, and one signature: "Received By (Name & Signature)". Still dependency-free and deterministic. `_Page`/`_assemble` output for the OCR test sheets is unchanged.
- **Numbering:** `TT2026-000001` (`INVOICE_PREFIX` default `TT`, format `{prefix}{year}-{n:06d}`). The yearly sequence carries on from numbers already issued, so the dev DB went INV-2026-000008 → TT2026-000009. 999,999 a year ≈ 2,700 a day.
- **Migration `0021_invoice_template`:** `companies.address/phone/fax/email/business_no/default_salesperson` (existing rows get Tamisha's letterhead from the paper invoice, Bus # 803444827, salesperson Andy Narine); `customers.bill_to_address/ship_to_name/ship_to_address/fax/vendor_no`. Rollback: `alembic downgrade 0020`.
- **API:** customer create/patch/read carry the new fields (addresses trimmed line by line); issue (sheet and direct) accepts `salesperson` and `customer_po`; snapshot lines carry `pack_lb` (lb per unit from the default case size, or the entered weight); `GET /invoice-settings` (any invoice permission). Contract regenerated from HEAD + this change only.
- **Web:** Customers form/details have fax, vendor #, bill-to and ship-to; Create invoice › Direct shows today's date, salesperson (default Andy Narine), Customer PO No., the customer's bill-to/ship-to/terms/vendor #, and lists the products of the customer's last invoice with blank quantities (left blank = not billed; a prefill never overwrites what someone already picked or typed).
- **New companies get the letterhead:** `bootstrap.apply_letterhead` fills it when `create_super_admin` (fresh production database) or the demo seed creates a company; the migration only fills companies that already exist. The E2E fixture company carries a synthetic Bus # and salesperson.
- **Tests executed** (HEAD + this change only, exported to a scratch folder, so other sessions' uncommitted work is left out):
  - API: `ruff check src tests migrations scripts` clean; `mypy` clean (75 files). pytest on `tami_test_invtpl2`: **471 passed, 1 failed** of 472: `test_supplier_ledger.py::test_ledger_billed_paid_returned`, the known failure after 19:00 EST (see the Trash entry above). `test_contract.py` passes with the regenerated contract.
  - New `tests/test_invoice_template.py` (11 tests): same snapshot gives the same bytes; no colour operators and one DeviceGray JPEG image; the layout's words in order (header box, bill/ship to, salesperson strip, `Qty Units Description LBS KG Unit Price Amount` with no `#` column, totals, notes, Gross/Tax/Net, one signature); blank fields print a dash; weighed / KG / piece / no-case-size rows; long text cut to its box; `TT2026-000001` format; the year's sequence carries on (41 then `TT{year}-000042`); issue copies the customer record, company defaults, salesperson and PO into the snapshot and the stored PDF; salesperson length checked; new companies get the letterhead. Updated: pagination (25 rows a page, "Page: n of N", totals once, one signature), `INV-` to `TT` in API tests and five Playwright specs, INV-005 checks the new template.
  - Web: `tsc -b` clean; eslint clean on changed files; vitest **166 passed** (new: prefilled lines without a quantity aren't billed; last-invoice products listed once, weighed boxes as boxes; a prefill never replaces a pick or typed quantity; customer invoice fields create/patch).
  - Playwright (working tree, shared dev servers, one worker): `new-invoice`, `invoices`, `documents` and `payments` specs pass; `new-invoice.spec.ts` now also checks the salesperson default and the customer panel. Under load the owner test failed 2 of about 20 runs waiting for the invoice page after a successful issue: the API logged 25-40 ms for every issue, the browser saw one take 4.6 s, while other sessions' edits were reloading the dev servers. A real race found this way is fixed: a product picked while the prefill was loading could be dropped.
- **Dev app check:** TT2026-000010 issued from Create invoice › Direct in the browser (prefilled from TT2026-000009, one product left blank and skipped); PDF and PNG saved to the notes folder with a render of paper invoice 086494 in the new layout.
- **Dev data added for that check:** case sizes and box prices (price book `PB-2026-11-BOX`, based on PB-2026-03) for 8 products from invoice 086494, 40 boxes each in MAIN, and invoice fields on Island Grocers (synthetic).
- **Not done / open:**
  - No screen to edit the company letterhead / Bus # / default salesperson yet (set by the migration; change in SQL until a Settings › Company page exists).
  - The pick-sheet invoice page doesn't show salesperson / PO fields; the company default salesperson is used.
  - Tax: the template prints "Tax Amount" with the computed amount (CAD 0.00 while tax is unassessed, D-03) and no tax note; the old "not assessed" wording is gone. Owner/accountant to confirm before production.
  - The paper invoice's KG column used 2.204 lb/kg and its total weight (1,522 lb) didn't match its own lines (1,537 lb); the new template uses the exact 0.45359237 and sums the lines.
  - Boxes print the product's *default* case size; a product with several box sizes can't tell which one was sold unless weights are entered.

## 2026-11-09 — Online Orders: 3 cards per row; box (with size) or per lb for every product
- **Owner request:** product grid three across (full width, like the staff product grid); customers choose **Box** or **Per lb** for every product, and a **box size** for boxes. Until the real products are imported with their case sizes, products without a recorded case weight offer standard sizes 10/20/25/40/50 lb (D-32); a product's own BOX case sizes (packing profiles with a weight) replace them automatically.
- **Customer page:** compact cards (72 px photo, name, SKU · category, price only when shown; owner asked for smaller cards), 3 columns ≥ 1120 px, 2 below, 1 on phones; each card has a Box / Per lb switch (count badge when in the order), a box-size picker, Add → stepper. The cart moved from a side column to a "Your order" button in the toolbar plus a floating "View order" button opening a drawer (right side on desktop, bottom sheet on phones); box lines can pick or change their size in the drawer. A box line without a size is caught before sending (fixes the "Choose a box size" wall seen while the server change was live before the page was).
- **Server:** LB offered for every product (it is always a valid stock/sales unit); public config lists `box_sizes` (+ `standard_sizes`) on the BOX unit; items accept `box_size_lb`, validated against that list (required for boxes, refused for other units). The size is kept with the submitted values (also after privacy erasure; no migration), shown to staff ("2 boxes · 40 lb"), and put on the draft order line (note "Box size requested: 40 lb", source line text "…, 40 lb boxes"). The sales order line has no case-size field, so one product can still be on an order once per unit (one box size per product per request).
- **Tests executed:** `tests/test_order_connect.py` **61 passed** (new `test_box_sizes_and_per_lb_orders`; config lists units/sizes; conversion note; erasure keeps items); ruff + mypy clean; web tsc + eslint clean, vitest 165; Playwright `e2e/order-connect.spec.ts` **6 passed** (size required before adding a box, box + per lb lines of one product, sizes in the drawer and checkout summary, staff see "2 boxes · N lb" and "1 lb"); stand-in website page at 1440 and 390 px: no sideways scroll, no page errors. Full API suite on a clean worktree of HEAD (757ddc5) + this change, DB `tami_test_oc3`: **474 passed, 1 failed** (`test_supplier_ledger.py::test_ledger_billed_paid_returned`, the pre-existing evening/UTC date bug noted in the previous entry). Contract regenerated (no API shape change).

## 2026-11-10 — Scans: picking sheets and the office paper order sheet come up as product lines
- **Owner report:** "when I scan for picking sheets or sales order, the products do not come up — OCR is not working". Their upload `image11-09-2026-200108-1.pdf` (SCN-20261110-0001, and `-2.pdf` SCN-20261110-0003) is the office's own pre-printed sheet scanned sideways (one 200 dpi JPEG on a Letter page), uploaded as **Picking sheet**.
- **Causes found:** (1) picking sheets were never queued for OCR (`ocr.auto_queue` read only `SALES_ORDER` + supplier types); (2) re-uploading the same file returns the earlier scan (`duplicate`), with its old type and no way to read it from the result card; (3) the paper form is not PT-OS-1 (no SKUs, three side-by-side tables ITEM | Q | PKG | WEIGHT | NOTES), so even as a sales order nothing mapped (`NO_TEMPLATE`); (4) Tesseract on the raw scan glued table rulings into words (`|IveLlowYAM_——*<|`) and lost the handwriting; (5) "Pages —" because pages came only from an OCR run.
- **Changes:**
  - `ocr.auto_queue`: `PICK_SHEET` is read like `SALES_ORDER` (`ORDER_TYPES`); "Other" still waits for a request. Still review-only: a draft order after a person checks every line; no stock, reservation or invoice.
  - New `paper_sheet.py` (template `TT-PAPER-1`): detects the sheet by its section bars + PKG headers; printed name (catalog name or sheet alias, spacing ignored, small OCR slips allowed but sent for review) + mark in that table's Q box = line; PKG/WEIGHT/NOTES ignored; unmatched name with a Q mark = unknown-product line. Synthetic sideways test scans via `paper_sheet.synthetic_scan` (tests and `scripts/e2e_fixture.py --paper-sheet` only).
  - New `ocr_grid.py` + `ocr._read_page` (Tesseract): rulings painted out before reading; orientation chosen by confidently read level words (OSD said 90° for a page needing 270° at confidence 1.1, and Tesseract also reads sideways text with the layout lost); marked narrow boxes re-read digits-only; such reads capped at 0.800 and, below 0.5, kept only as a hint (`10?`) with no quantity.
  - Page count: `scanning.page_count` (pdfinfo / TIFF frames) on single-scan answers (`page_count` on upload, duplicate and `GET /scans/jobs/{id}`; lists leave it null). Contract regenerated (only `page_count` added).
  - Web: `AUTO_READ` includes picking sheets; the result card loads the scan's reading for any type (so a duplicate shows its review or **Open reading**), offers **Read this scan** when it was never read and **Read again** when reading failed, and shows pages from the document; the multi-file queue does the same for duplicates.
- **Real office scans (dev DB, read-only, current catalog of 108):** both now detected as `TT-PAPER-1` in ~6 s. SCN-…-0003: 22 product lines with SKUs; SCN-…-0001: 8 + 1 unknown ("ZAK SAL." handwriting). Every row with a Q mark was found except light blue ticks; signature/scribbles over the HERBS boxes add ~6 false lines to remove. Handwritten numbers: Tesseract read almost none correctly (e.g. 20 → "10", 1 → "4"), so quantities are left for the reviewer with hints. **Reading handwriting needs Document AI** (`OCR_PROVIDER=document_ai`, unverified until the office pilot).
- **Tests executed:**
  - `DATABASE_URL=postgresql+psycopg://tami@127.0.0.1:5432/tami_test_ocr python -m pytest tests/test_ocr.py tests/test_ocr_tesseract.py tests/test_scans.py tests/test_supplier_ocr.py tests/test_airway_packet.py tests/test_security_ocr.py tests/test_security_receiving.py` before the change: **53 passed**; after: same 53 passed.
  - New `tests/test_paper_sheet.py` (6): Q-column mapping by table from engine-neutral tokens (PKG tick ignored, unreadable mark, OCR slip in a printed name → review, joined words, unknown product, customer by handwritten name); not detected without the tables; picking sheet auto-read + `page_count` + "Other" on request + lists without page counts; same file again → earlier scan, which can be read; **sideways image-only sheet through Tesseract as PICK_SHEET and as SALES_ORDER → exactly 7 lines with SKUs and quantities (OKR 12, TUR 5 via "Tumeric", WIR 4 via "Wierri Peppers", YYM 3, CAL 6 via "Calalloo", SPM 2, GBJ 10 via "Jam Green Banana"), all LOW_CONFIDENCE, corrected and accepted into a DRAFT order with those lines; invoices/stock movements/reservations unchanged.**
  - Same suites + `test_paper_sheet test_sku_rule test_contract test_documents test_security test_security_routes`: **131 passed**. Full API suite on the working tree (`python -m pytest -x --deselect tests/test_supplier_ledger.py::test_ledger_billed_paid_returned`, the known after-19:00 date failure), DB `tami_test_ocr`: **480 passed**. Orientation check of the synthetic sheet turned 0/90/180/270 and the two real scans: all read the right way up.
  - `ruff check` / `ruff format --check` on changed Python files clean; `mypy` on changed modules clean. Web: `tsc --noEmit` clean, eslint clean on changed files, `vitest run` **166 passed**.
  - Playwright (shared dev servers; Celery worker restarted to load the new OCR code): `e2e/paper-sheet.spec.ts` (2, new: sideways picking sheet → 3 lines OKR 12 / YYM 3 / SPM 2, Pages 1, axe clean, same file again as Sales order → "Already on file" with its lines; "Other" → **Read this scan** → lines), plus `ocr.spec.ts`, `scan-uploads.spec.ts`, `scans.spec.ts`: **7 passed**. Screenshot `apps/web/screenshots/paper-sheet-review-1440.png`.
- **Not done / open:**
  - The owner's two existing scans were not re-read for them: open SCN-20261110-0001 / -0003 (or upload the file again) and press **Read this scan**.
  - Handwritten quantities and the customer name are not read with Tesseract; staff type them (or enable Document AI). Accuracy on real handwriting is not measured (the 50-sample acceptance is still pending).
  - Scribbles/signatures crossing Q boxes produce lines to remove; light pen ticks may be missed.
  - Accepting a picking-sheet reading creates a new DRAFT sales order like a sales-order scan; if the order already exists, reject the reading instead.

## 2026-11-10 — Boxes with no box price: per lb × box weight
- **Problem:** online order SO-20261110-0005 (12 box lines) couldn't be confirmed: "no active price for AEG per BOX". The active price book (PB-2026-03, imported 22:41) has per-lb prices only, and the online form offers boxes for every product (D-23/D-32).
- **Owner decision (asked in the session):** a box with no box price is priced per lb × the box weight. Asked with the evidence that this is higher than the paper case prices (invoice 086494: Green Banana $29.00 per 40 lb box vs 40 × $1.49 = $59.60); the owner chose it anyway.
- **Built:** `orders.price_lines` (confirm and direct invoices): when a BOX line has no BOX price, resolve the LB price (customer > group > standard, as of confirmation) and multiply by the box weight: the size the customer picked on the online order (`box_size_lb` in the submission), else the product's default active BOX case size. The line snapshot keeps `basis: PER_LB_X_BOX`, `lb_rate` and `box_lb`. An explicit box price always wins. Still NO_PRICE when there is no per-lb price, or no box weight (message says which). Invoices print the box size the price came from (`CASE-40`, LBS, KG).
- **Checked on the dev order (rolled back, not confirmed):** all 12 lines price, e.g. AEG 2 × (4.49 × 40) = 179.60 a box, ALO 2.99 × 10 = 29.90.
- **Tests executed:** new `tests/test_box_pricing.py` (3): per-lb × default case size with the invoice row (`2 CASE-30 … 60.00 27.22 64.50 129.00`) and a box price winning on the same order; NO_PRICE without a weight or without any price; the online form's size beats the case size. `test_orders`, `test_invoicing`, `test_direct_invoice`, `test_invoice_template`, `test_pricing`, `test_box_pricing`, `test_security_orders`, `test_catalog`: **92 passed**. ruff and mypy clean.

## 2026-11-11 — Scan Center: animated scanner driven by live scan events
- **Owner request:** an interactive, premium isometric scanner (metallic blue-grey, cyan beam, paper feed) for scans and OCR, in the app's own style, where every state comes from real backend events (no timers, no invented percentages): Retrieving PDF → Uploading → Extracting text → Matching products → Review ready; multipage PDFs with real page counts and page results; missing documents, duplicates, failed OCR, timeouts and retry; review with the original next to editable fields and uncertain names/quantities highlighted; approvals unchanged; reduced motion. Reference shot (Dribbble "Docify") used for layout/motion ideas only; no assets or branding copied. Animation is SVG + CSS (the project's existing approach); no new dependency.
- **API (migration `0022_scan_events`):** `scan_events` (OCR_STARTED, PAGE_READ, MATCHING, MATCHED, OCR_RETRY, OCR_FAILED, OCR_TIMEOUT), written by the worker in its own short transactions (`scan_progress.py`; lock timeout 0.5 s, dropped and logged instead of slowing the reading) so pages show while the reading's transaction is open; no FK on `extraction_id` (the worker holds that row lock); ON DELETE CASCADE with the scan. Hooks in `ocr.run_extraction` and the Tesseract page loop. `GET /scans/jobs/{id}/progress` and `GET /scans/jobs/{id}/progress/stream` (SSE, Redis wake-ups + 1 s look, `end` at rest; per-employee stream cap). Stall check `OCR_STALL_SECONDS` (180): a QUEUED reading no worker holds and with no event for that long → FAILED/OCR_TIMEOUT (+ audit); a late worker finds it closed. First words read per page and warnings only for `scan.review`.
- **Web:** `components/scanner/` (`IsoScanner` SVG model: hopper → glass → output tray, beam + light curtain + recognised lines only while a page is read, pages move when a PAGE_READ arrives, chips from MATCHED counts, status badge/LED/display per outcome, pointer tilt; `ScanProcessing`: steps rail, page strip with per-page results, live region, one next action; `useScanProgress`: SSE with polling fallback). Used in the Scan Studio (replaces the old "Step n of m / nn%" counter), the result card, the multi-file list (**Watch**), `/scan/:id`, and a review page whose reading is running. Review page: every line to check outlined on the original (amber unclear quantity, red unknown product/unreadable; click → line), uncertain product or quantity marked in the row, `?page=n` from the page strip. Studio picture is the same scanner at rest.
- **Gmail:** no Gmail/IMAP retrieval exists in the code. Retrieving PDF is the real bridge step (REQUESTED/SCANNING); for uploads it is done at once. The owner was asked how scans reach Gmail before anything is built; nothing calls Google.
- **Tests executed:**
  - `cd apps/api && DATABASE_URL=postgresql+psycopg://tami@127.0.0.1:5432/tami_test_scanui PYTHONPATH=src uv run pytest -q`: **499 tests, 498 passed, 1 failed** — the known `test_supplier_ledger.py::test_ledger_billed_paid_returned` after-19:00 date bug (see earlier entries). New `tests/test_scan_progress.py` **15 passed**: 1-page upload → steps/pages/events/counts; 10-page PDF → PAGE_READ 1..10 in order and per-page lines/to-check; page events visible while the worker holds the reading open; engine outage → retry (attempt 1 of 2, no page "being read" between attempts) → FAILED → read again → ready; broken PDF fails in Tesseract (UNREADABLE); stalled reading → OCR_TIMEOUT only when no worker holds it, idempotent, late worker no-op, audit row; recent progress isn't a stall; duplicate upload → same scan, no second reading; "Other" → UNREAD; bridge REQUESTED/SCANNING/NO_PAGES/SCAN_TIMEOUT; first words for reviewers only; access 403/404/401; SSE `progress` then `end`; CASCADE with the scan; event writes give up after the lock timeout. ruff + mypy clean on new modules. Contract regenerated (additions only: the two progress paths and their schemas).
  - `cd apps/web && npx vitest run`: **178 passed** (new `lib/scanProgress.test.ts`, 12: view states from snapshots, real upload percentage only, retry wording, failure/timeout/missing/duplicate wording, live-region sentence, page labels, scanner markup per scene, unique gradient ids, steps rail `aria-current`, page strip pressed state). eslint clean on changed files. `npx tsc -b --noEmit`: clean for this work; the one error is in the OCR session's uncommitted `e2e/paper-sheet.spec.ts(57,64)`.
  - Playwright on the dev servers (real Celery worker, Tesseract; worker restarted for the OCR hooks; migration 0022 applied to `tami`): new `e2e/scan-processing.spec.ts` **10 passed** (1-page and 10-page flows with the scanner's scenes in server order and page n of 10 increasing; review page with the original, 2 outlined items, marked quantity/product, editable line, draft order still gated; reduced motion = no animation, motion allowed = beam animates only while reading; engine outage → Read again → ready; broken PDF; stalled → Timed out → Read again → ready; duplicate; empty feeder; uploading; waiting/scanning/matching/retrying; 390 px without sideways scroll; axe WCAG 2.2 AA clean on every state). Existing `scans`, `scan-uploads`, `ocr`, `paper-sheet` specs: **7 passed**.
  - Screenshots of every state (1440×900, reduced motion) in the shared notes folder `local_0c7a915d…/scanner-ui/screenshots/`.
- **Not done / open:** Gmail scan retrieval (waiting for the owner's answer on how scans reach Gmail); Document AI still unverified; a bridge scan reports scanning and uploading as one step (the bridge does not report them apart); this change builds on the OCR session's uncommitted page count / Tesseract page reader, so it is committed after that work.

## 2026-11-11 — "Missing or invalid CSRF token" on Scan Center upload
- **Owner report (screenshot):** uploading on Scan Center showed "Missing or invalid CSRF token".
- **Cause:** the CSRF token is kept per tab (sessionStorage) while the session cookie is shared by all tabs. Every sign-in starts a new session and cookie; the API log showed the 403 on `POST /scans/uploads` and, right after, two `POST /auth/dev-login` at once (dev auto sign-in from two tabs, or React StrictMode running it twice). A tab could then hold another session's token, or none in a new tab, and nothing recovered: the upload XHR just showed the error.
- **Fix:**
  - API `POST /auth/csrf` (`routes/auth.py`): a fresh CSRF token for the signed-in session; 401 without a live session; same origin only (Origin must be `FRONTEND_ORIGIN`, plus main.py's origin check, SameSite=Strict cookie, CORS). The old token stops working. `deps.live_session` split out of `get_principal` (same checks).
  - Web `api.ts`: a mutation refused with `CSRF_FAILED` renews the token once (one renewal at a time) and is sent again with the same idempotency key (it was refused before anything ran), for JSON requests and the upload XHR; if renewal fails it signs out as before. Simultaneous dev auto sign-ins share one request.
- **Tests:** new `tests/test_csrf_renew.py` (2: stale tab after signing in again gets a fresh token, the replaced token stops working; no session 401, no/foreign Origin 403, signed-out session can't be revived). `pytest tests/test_csrf_renew.py tests/test_auth.py tests/test_dev_login.py tests/test_security.py tests/test_security_routes.py tests/test_mfa.py tests/test_user_management.py tests/test_contract.py tests/test_scans.py`: **135 passed**. ruff/mypy clean; contract regenerated (`/auth/csrf`, `CsrfOut`). Web: `tsc -b` clean, eslint clean, vitest **178 passed**; new `e2e/csrf-recovery.spec.ts` (signed in again from a second tab, then upload from the first, and again with no token at all; API log shows 403 → `/auth/csrf` 200 → upload 201), with `scans`, `ocr`, `paper-sheet`, `scan-uploads`, `smoke`: **12 passed**. Full API suite on the working tree (other sessions' uncommitted scanner-UI work included; `--deselect tests/test_supplier_ledger.py::test_ledger_billed_paid_returned`, the known after-19:00 date failure), DB `tami_test_ocr`: **500 passed**.

## 2026-11-11 — Fast entry grid: Create invoice › Direct and New sales order
- **Owner request:** Focus V7 entry speed (15–30 products typed fast) with the new minimal look; mockup "Create invoice / Direct invoice" with an always-open S/N · Item · Qty · Batch No · SO Link · LB · Rate · Gross grid and a sticky Subtotal / Tax / Net total bar.
- **Web:** shared `ui/LineGrid.tsx` (20 rows always open, 5 spare rows appear as you fill them, up to the document's limit: 100 invoice / 200 order lines). Enter goes Item → Qty → next line (straight to Qty on last-invoice rows), Tab walks every cell, arrows move around, Enter on an empty row jumps to the page's main button. Item cell autocompletes by name, SKU or alias (`lib/lineGrid.ts`, one `GET /product-lookup` load), fills the default unit. A block pasted from Excel/Sheets spreads over the grid from the cell it lands on (Item, Qty, Unit, As written; header row skipped; "4 box" cells; unmatched names stay red to fix). Batch No is a dropdown (Oldest first by default, or one batch with its free quantity and expiry). Both pages rebuilt on it: `pages/NewInvoice.tsx` Direct (header: customer, date today, salesperson default, PO, warehouse, tax code / terms / currency read-only from the server or customer record, narration; customer-record strip and last-invoice prefill kept from the invoice-template work; "Save draft" keeps a draft on this computer) and `pages/Orders.tsx` New sales order (customer, wanted-for, warehouse, note, original request; As written per line).
- **Money stays server-side:** live per-row Rate / Gross / LB / batches / stock come from the server (`POST /sales-orders/preview`, provisional); "Check prices and stock" runs the existing `POST /invoices/direct/preview` (authoritative, green Net total) and any edit afterwards needs a new check before Issue. Pricing is the app's own (`orders.quote`, shared with confirm: box price, else per lb × box weight); nothing is inferred from column labels. No client money arithmetic.
- **API (no migration):** `orders.price_lines` split into `orders.quote` (same behaviour); new `line_preview.py` + `POST /sales-orders/preview` (sales_order.manage, invoice.prepare or invoice.issue in the warehouse; read-only, rolled back; per-line problems instead of a 422: NO_PRICE, INVALID_UNIT, UNIT_NOT_DIVISIBLE, DUPLICATE_LINE, INVALID_LOT, LOT_NOT_SALEABLE); `GET /product-lookup` (catalog.read; active products with case sizes and aliases, batched queries); direct invoice lines take an optional `lot_id` (the batch handed over, checked like a pick-sheet lot; stored on the picking line, so stock leaves that batch only, else FEFO); stock checks list the batches issuing takes (`lots`). Fields left read-only: tax code (D-03 not assessed; no codes invented), currency (CAD), SO Link (a direct invoice opens its own order when issued). Customers have no default warehouse/currency fields; the warehouse is chosen from the user's permitted ones.
- **Tests executed:**
  - `cd apps/api && PYTHONPATH=src uv run pytest -q`: **505 tests, 504 passed, 1 failed** — the known after-19:00 `test_supplier_ledger.py::test_ledger_billed_paid_returned` date failure (supplier code untouched). New `tests/test_line_grid.py` (4: lookup with case sizes/aliases; preview prices/weighs/lists batches FEFO, per-line problems, writes nothing, permissions; box without box price = per lb × box; direct invoice takes the chosen batch only, refuses another product's batch). ruff clean; mypy only the 2 existing `ocr.py` errors. Contract regenerated.
  - `cd apps/web && npx tsc -b --noEmit && npx vitest run`: clean, **192 passed** (new `lib/lineGrid.test.ts`, 14: rows, Enter/Tab/arrow moves, matching, Excel paste incl. quoted cells, header rows, "4 box", Focus-style full-row copies, checks, prefill keeps the cursor's row).
  - Playwright on the dev servers: new `e2e/invoice-grid.spec.ts` (keyboard-only: 3 typed lines + 3 pasted with Cmd/Ctrl+V, Check, hand-over, Issue → CAD 252.80, axe clean; batch choice and re-check after an edit), updated `new-invoice.spec.ts`, `orders.spec.ts`, plus `invoices`, `smoke`, `navigation`: **16 passed**.
  - Dev app: a 20-line direct invoice (7 last-invoice rows + 1 typed + 12 pasted) checked and issued as **TT2026-000011, CAD 4,973.95**; the check refused a later attempt when Papaya had only 116 lb left. Synthetic dev stock added for it: 500 lb each in lots `GRID-DEMO-<SKU>` (HSP ALO WCO IOK GNC CAS EDD CAL JPL PCR CHO BFR), 30-day expiry. Screenshots in `side-session-notes/local_9edf3e59…/entry-grid/`.
- **Open / for the owner:** tax code shows "Not assessed" until D-03 (the mockup's "E" is not invented); invoice date is the issue date (not editable); the customer select is native (type-ahead works in browsers, not in headless macOS Chromium); a customer default warehouse/currency would need new customer fields.

## 2026-11-11 — Gemini as an OCR engine for handwritten scans (`OCR_PROVIDER=gemini`)

- **Asked for:** read the office's handwritten order sheets (and supplier / airway papers) with Google Gemini, keep Tesseract selectable and as the fallback, and end in the existing editable drafts. Checkpoint tag `pre-gemini-ocr` on `277567e`.
- **Google (owner's AI Studio, second signed-in account):** new project **Tamisha Trading OCR**, ID `gen-lang-client-0606743746`, Free tier. "Fur and Feathers" and "Gemini Project" untouched. Model code from Google's model page: **`gemini-3.8-flash`** (stable; text/image/PDF in, structured outputs supported). Free limits read on the Rate Limit page: **5 requests/minute, 250,000 tokens/minute, 20 requests/day** (read on the same account's free-tier "Gemini Project"; the new project's page answers "Permission denied" until its first key exists). Pricing page, free tier: "Used to improve our products: Yes". API per Google's docs: `POST /v1beta/interactions`, `Api-Revision: 2026-05-20`. **No key was created, read or stored by the build; no billing, no terms accepted.**
- **Built (API):** `gemini_client.py` (one HTTPS call; key only in a request header; `store:false`; retries with backoff; per-minute limit, daily cap and circuit breaker counted in the new `ocr_engine_calls` table under an advisory lock; Google's own per-day 429 ends the day), `gemini_rules.py` (answer schema + Pydantic validation, literal quantity parsing, the safeguards), `gemini_ocr.py` (deskew, upright, table crops, stages A/B/C, cross-check against the local ink look, mapping to review lines, fallback), `routes/ocr_engine.py` (`GET /ocr/engine`, `GET /ocr/extractions/{id}/engine`, `company.manage`), `scripts/ocr_eval.py` (side-by-side evaluation outside the app). Migration **0023** (additive: `ocr_engine_calls`, `ocr_extractions.engine`, `ocr_fields.detail`; applied to the dev DB so the reloading API kept working). `ocr.py`: page rendering and layout split out for reuse (`page_images`, `page_layout`), `Proposal.detail`, Gemini branch in `run_extraction`. Settings: `GEMINI_API_KEY`, `GEMINI_MODEL`, `GEMINI_DATA_CONSENT` (default false: nothing is sent until the owner agrees), `GEMINI_REQUESTS_PER_MINUTE`, `GEMINI_DAILY_REQUEST_CAP`, `GEMINI_RECHECK_RESERVE`, `GEMINI_MAX_RETRIES`, `GEMINI_TIMEOUT_SECONDS`, `GEMINI_BREAKER_*`, `OCR_FALLBACK_PROVIDER`, `OCR_TARGET_SECONDS`. Details in `docs/modules/07-ocr.md`.
- **Built (web):** review screens reused. Each line now also shows what was seen besides the quantity (package, weight, notes, crossed-out number, NO, tick, why it needs a look); new warnings in plain words; "Read by …" line; Settings → **OCR test** (admin) with engine, key set or not, today's allowance, accuracy per engine, latest requests and one reading in full (scan, validated JSON, draft lines, Tesseract lines beside them).
- **Design decisions:** on the free plan stage A rides with stage B, one request per page; stage C (second look at doubted rows) runs only while more than 5 requests of the day are left. A line is `OK` only when the local look also found writing in that row's box: the model's own certainty never suffices. Supplier / airway papers keep the local reader's header as the base; Gemini fills blanks and supplies lines; both agreeing on product and quantity makes a line `OK`.
- **Tests (stubbed Google answers on synthetic sheets; they prove the server's logic, not Gemini's reading):** new `tests/test_gemini_ocr.py` — quantities `1`, `1/2`, `1.5`, `5`, `15` kept apart; schema validation rejects extra keys / wrong types; untouched printed rows and TOTAL rows are not lines, written totals only compared; NO / tick / crossed-out / supplied / unavailable stay per row and unknown stays null; model certainty alone never gives `OK`; shift and missed rows flagged, never moved or dropped; second look keeps both readings; Tamisha never the customer; order refs by digits; deskew; daily cap, per-minute limit, Google's daily 429, slow-down retry, breaker; bad JSON re-asked then fallback; 403 never retried and the key never appears in errors or records; scan → Gemini reading → review → **DRAFT** order → reopened and edited, no invoice / reservation / stock movement; same file again = same scan, Google asked once; fallback to Tesseract for no consent, no key, cap 0, HTTP 500, timeout, bad JSON, daily quota (reading stays reviewable, warning says why); two stores in one PDF not merged; customer prefilled only when exact and sure; supplier invoice → open receiving draft, no stock movement.
  - `cd apps/api && DATABASE_URL=postgresql+psycopg://tami@127.0.0.1:5432/tami_test_gemini PYTHONPATH=src uv run pytest tests/test_gemini_ocr.py -q`: **23 passed, 1 skipped** (the skipped one is the only real Google call, `GEMINI_LIVE_TEST=1`, not run: no key yet).
  - Full API suite, same DB (`uv run pytest -q`, working tree incl. other sessions' uncommitted CSRF work): **529 tests, 528 passed, 1 skipped, 0 failed** (run at 13:20, so the known after-19:00 supplier-ledger date failure did not occur).
  - OCR-related suites (`test_ocr`, `test_ocr_tesseract`, `test_paper_sheet`, `test_supplier_ocr`, `test_airway_packet`, `test_scan_progress`, `test_scans`, `test_security_ocr`, `test_receiving`, `test_contract`) with the above: all passed. ruff clean; mypy only the 2 existing `ocr.py` errors. Contract regenerated (additions only: the two engine paths, `OcrEngine*` schemas, `detail` and `engine` properties).
  - `cd apps/web && npx tsc -b && npx vitest run src/lib/ocr.test.ts`: clean, **9 passed** (3 new); eslint clean on the touched files. Admin page opened on the dev servers (engine status, a Tesseract reading with its scan preview).
- **Not done / needs the owner:** create the API key in the new project and put `GEMINI_API_KEY=` in `apps/api/.env`; agree (or not) to send scans to Google on the free plan (`GEMINI_DATA_CONSENT=true`); set `OCR_PROVIDER=gemini`; the Celery worker must be restarted to load the new code. **No real scan has been sent to Google and no real Gemini result is claimed anywhere above.** The reference scans (`~/Tamisha Trading/Scans`: 29 PDFs, 90 pages) need 90+ requests, i.e. five days of the free allowance or a paid plan; evaluation results will be logged here once run. One PDF holding several stores still becomes one reading (flagged `MIXED_ORDERS`); splitting it into one draft per store is not built.

## 2026-11-11 — Gemini OCR on the owner's real order sheets (first real results)

- **Scope set by the owner:** real scans, order sheets only (no packing lists, no airway bills), sent to Google's free plan with the owner's agreement. Of the 19 single pages in `~/Tamisha Trading/Scans/ilovepdf_extracted-pages (2)`, 15 are order sheets (1, 2, 4, 6–10, 12–15, 17–19); 3, 5, 11, 16 are packing lists and were not sent. Scans and readings stay out of the repo (results in the sessions' notes folder).
- **What went wrong first, and why:** (1) my own mistake: I appended a comment to `apps/api/.env` while the key line had no final newline, so the key was sent with the comment glued on (HTTP 401); separated again without reading the key. (2) Google's newer **Interactions** endpoint answered `503 "currently experiencing high demand"` for every model on this free-plan key, while **generateContent** answered with the same key and model seconds apart. The client now uses generateContent (`GEMINI_API=generate_content`, default). (3) `gemini-3.8-flash` itself returned 503 or timed out on both endpoints all afternoon (9 of its 20 daily requests spent, no answer); `gemini-3.6-flash` was used up (22 / 20 on Google's page, mostly direct test calls from another session). Failed requests count against Google's daily allowance.
- **Model used, by the owner's decision (relayed by the session that started this one):** `gemini-3.1-flash-lite`. Limits read on the project's own Rate Limit page: **15 requests/minute, 250,000 tokens/minute, 500 requests/day**. Dev `.env`: 12/min, 500/day, second look on.
- **End to end in the dev app (same upload endpoint as the Scan Center, worker, review screen):** all 15 sheets uploaded or re-read; readings are in Scan Center → To review (SCN-20261111-0008 … -0022).
  - Run 1 (before the fixes below): 14 of 15 read by Gemini, 1 fell back to Tesseract; 19–152 s per sheet, median 59 s; 48 requests.
  - Run 2 (after the fixes): **15 of 15 read by Gemini; 8–43 s per sheet, median 28 s** (first request median 12 s; the second look at unclear rows adds the rest); 26 requests, 246,155 tokens.
- **Accuracy, 5 sheets checked against the scan by eye by the build session (103 written quantities; not staff-verified):** run 1: 94 right (91%), 6 wrong (3 flagged, 3 shown as clear), 3 missed. Run 2: 90 right (87%) plus 3 reported in the package box where the customer wrote them, 6 wrong (5 flagged, **1 shown as clear**, a digit that is doubtful on the scan itself), 4 missed. **Tesseract: 0 of 103**; over all 15 sheets it found 238 rows with writing and proposed a number for 8. The model's answers vary between runs (flash-lite is Google's lightest tier). Customer names were read as text on 13 of 15 sheets but none is prefilled: none matches a customer in the dev database exactly, by design.
- **Misses by kind, and what was changed for each:** an out-of-range highlight box made the validator discard a whole page (sheet 12 → Tesseract): the box is now dropped; a quantity put on the row above and shown as clear (Guava's 6 on Starfruit; Long Chilli's 1 read as Thai Chilli 4): flagged now when the next row has writing nobody read (`NEIGHBOUR_UNREAD`); struck-out quantity with the new one in the next box read as the old one (sheet 1: 5 struck, 6 beside): a second bare number in the package box sends the row to review (`PKG_NUMBER`); "1 x 24" read as a crossing-out and an `SI#` invoice number taken for the order number: prompt; tick-like 2s read as ticks: prompt; margin numbers produced up to three total warnings per sheet: one warning, compared with the sum, each table's sum and the line count; a separate stage-A request added 2–30 s and mostly noise: off unless `GEMINI_SEPARATE_STAGE_A=true`; two scans of the same order (SO#080773, sheets 13 and 14): `SAME_ORDER_NO` warning.
- **Known and left:** 2–8 empty extra rows per sheet where the local ink finder saw writing Gemini did not report (kept on purpose: 2 of 8 on one sheet were real misses); crossed-out quantities are still the weakest case; an order number cut off at the page edge was guessed (sheet 6) and is not flagged; names are not matched to customers until staff choose them; the other 10 sheets are not checked against their scans.
- **Tests:** `tests/test_gemini_ocr.py` **25 passed, 1 skipped** (stubbed answers; the file can no longer reach Google by accident). ruff clean, mypy only the 2 existing `ocr.py` errors; web `vitest src/lib/ocr.test.ts` 9 passed, `tsc -b` clean. The real calls above are the integration evidence; no real result is claimed from stubbed answers.
- **For the owner:** the API key was pasted into another session's chat, so replace it in AI Studio and in `apps/api/.env` (on its own line), then restart API and worker. `gemini-3.8-flash` has not produced one answer yet; retry it when Google's load allows (set `GEMINI_MODEL`, 5/min, 20/day).

## 2026-11-11 — Boxes sold per lb by weight; the draft order is checked on the Create-invoice form
- **Owner report (screenshots):** confirming SO-20261111-0008 (from a scanned sheet) failed: "Line 1: CEG has a price per lb but no box price, and the box weight is unknown. Add a case size or a box price…". Owner: "there is no box price, all products go as per pound"; the confirmation screen should look like Create invoice, with less detail and no reason to cancel.
- **Cause:** a box with no box price needed a case size (or an online box size) for its weight; the WEIGHT written on the sheet (35 lb, read by the scan) never reached the order.
- **Changes:**
  - Migration `0025_order_line_weight` (after the Gmail session's `0024`): `sales_order_lines.weight_lb` (whole line, boxes only, > 0). Rollback `alembic downgrade 0024`. Applied to the dev DB.
  - Pricing (`orders.quote`): a weighed box with a per-lb price = per-lb × weight ÷ boxes (beats a box price); otherwise as before (box price, else per lb × online size or case size); no weight and no case size → 422 `WEIGHT_REQUIRED` "Line N: X is sold per lb. Enter the weight (LB) for this line." Weight on a non-box line → `WEIGHT_NOT_ALLOWED`.
  - API: `weight_lb` on order lines (create, draft edit, read, frozen source snapshot), direct invoice lines and the line preview (which also returns `lb_rate`). Scan acceptance carries the sheet's WEIGHT box (`ocr_fields.detail.weight`, plain pounds only) onto the draft line.
  - Web: the DRAFT order page is now the New sales order form (shared `OrderForm`): Customer, Wanted for, Warehouse, payment terms, Note; grid S/N · Item · Qty · Unit · LB (editable weight, case-size weight greyed when empty) · Rate (per lb when weighed) · Gross; Subtotal/Tax/Net; Save draft and Confirm order (no confirm dialog). Cancel draft asks no reason. Create invoice gets the same editable LB. Picking/backorder columns, original request and history stay on confirmed orders.
- **Tests:**
  - New `tests/test_order_weights.py` (5): weighed boxes priced per lb × weight (also over a box price), snapshot keeps the box weight; no weight → WEIGHT_REQUIRED, weighing the draft then confirms; weight only on boxes; preview prices a weighed box and asks for a weight; sheet weight parsing; scanned WEIGHT reaches the draft line. `test_box_pricing.py` updated (unknown weight → WEIGHT_REQUIRED).
  - Full API suite on the working tree (Gmail session's uncommitted work included), DB `tami_test_ocr`, known date test deselected: **565 passed, 1 skipped, 1 failed**: `test_invoicing.py::test_email_queued_sent_once_and_resend` (email still QUEUED after two outbox passes in the full run; passes alone twice; not touched here, the working tree also holds the Gmail session's outbox/worker changes). After the Gmail commit (4ecacb5), on HEAD + this change: order/pricing/preview/direct-invoice/OCR/paper-sheet/contract/invoicing suites **56 passed** (the email test included).
  - Web: `tsc -b` clean, eslint clean, vitest **207 passed** (new weight tests in `lineGrid.test.ts`). Playwright (dev servers): new `e2e/order-weights.spec.ts` (PLG 2 boxes: LB greyed 100.00 from the case, rate 0.95/lb, CAD 95.00; typed 90 lb → CAD 85.50; confirmed at 42.75 a box; draft cancelled with no reason field; axe clean) and updated `orders.spec.ts` (confirm on the form), with `new-invoice`, `invoice-grid`, `invoices`, `paper-sheet`, `smoke`: passed. `ocr.spec.ts` fails on the dev servers because the worker now reads with Gemini (`OCR_PROVIDER=gemini`): the write-in line comes back "Unreadable" instead of "Unknown product" — engine-dependent, not this change. `paper-sheet.spec.ts` made engine-neutral for the same reason.
- **Open:** SO-20261111-0008 was created before this change: open it, type 35 in LB on the Chinese Eggplant line (and the weights of the other boxes, which have no case size), then Confirm order.

## 2026-11-11 — Gmail Inbox, Pick from Gmail, Create sales order screen (owner's master prompt)

- **Asked for:** the owner's "Live Gmail Integration, Document Selection, AI OCR & Sales Orders UI Optimization" brief (15 sections). The Create Sales Invoice screenshot it mentions was not supplied; the live `/invoices/new` screen was used as the visual reference.
- **Not verified, said plainly:** no Google Cloud project, OAuth client or Pub/Sub topic exists on this machine. **No real mailbox was connected, no real email was synced, no real push was received.** Everything below about Google is proven only against a stand-in (`FakeGoogle` in `tests/test_gmail.py`) built from Google's published guides (push, sync, scopes, Pub/Sub push authentication, read 2026-11-11). The app shows "Gmail is not set up yet" until the owner does the steps in `docs/modules/15-gmail-inbox.md` › Setup.
- **Built (API, commit `4ecacb5`):** migration `0024_gmail_inbox` (4 tables, 7 `gmail.*` permissions; up / down / up run on a test DB; applied to the dev DB); `pro_trading/gmail/` (OAuth code + PKCE with `gmail.readonly` only, Fernet-encrypted tokens, History-API sync with a forward-only cursor moved in the same transaction as the rows, resumable import, expired-cursor resync, backoff, daily watch renewal, push verification, attachment checks, processing history); `routes/gmail.py` (13 routes); worker handler `gmail.sync` and beat task `gmail_poll`; notifications `gmail.message_received` / `gmail.sync_failed` / `gmail.disconnected`. The `GMAIL_*` settings in `config.py` went into HEAD with another session's commit `80dd23c` (same working tree).
- **Built (web):** Gmail Inbox page `/gmail` (sidebar entry added in `nav.ts` only, after Scan Center), OAuth return page `/gmail/oauth/callback`, connection settings dialog, shared inbox browser (filters, search, list, email with cleaned message in a sandboxed frame, attachments), page-by-page preview, "Use for OCR" dialog with Previously processed; Create invoice › **Pick from Gmail** as a third way (the two existing ways untouched).
- **Create sales order screen:** on top of the other session's order form (`6feb413`): title "Create sales order", Save draft / Confirm order also in the page header, product thumbnails in the grid (`ui/ProductThumb.tsx`, about 40 px, object-fit contain, neutral placeholder), "Add 10 more rows", and the Item column keeps a readable width on phones (the grid scrolls inside its frame; before, the product name was squeezed to nothing at 390 px on both entry screens). `LineGrid` props `thumbs` / `addRows` are opt-in: Create invoice › Direct is unchanged apart from the phone-width fix. Order rules, prices, tax and totals are untouched.
- **Reused, not rebuilt:** scan jobs, `source_documents`, the OCR engines and review screen, `mutate()`, `authorize()`, `notify_safely`, the outbox, the SSE feed, the entry grid. Accepting a reading still creates a DRAFT sales order only.
- **Tests executed:**
  - `cd apps/api && DATABASE_URL=…/tami_test_gmail_wt PYTHONPATH=src uv run --no-sync pytest -o addopts="" -q` in a clean worktree of HEAD + this work: **568 passed, 1 skipped, 0 failed** (run at 16:33, before the known after-19:00 supplier-ledger date failure). `tests/test_gmail.py`: **33 passed**. `ruff check .`: 1 finding, in `tests/test_box_pricing.py` (line too long; not from this work). `mypy`: the 2 existing `ocr.py` errors only. Contract regenerated in the commit (`tests/test_contract.py` passes).
  - `cd apps/web && npx tsc -b --noEmit`: clean (also in the clean worktree). `npx vitest run`: **207 passed** (new `lib/gmail.test.ts`, 11). eslint clean on every touched file (file by file).
  - Playwright on the dev servers: new `e2e/gmail.spec.ts` **9 passed**, new `e2e/order-screen.spec.ts` **2 passed**; `orders`, `order-weights`, `invoice-grid`, `new-invoice`, `navigation`, `smoke`, `invoices` pass.
  - Whole Playwright suite once (96 tests, 5 workers, shared dev servers): **81 passed, 6 failed, 9 not run** (the rest of the failed files, which run serially). None of the 6 is in the new specs: `documents:37` and `new-invoice:70` pass when run alone (load); `ocr:30`, `paper-sheet:73`, `scan-processing:131` depend on the live OCR engine and worker timing; `order-connect:201` fails every time since `6feb413` (a converted draft now opens as the form, which no longer shows "sent from the website, converted") and was reported to that session.
  - Screenshots (1440 and 390 px; Gmail Inbox, email, 10-page preview, Pick from Gmail, Use for OCR, Previously processed, settings, Create sales order before / after, the Create invoice reference) in the shared notes folder `local_3807a548…/gmail-and-orders-screenshots/` with a README saying which data is synthetic and which one status answer was mocked.
- **Dev environment:** dev DB at `0025`; the Celery worker was restarted at 16:05 by the order-weights session with this code in place (`pro_trading.gmail_poll` runs every minute and does nothing while no mailbox is connected). Synthetic companies "Gmail UI Synthetic …" were added to the dev DB by the fixture, like the other e2e fixtures.
- **Open / for the owner:** D-33 (who may see the mailbox), D-34 ("draft invoice" = DRAFT sales order; the accept step still merges same product + unit, which the brief says not to do for different weights or batches), D-35 (how far back, retention, downloads), D-36 (Workspace or @gmail.com account: decides Google verification and the 7-day sign-in expiry in Testing). Not done: row-level memoization of the entry grid (200 rows re-render per keystroke; no slowness measured, not virtualized), virus scanning of attachments (no scanner in the project), email-body search, document splitting, an instant live update for ordinary mail (it appears on the 20-second refresh).

## 2026-11-11 — Gmail Inbox connected to the real mailbox (development machine)

- **Asked for (owner):** set the Gmail Inbox up with `tamishatrading@gmail.com`.
- **Google side (in the owner's Chrome, as that account):** the owner turned on 2-step verification (Google Cloud refuses accounts without it), ticked Google's User Data Policy, pasted the client secret into `apps/api/.env` and clicked Allow on the consent page. Done for them: project **Tamisha Trading ERP** (`tamisha-trading-erp`; the account's existing "SMTP Project" untouched), Gmail API enabled, consent screen "Pro Trading" (External, Testing, test user `tamishatrading@gmail.com`, scope `gmail.readonly` only), OAuth web client "Pro Trading (local)" with redirect `http://localhost:5173/gmail/oauth/callback`. No Pub/Sub topic: `localhost` cannot receive a push.
- **Server:** `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_TOKEN_KEY` (generated here) and `GMAIL_ALLOWED_ADDRESS` in `apps/api/.env` (git-ignored); API and Celery worker restarted at 16:59 and the worker again at 17:04 for the fix below.
- **Verified on real mail (17:01–17:06):** connection `CONNECTED` with scope `gmail.readonly`; older mail imported in steps of 50 per poll (261 emails at 17:05 and still going); an email with a PDF sent at 17:01:02 was stored by the next poll through the history feed (cursor 48651 → 48741); inbox, filters, cleaned message view and the page preview of a real emailed PDF ("Page 1 of 1") work; the owner ran **Use for OCR** on a real emailed sheet: scan `SCN-20261111-0024`, Gemini, 1 page, 31 lines, `NEEDS_REVIEW`, existing "ready to review" notification raised. In the company since connecting: 0 sales orders, 0 invoices, 0 stock movements.
- **Bug found by the real run and fixed (`44d1bee`):** Gmail puts a `Content-ID` on ordinary attachments, so every real PDF was marked "inside the message" (listed last, and no new-document notification for the 17:01 email). Now only a picture with an inline disposition counts; the 6 rows already stored were corrected in the dev DB. Test added from the real header shapes.
- **Tests:** `tests/test_gmail.py` now blanks the `GMAIL_*` connection settings for every test, so the real `.env` cannot change results or let a test reach Google (`ecf811b`). Full API suite with the real `.env` present: **569 passed, 1 skipped, 0 failed**.
- **Not verified / open:** Pub/Sub push and watch renewal; token refresh after the first hour; behaviour under Google rate limits and an expired history cursor on the real service. The Google app is in "Testing": the sign-in ends after 7 days and the mailbox must be connected again (D-36). The worker log shows every beat task firing twice a few seconds apart (also before this work); harmless here because the sync is idempotent, but worth a look.

## 2026-11-11 — Gemini OCR: all 15 real order sheets checked against their scans

- **Asked by the owner:** check the other 10 order sheets too. Every sheet was read by eye from the scan by the build session and compared with the second app run (`gemini-3.1-flash-lite`, readings SCN-20261111-0008 … -0022). By-eye references, not staff-verified; 10 quantities the session could not read with confidence itself are left out. References and both runs are in the sessions' notes folder (`gemini-ocr/eval/`), not in the repo.
- **Result over 247 written quantities (14 sheets with writing; sheet 2 is a blank form and gave no lines, correctly):**
  - Gemini: **225 right (91%)**: 159 shown as clear, 66 right but flagged for a look. 6 more were reported in the package box, where the customer wrote them. **10 wrong: 8 flagged, 2 shown as clear** (both on sheet 14: a 2 read as 5 with the 2 moved to the package box, and "½" read as 2). 6 missed, each kept as an empty row to fill in.
  - Extra lines: 34 empty rows from the local ink finder (to remove), 7 with a quantity that is not on the sheet (6 flagged, 1 shown as clear: a 6 that straddles two rows was given to both).
  - Tesseract: **3 right of 247**.
  - NO marks: 2 on these sheets, 1 detected (Pimento Pepper, sheet 10), 1 missed (written beside the item name, sheet 19; the margin total there did trigger `TOTAL_DIFFERS`). Customer text read on 13 of 14 sheets: 5 exactly as the session read them, 6 a letter or a trailing number off ("Green Fresh" for "Green Fresh 3"), 2 the session could not confirm; none prefilled (no exact match in the dev database). Order number right on 12 of 14 (one cut off at the page edge and guessed, one a digit off).
  - Seconds per sheet: 8–43, median 28.
- **After this check:** the "2 moved to the package box" case is now sent to review by `PKG_NUMBER` (commit 2b31c5e); the prompt says NO may sit beside or over the item name. "½ read as 2" and a mark straddling two rows have no rule yet: they are the known ways a wrong number can still look clear (3 of 247 here).

## 2026-11-11 — Trash: the Super Admin deletes (almost) everything; what depends on it goes with it

- **Asked by the owner:** "super admin must be able to delete everything no matter what, almost everything" (example: Scan SCN-20261111-0020 refused because 2 OCR reads use it). The cascade/protected table was shown to the owner first and **approved as proposed** (prices in active price books go with the record; uninvoiced confirmed/picking/cancelled orders can be deleted; one-click Deactivate/Suspend when refused).
- **Built (API):** `trash.py` follows every foreign key that points at a removed row (`EDGES`): rows that only exist for it go into the same Trash item and are restored as one unit (scan → OCR reads, lines, corrections, Gmail imports, scan events, filed copies, the scanned file; customer → uninvoiced orders with lines, picking sheets and order changes, website entries, filed documents, prices; product → aliases, case sizes, prices, its lines on draft orders; supplier → filed documents; group → its prices); loose links are cleared and put back on restore unless something newer took their place (customers of a group; OCR reads matched to a customer/supplier/product/order; website items; duplicates; drafts based on a price book; order changes from an aborted picking sheet). A key not listed refuses (409 `IN_USE`, with `instead` and `hint`). Still refused, with what to do instead: anything used by invoices (+ lines, PDFs, emails), payments, allocations, supplier payments, the stock ledger, receiving, shipments or the audit log; open scans / open picking sheets (cancel / abort first); invoice PDFs; the default case size; yourself and the last Super Admin. New routes `GET /trash/preview/{kind}/{id}` (changes nothing) and `POST /trash/instead` (deactivate product/customer/supplier, suspend user; Super Admin with two-step; idempotent; audited as `<kind>.update` with `instead_of_delete`). Audit `trash.moved` now also records `unlinked` counts. Restore skips always-generated identity columns (scan events).
- **Migration `0026_trash_cascade`:** guard triggers let, only while `pro_trading.trash = on`: DELETE of OCR reads/lines/corrections, order lines and order changes, prices in published books, sales orders in any status, converted website entries; INSERT of a price back into a published book (restore); UPDATE that changes nothing but the named link column (OCR read's order, OCR line's product, order change's picking sheet, website item's product, draft's base price book). Invoice, payment, stock, shipment and audit triggers untouched. Upgrade / downgrade / upgrade run on the dev DB (now at `0026`).
- **Built (web):** the Delete dialog asks for the preview first and lists "Also moves to the Trash with it" and "Stays, but no longer linked to it" with counts and examples ("2 OCR reads (SCN-…)"), then "Restoring it brings all of this back."; when protected records keep it, it says so, lists them, and offers **Deactivate …** / **Suspend user** in one click. Screenshot: `apps/web/screenshots/trash-cascade-scan-1440.png`.
- **Correction to what the owner was shown:** for a product on a confirmed order the table said "cancel that order or amend the line first"; a cancelled or amended order line still names the product, so the message says "Delete those orders first" (the Super Admin can delete any uninvoiced order now), or deactivate the product.
- **Tests executed:**
  - `cd apps/api && DATABASE_URL=…/tami_test_trash PYTHONPATH=src uv run pytest -q` on a clean export of HEAD + this work only: **576 passed, 1 skipped, 0 failed** (17:38, before the known after-19:00 supplier-ledger failure). New `tests/test_trash_cascade.py` (7): the owner's example (scan with 2 OCR reads, OCR lines and a correction: preview names them, delete takes them, audit row with snapshot and counts, restore brings back the same reads, lines and correction); customer with a draft order, a confirmed order and its open picking sheet (cascade, its OCR read unlinked and relinked, restore puts the order back in PICKING); group (customers unlinked and relinked; a customer moved to another group meanwhile is left alone); product (refused while on a confirmed order, naming it; then takes its draft-order line and 3 prices and gives them back); protected (invoiced customer, product with stock and invoice lines, invoiced order: 409 with `instead`; one-click deactivate is audited, idempotent, Super Admin only; nothing moved); user with history suspended instead, not yourself; DB guards still refuse outside Trash mode and never let a read value change. `test_trash.py`, `test_trash_order_connect.py` and `test_gmail.py` updated where they asserted the old refusals (confirmed order, OCR-read scan, converted website entry, Gmail-made scan). Contract regenerated in the commit.
  - The same suite in the shared working tree (with other sessions' uncommitted work): the contract test (their schema changes) and 4 `test_gemini_ocr.py` fallback cases fail there; both pass on HEAD + this work, so they come from that uncommitted work, not from this change.
  - `ruff check` / `ruff format --check` / `mypy` on every touched file: clean. Repo-wide, unchanged from before: 1 ruff finding in `tests/test_box_pricing.py`, 2 mypy errors in `ocr.py`, 3 files not formatted (`ocr.py`, `test_inventory.py`, `test_order_weights.py`) — none touched here.
  - `cd apps/web && npx eslint src && npx tsc -b --noEmit && npx vitest run`: clean, **209 passed** (`lib/trash.test.ts` +2). `tsc -b` also clean on the clean export.
  - Playwright on the dev servers (dev DB migrated to `0026`): `trash.spec.ts` **5 passed**, including the new "Super Admin deletes a scan that OCR reads use, and restores it whole" (dialog lists "2 OCR reads (SCN-…)" and "1 scanned file", axe clean, reads gone after the delete and back after the restore) and the customer dialog listing its sales order and the 2 OCR reads that lose their link; with `smoke`, `navigation`, `scans`, `customers`, `products`, `order-connect`: **25 passed**.
- **Open:** OCR engine-call logs (`ocr_engine_calls`, not a foreign key) stay when a scan is trashed. A delete that races a new reference answers 409 "try again". A record restored after its group/customer was itself trashed comes back without that link (restore the other one first).

## 2026-11-11 — Scan review in the Create invoice style; Confirm selected; corrections need no reason
- **Owner request (screenshots of Review scan SCN-20261111-0024):** make the scan review page match Create invoice, add a button that confirms whatever is selected, and "no need to explain" when correcting a line.
- **Web (`pages/OcrReview.tsx`):** the recognized lines are now one compact grid like the entry grid (S/N · Item with "Sheet says" and the reader's notes · Qty + unit · Status with how sure · Confirm / Correct-or-Enter / Remove), customer and note on top like the invoice header, and a sticky bar (`ui/gridParts.ActionBar`, shared with the invoice's totals bar) with **Confirm selected (N)**, Lines kept / Still to check, the "I checked every line" tick and Create draft order. Only clear lines still waiting can be ticked (header box selects them all). Read again / Reject sit next to "Add a line the reading missed". The editor opens as a row under its line; it no longer asks why for a correction. A product that can't be loaded no longer stops the editor opening.
- **API:** `POST /ocr/extractions/{id}/fields/confirm` (`OcrConfirmLinesIn`: version + field ids): the same rule as confirming one line (clear readings only), all or none, one version step, a CONFIRM in each line's history; an unclear one refuses the batch naming its line. CORRECT no longer needs a reason (removing a clearly read line still does). Doc `modules/07-ocr.md` updated. Contract regenerated.
- **Tests executed:** `pytest tests/test_ocr.py tests/test_contract.py tests/test_paper_sheet.py` passed (new `test_confirm_ticked_lines_together_and_correct_without_reason`; the accept test now checks the remove-reason rule). Web `tsc -b` clean, eslint clean, vitest **209 passed**. Playwright: new `e2e/ocr-review-grid.spec.ts` (the reading served by route mocks, so it doesn't depend on the engine: tick all clear → Confirm selected (3) sends exactly those ids; Enter on the unclear line saves with no reason; axe clean), plus `invoice-grid`, `new-invoice`, `paper-sheet`: **7 passed**.
- **Not passing, not from this change:** `e2e/ocr.spec.ts` on this machine: the real reader (Gemini / built-in) now reads the synthetic sheet's CFZ line as Unclear and flags two adjacent rows, so the clear-line steps can't run and axe reports `target-size` on the scan viewer's flag outlines (viewer unchanged). Its write-in assertion was loosened to "Unknown product or Unreadable" like the plantain one.

## 2026-11-11 — Packaged items: every product has a packaged twin; PKG on the sheet orders it
- **Owner request:** "add more products, all products must have package items, pkg scans must use package and q column for regular loose items"; then (relayed answer) "separate product with package as categories and displaying all the way in the bottom".
- **Catalog:** each product gets its own packaged product: SKU = its three letters + `P` (CAL → CALP), name "<name> - Packaged", same stock unit and expiry tracking, no case size, no price and no stock until set (prices are not invented). Filed in the **Packaged** category, listed last: categories put Packaged at the end and `/products` / `/product-lookup` sort by SKU length then SKU (the cursor follows the same order), so packaged items come after every loose one. New products get their twin when created (in the same transaction, noted in the audit entry); `scripts/packaged_products.py` fills in an existing catalog (idempotent, audited). Migration `0027_packaged_products` (after 0026): SKU check `^[A-Z]{3}P?$`, column 4 wide; people still type three-letter SKUs; the downgrade keeps the data (old check NOT VALID). `packaged.py` holds the rule. Web product lists load up to 1000 products (the catalog doubled).
- **Scans:** paper sheet (built-in reader): a number in the row's PKG box is its own line for the packaged twin, a mark in Q the loose item; both → two lines; a tick or unit word in PKG stays a packing note. Gemini: the same (a bare number in `pkg` → packaged line judged like a quantity; the loose line no longer carries it as a "Package" note or a PKG_NUMBER doubt; Q empty → only the packaged line; the second look skips packaged lines, it reads Q). Printed names never match a packaged item. Without a twin the old behaviour stays.
- **Dev data:** migration applied to `tami`; `scripts/packaged_products.py` added 108 packaged items to Demo Trading Co (216 active products).
- **Tests executed:** new `tests/test_packaged.py` (4: twin on create, packaged listed last incl. paging across the boundary and lookup, three-letter SKUs for people, backfill idempotent; paper-sheet PKG → OKRP/IOKP lines; Gemini PKG → packaged line, PKG-only row, unit word in PKG). Full API suite (`pytest -q`): **594 passed, 1 skipped**. Web `tsc` clean, vitest **212 passed**; Playwright `catalog` (selectors now exact: CEE vs its twin CEEP), `products`, `pricing`, `invoice-grid`, `orders`, `paper-sheet`, `scans`, `ocr-review-grid` passed. `scan-processing` "1-page PDF" fails at its scanner-animation order check (a scene steps back) before reaching review; the dev Celery worker still runs code from before this change (started 17:11), so this isn't from it.
- **Dev note:** the Celery worker must be restarted to read scans with the PKG rule (the API reloads by itself).
- **Open for the owner:** packaged items have no prices or case sizes yet (set them in Pricing / Case sizes); the script refuses to run in production like `load_catalog.py`, so production needs it run deliberately; renaming or deactivating a product doesn't change its twin.

## 2026-11-11 — Packing sheet: renamed from "Picking sheet", printed as the office PACKING LIST, every page, 2 copies
- **Owner request:** "change Picking sheet to: packing sheet … like this [the paper PACKING LIST 080778] in preview and while printing … I only see 1 page, it should print all the available pages … 2 prints by default". Before: Print called `window.print()` on the app page, so Chrome printed the app shell and only what was on screen (1 page).
- **Rename (labels only, no migration):** "Packing sheet" / sidebar "Packing" / "To pack" / "Verify packing" / "Packed" / "Packed by" in the page, orders, Create invoice ("From a packing sheet", "Direct invoice (no packing sheet)"), invoice pages, Overview, notifications ("Packing sheet X ready to check", source module "Packing"), activity ("checked a packing sheet"), permission texts, trash labels, Scan Center / Gmail document type `PICK_SHEET` → "Packing sheet". Code, tables, API paths, permission codes and statuses keep the picking names. Web URLs `/packing`, `/packing/:id`, `/packing/:id/invoice`; `/picking/*` redirects (stored notification links keep working; the activity link parser accepts both).
- **Print = server PDF**, not print CSS: Chrome adds its own date/URL header and footer and its own page breaks to HTML prints; a PDF prints the same pages on every PC. `GET /api/v1/pick-attempts/{id}/sheet.pdf?copies=2` (`packing_pdf.py`, template `packing-list-1`; `sales_order.read` in the sheet's warehouse; rendered on request, never stored; deterministic). Layout from the paper form: "TAMISHA TRADING INC." / "PACKING LIST", Sales Order Number + Date box, QR = pick code (new dependency-free `qr.py`, byte mode level M; matrix identical to Project Nayuki's reference encoder — the one inside qrcode.react — on all 8 masks; the printed QR decodes to `SO-20261111-0009-P1` with Chrome's BarcodeDetector), CUSTOMER NAME / EMPLOYEE NAME (the packer once recorded) / MISSING GOODS, CHK | QTY | UNITS | PRODUCT | Batch Number | REMARKS, Total row. Later pages repeat a compact header and the column titles; "Page n of m" per copy. 30 rows on page 1 (29 lines + Total), 38 on later pages. The file holds 2 identical copies by default (`copies` 1–4), each from a new page; the print dialog's Copies stays at 1.
- **Data:** `PickOut.order_date` (order confirmed date, company time zone), `PickLineOut.units` (CASE-{lb} by the invoice rule: the line's box weight from the price snapshot, else the default case size; PIECE-{lb}; LB/KG) and `.remarks` (order line note, then the packer's). `invoice_pdf._assemble` takes an optional `producer` (invoice bytes unchanged).
- **Web `/packing/:id`:** "Record packing" (entry, unchanged) | "Paper sheet" (`?view=paper`, same layout in HTML, scrolls inside its frame on phones). **Print (2 copies)** fetches the PDF and prints it from a hidden frame (Chrome); if the frame can't print, the PDF opens in a tab. Ctrl/Cmd+P on the page prints only the paper sheet, twice (print CSS fallback).
- **Scan-back:** OCR never read the app's printed sheet (PICK_SHEET scans are read as the office order sheet, PT-OS-1 / TT-PAPER-1); the QR still opens the sheet from the Packing queue's code box. OCR/Gemini code untouched; both OCR sessions told.
- **Tests executed:**
  - `cd apps/api && DATABASE_URL=postgresql+psycopg://tami@127.0.0.1:5432/tami_test_packsheet PYTHONPATH=src uv run pytest -q` (shared working tree, other sessions' uncommitted work included): **594 passed, 1 skipped, 0 failed**. New `tests/test_packing_sheet.py` (13): API columns/copies/headers/determinism/`copies` range, warehouse scope (403/401), pagination 1/29/30/45/67/68/120 lines (every line printed, header + column titles per page, Page n of m per copy), units rule, long text cut, QR matrix = reference. Contract regenerated (HEAD + this change only). `ruff check`, `ruff format --check`, `mypy` on touched files: clean.
  - `cd apps/web && npx tsc -b --noEmit && npx vitest run`: clean, **212 passed** (`lib/packing.test.ts` new, `staff.test.ts` label). eslint clean on touched files.
  - Playwright on the dev servers: new `e2e/packing-sheet.spec.ts` **6 passed** (old `/picking` links redirect, sidebar "Packing"; paper view matches the form, 45 rows, axe clean; Print fetches `sheet.pdf?copies=2` = 4 pages for 45 lines, 2 for 1 line; Ctrl/Cmd+P with print media shows only 2 copies, `page.pdf` gives 4 / 2 pages; recording packed quantities still works; 390 px no sideways scroll). Renamed-label specs: `navigation`, `orders`, `invoices`, `new-invoice`, `reports`, `documents`, `payments`, `paper-sheet`, `trash`, `activity`, `smoke`, `order-weights`: passed. `gmail.spec.ts` "returning from Google without consent" fails because the dev server now has a real Google client configured (not from this change); `gmail` "Create invoice keeps…" and `notifications` "live event" failed once under parallel load and pass alone.
- **Samples for the owner** (sessions' notes folder `packing-sheet/`): the owner's SO-20261111-0009-P1 (29 lines → 1 page per copy), a 1-line sheet and a synthetic 45-line sheet (2 pages per copy), as PDFs and PNGs, plus the on-screen paper view.
- **Open / for the owner:** UNITS follow the invoice rule, so a weighed line shows weight ÷ boxes (SO-20261111-0009 IOK "6 boxes, 10 lb" → CASE-1.6667, same as its invoice would); if the order sheet's WEIGHT means lb per box, the order-weights rule needs changing (not done here). Copies print identical (labels "Warehouse copy / Office copy" possible on request). Printing from the hidden frame was checked in headless Chromium only (the PDF request and frame); the real print dialog needs one try in the office Chrome.

## 2026-11-11 — Screen half empty after scrolling down (app frame scrolled off the window)

- **Owner report (screenshot, scan review after a scan):** after scrolling down, the top half of the window showed a cut-off page and the bottom half nothing; the sidebar was cut off too.
- **Cause (app-wide, not the scan screen):** only `<main>` is meant to scroll. Screen-reader-only labels (`sr-only`, e.g. every data table's caption) are absolutely placed; `<main>` was not their containing block, so one far down a long page lay outside `<main>`'s clipping and made the *document* taller than the window (measured: 2448 px in a 768 px window). Scrolling past the end of the page, or any `scrollIntoView` (Scan Center does one when a reading is ready), then scrolled the window and dragged the whole frame up.
- **Fix (`components/Shell.tsx`, frame only; the sidebar is untouched):** `<main>` is `relative` (contains those labels) with `overscroll-contain`; the frame root is `relative overflow-clip`, so nothing inside the app can stretch or scroll the window again.
- **Tests:** new `e2e/shell-scroll.spec.ts` (4): a 40-row order at 1440×900, 1024×600 and 390×844, and six list screens in a short window, are wheel-scrolled far past the end and pushed with `scrollIntoView` / `window.scrollTo`; the window stays at 0, the document is no taller than the window, the frame and top bar stay put, the sticky totals bar stays at the bottom. Run once against the old frame it fails ("/scan: the window scrolled"). With the fix: **4 passed**; with `navigation`, `notifications`, `smoke`, `orders`, `order-screen`, `gmail`, `trash`, `settings`, `invoice-grid`, `activity`: **54 passed** in total. `tsc -b` clean, vitest **212 passed**.
- **Also:** `e2e/gmail.spec.ts` no longer assumes the server has no Google client (this machine now has one): its settings-dialog check accepts either state and never clicks Connect.

## 2026-11-11 — Scan review: magnifier on each line; no full-page scan beside the lines

- **Owner request (screenshots):** while hovering a line, show the original scan zoomed on that product in a small floating window, so nobody scrolls to the scan; at least four columns visible (item, Q, PKG, weight, notes); a spotlight on the line; the full-page scan on the left is not needed.
- **Web:** `components/scanner/ScanLens.tsx` + `lib/scanLens.ts`. Hovering (or tabbing into) a recognized line opens a 600×200 window beside the row showing that row of the original: framed from the item name across at least one whole table of the sheet (ITEM · Q · PKG · WEIGHT · NOTES), the row centred, everything else dimmed (spotlight). Zoom Auto / 2× / 3× / 4× (never closer, so the columns stay in view), mouse wheel over the window zooms, choice remembered on the computer; Esc, leaving the row or scrolling closes it. The page on the left is gone: the lines use the full width; "Open original in a new tab" stays.
- **API:** `GET /scans/documents/{id}/pages/{n}?dpi=110|220|300` (default 110, unchanged); PDF pages are drawn sharper for the magnifier, cached per resolution; any other value 422. Contract regenerated.
- **Positions are the reader's:** Gemini's boxes are approximate (often only the name, sometimes two tables wide); the framing compensates by always showing a whole table from the item's left edge. A line without a position (typed by hand) has no magnifier.
- **Tests:** `lib/scanLens.test.ts` (6, framing with real Gemini boxes, zoom limits, centring, page edge); `e2e/scan-lens.spec.ts` (4, on a synthetic reading from `scripts/lens_fixture.py`, built-in fake reader, no outside OCR call): hover opens it beside the row, zoom buttons / wheel / remembered zoom, keyboard + Escape, resolutions; API `test_security_receiving.py` +1. vitest 218 passed, tsc clean, eslint clean on touched files; API contract, Gmail, OCR, scans, security tests 77 passed.

## 2026-11-11 — Receiving made simpler: today's date, documents from Gmail / Scan Center, automatic batch names

- **Owner request (screenshots of Receiving):** dates start as today (changeable) everywhere; pick the supplier PDF from the Gmail box or a scan instead of downloading it, and keep documents optional when stock is counted by hand; batch names made automatically from the supplier's initials plus a number; a simpler, friendlier layout.
- **Dates:** shared `todayIso()` (`lib/format.ts`, this computer's calendar day). *Now starts as today:* Receiving › New shipment "Arrives on" (was "Expected on", empty). *Already today, now the shared helper:* supplier payment "Paid on", customer payment "Date sent". *Left empty on purpose:* expiry dates (Receiving count, Inventory opening stock) — printed on the goods, today would be wrong; order "Wanted for" — empty means no delivery date was asked for; Reports from/to (preset last 30 days) and Gmail custom range — search filters; Pricing "Activate at" — empty already means now.
- **Documents (API):** new `POST /shipments/{id}/documents/gmail` {version, attachment_id, kind, doc_ref}: needs `grn.record` in the shipment's warehouse **and** `gmail.attachment.read`; the file is fetched and checked by `gmail.processing.load` (type from its bytes, size limit, must open), kept unaltered as a source document (channel GMAIL), listed in the Scan Center as a vendor document, linked back on the Gmail attachment; no OCR, no stock change; idempotent; same file twice → 409 ALREADY_ATTACHED. Scan Center attach reuses the existing `POST /shipments/{id}/documents`. Upload and Gmail share `_vendor_doc_job`.
- **Batch names (D-37, recorded with the owner's words):** count line saved with Batch empty → `<supplier initials>-<4-digit number>` ("This Fruta SAS" → TFS-0001). Number runs per company in `batch_counters` (migration **0028**, additive, with downgrade), taken by an `INSERT … ON CONFLICT DO UPDATE … RETURNING` that locks the counter row until commit, so concurrent saves never share a number; never reused (a removed line keeps its number used); a number already typed by hand is skipped. Editing a line with an empty batch keeps its name. `ShipmentDetail.next_batch_ref` is the preview shown in the empty field. Batch reaches the stock lot on approval as before.
- **Web (`pages/Receiving.tsx`):** New shipment: one row (Supplier, Arrives on, invoice no., airway bill no.), note behind "+ Add a note". Shipment page is one flow: steps strip (Documents — optional → Count → Send for approval → Manager approves); documents strip (chips open the viewer; Upload file / From Gmail / From Scan Center, "Optional — you can count by hand"); **What arrived** grid — saved rows plus one entry row (Product typed by code or name, Unit, Good, Damaged, Refused, Batch, Expiry, Note), Enter saves and returns to Product; "Send count for approval" sits on the grid; "Check against the documents" (document lines + comparison) folds away until used; costs folded by default. `GmailBrowser` gained `onSelect`/`selectLabel` for the picker. The approval flow, permissions and "stock changes only when a manager approves" are unchanged.
- **Tests executed:**
  - `cd apps/api && DATABASE_URL=postgresql+psycopg://tami@127.0.0.1:5432/tami_test_receiving PYTHONPATH=src uv run --no-sync pytest -o addopts="" -q tests/test_receiving.py tests/test_security_receiving.py tests/test_gmail.py tests/test_scans.py`: **75 passed** (new: initials, empty batch → ARE-0001… never reused / hand-typed skipped / kept on edit / reaches the lot, 4 concurrent saves → 4 different numbers, counting with no document posts, Gmail attach: permissions 403 for warehouse and sales, stored byte-for-byte, retry idempotent, second attach 409, no OCR, unknown 404). ruff clean on touched files; mypy clean on receiving (2 existing errors in `ocr.py`, not touched).
  - `cd apps/web && npx tsc -b --noEmit && npx vitest run`: clean, **221 passed** (product lookup, `todayIso`). eslint clean on touched files.
  - Playwright on the dev servers: `e2e/receiving.spec.ts` **3 passed** (Arrives on = today, no "From Gmail" for warehouse staff, upload, grid row with empty batch → ARE-0001 and focus back on Product, document line + comparison, axe at 1440 and 390, approval unchanged); `gmail`, `payments`, `suppliers`, `new-invoice` specs **15 passed**.
  - Screenshots: `apps/web/screenshots/receiving-new-1440.png`, `receiving-empty-1440.png`, `receiving-count-1440.png`, `receiving-count-390.png`, `receiving-posted-1440.png`.
- **Open:** D-37 per-company vs per-supplier numbering and yearly restart. Shipment numbers (`SHP-YYYYMMDD`) use the server's UTC day, so a shipment made in the evening can carry tomorrow's date — not changed here.

## 2026-11-12 — Receiving units: box, lb and kg everywhere; lb by default, kg when the documents are in kg

- **Owner request (screenshot of a Thyme count, 105 KG on the invoice):** "add kg, lb and box options everywhere, lb for default and kg only if the shipment scans is kg".
- **Web (Receiving count row and document-line form):** the unit list always shows box, lb and kg (piece too for products sold by the piece). Default **lb**; **kg** when a live document line of the shipment is in kg or its printed text says KG; typing "… KG" in "Description as printed" switches lb to kg. Box is greyed "(no case size)" for a product without a box size (a box can't be stocked without one). Choosing a product keeps lb/kg as chosen. `lib/receiving.ts`: `receiveUnits`, `docsInKg`, `defaultUnit`, `kgSavedAsLb`.
- **API:** the existing rule stays — a product is stocked in kg only if its stock unit is kg (no double measure). So kg entered for a product kept in lb is converted exactly on save (`units.kg_to_lb`, 1 lb = 0.45359237 kg, 4 places, as stock is kept): count lines (add and edit) store lb and add "Counted in kg: 105 kg good, 2 kg damaged" to the note; document lines store lb (a per-kg unit cost becomes per-lb), the printed text is unchanged, the audit keeps what was entered. The comparison then lines up in lb. Box for a product without a case size is still refused (INVALID_UNIT).
- **Tests:** `cd apps/api && DATABASE_URL=…/tami_test_receiving PYTHONPATH=src uv run --no-sync pytest -o addopts="" -q tests/test_receiving.py tests/test_security_receiving.py tests/test_units.py` **38 passed** (new kg→lb test; `test_validation_rules` now expects kg for a box product to be saved as lb instead of refused). Web: tsc clean, vitest **224 passed** (unit choices, kg default), Playwright `e2e/receiving.spec.ts` **3 passed** (default lb, options box/lb/kg). Checked on the owner's TSA 2614 shipment: the count row starts in kg.
- **Not changed:** order, invoice and packing screens keep their price-based units (a unit there decides the price; changing them is a pricing question, not a receiving one).

## 2026-11-12 — Kg on orders and invoices, invoiced in lb (D-38 settled)

- **Owner decision:** "Invoice in lb" — a kg line for a product kept in lb is saved and invoiced in lb.
- **API:** `orders.kg_for_lb` / `orders.lines_in_stock_unit` convert such lines exactly (`units.kg_to_lb`, 4 places) and add "Ordered in kg: N kg" to the line note. Applied in `routes/orders.insert_order` (order form, direct invoice, OCR accept and Order Connect convert all go through it) and in the draft-order update. `line_preview.preview` checks, prices, weighs and stock-checks the row as the lb line it will become (row keeps the kg typed; `lb_rate` set so the grid shows "x/lb"; amount = lb qty × lb rate, the same as the saved line). A kg line plus an lb line of the same product is refused as DUPLICATE_LINE (they are one lb line). Products stocked in kg are unchanged.
- **Web:** `lib/lineGrid.gridUnits` always offers lb and kg after the product's own units (box only with a case size), so New sales order, Create invoice › Direct and the OCR review grid show box/lb/kg.
- **Tests:** API full suite on `tami_test_kgorders`: **603 passed, 1 skipped, 1 failed** — `test_platform::test_migration_down_and_up` fails on `search_recent`, a table from the global-search session's uncommitted migration in the shared tree, not this change; new: kg order → lb line with note, preview amount = confirmed price, edit converts, kg+lb repeat refused, kg product stays kg, direct invoice in kg issued in lb with stock taken in lb. `test_validation_and_draft_editing` now checks an unsized unit (PIECE) for INVALID_UNIT instead of KG. Web: tsc clean, vitest 225 passed (`gridUnits`), Playwright order-screen / invoice-grid / new-invoice / order-weights / ocr-review-grid **9 passed**.

## 2026-11-12 — Error handling everywhere: plain words, saved or not, next step

- **Owner request:** "fix everything about the error handling everywhere properly, analyse every possible error and properly address proper outcome, actions after an error one by one."
- **Inventory:** `docs/operations/ERROR_CATALOG.md`. It covers 421 `ApiError` raise sites (199 code/status pairs, generated from the source), the shared codes, and outages and integrations (PostgreSQL, Redis, Celery/outbox, Gmail/Google OAuth, Gemini OCR, scanner bridge, PDF, email). It also covers web-side failures (offline, timeouts, chunk downloads, render crashes). Each row says what the user sees, what happens to their data and the next action.
- **API (`errors.py`, `bodylimit.py`, `main.py`, new `routes/client_errors.py`):**
  - Unexpected exceptions are classified: database down or pool exhausted → 503 `DB_UNAVAILABLE` (`Retry-After: 30`); deadlock or serialization failure → 409 `BUSY_RETRY`; constraint not pre-checked → 409 `CONFLICT`; Redis down → 503 `SERVICE_UNAVAILABLE`; anything else → 500 `INTERNAL`. No SQL, paths or exception text is sent to the client.
  - Every error is logged with its request id, method, path, code and actor. The JSON log now keeps the traceback (`exc`); before, it was dropped.
  - Framework 4xx errors and `VALIDATION` use plain words. `VALIDATION` responses also name each `field`.
  - 413 responses carry a request id (header and body).
  - New `POST /api/v1/client-errors` logs screen crashes with the reference shown on screen. Signed-in staff only, 20 per minute, fields capped, nothing stored.
- **Web:**
  - `lib/errors.ts`: `describeError` / `errorText` give what happened, whether it was saved (writes only) and the next action (retry, reload, sign in, fix, admin, wait, check), plus the support reference for 5xx. Unknown codes show the server's plain text, or safe words for the HTTP status.
  - All 15 feature `*Message` helpers fall back to it; there are no more "Something went wrong" dead ends. Pages that showed `err.message` raw (Online Orders, form builder, OCR engine, notification settings, supplier OCR) and the pricing row-error code column now use it.
  - `api.ts`: 120 s timeout; `OFFLINE` / `NETWORK` / `TIMEOUT` / `SERVER_UNREACHABLE`; the method and request id travel on every `ApiError`; uploads behave the same.
  - `ErrorState` explains the cause and offers the right button. It falls back to the request that failed in the last 15 s.
  - Error boundaries: whole app, and per screen inside the frame. A failed chunk download reloads once, then explains.
  - Offline/back-online banner.
  - Sign-in-ended dialog over the page: the page and typed input stay, and you continue in place.
  - Customer form keeps typed input after a version conflict (`keepEdits`).
  - The sidebar and theme are unchanged. No business rule changed.
- **Tests executed:**
  - `cd apps/api && DATABASE_URL=postgresql+psycopg://tami@127.0.0.1:5432/tami_test_errors PYTHONPATH=src uv run --no-sync pytest -o addopts="" -q`: **618 passed, 1 skipped, 1 failed** before regenerating the contract. The failure was `test_contract`, for the new endpoint; it was regenerated and the `client-errors` path committed. New `tests/test_errors.py`: **15 passed** (envelope; 7 exception classes → status/code with no leaks; request id in body and header; logging with request id and traceback; plain framework texts; validation field names; 413 with id; crash reports). ruff and mypy clean on touched files.
  - `cd apps/web && npx vitest run`: **287 passed**. New `lib/errors.test.ts` has 62 cases: every shared code; saved wording for writes vs loads; feature and unknown codes; reference; every helper's fallback; the fetch wrapper; `keepEdits`. The 10 helper tests that pinned the old "Something went wrong" text were updated.
  - eslint is clean on touched files. `tsc -b` is clean except `src/lib/search.ts`, which is the global-search session's uncommitted file.
  - Playwright `e2e/errors.spec.ts`: **8 passed**. It covers:
    - a 500 with a reference, then Try again
    - API down (502)
    - forbidden
    - offline banner and recovery
    - session expiry with the password asked again, typed search kept, same page
    - version conflict keeping typed input
    - a missing record
    - a screen whose code can't download
  - `csrf-recovery` passed.
  - Full Playwright run (2 workers): **88 passed, 10 failed**.
    - 7 failures are axe `aria-allowed-attr` on the new "Search or go to…" box, from the global-search session's uncommitted work: reports, scan-processing, scan-uploads, scans, settings, suppliers, trash.
    - The pricing spec checked for the raw `INVALID_UNIT` code. It now checks the column name; on rerun it passes that point and stops at the same axe issue.
    - `documents.spec` waited for the Drive copy job; on rerun that passed and it stops at the same axe issue.
    - `ocr.spec`: the dev Celery worker (started the day before, concurrency 1) did not read the upload within 30 s. The worker code is not touched here.
- **Open:** other edit forms (products, price books, receiving, orders, Online Orders) still reload and ask to redo the change after a version conflict; each needs the same small `keepEdits` change.

## 2026-11-12 — Universal search (⌘K / Ctrl K): every module, exact numbers first, server-side permissions

- **What the owner gets:**
  - The header's "Go to…" box is now "Search or go to…". ⌘K / Ctrl K, a click, or "/" (on screens without their own search) opens one search over every module. The old screen list is kept as the "Screens" group.
  - Phones get a search button in the header that opens it full screen.
  - Results are grouped by kind with a few per group, a "See all" link and highlighted matches. Category chips show only what the person may open. Wide screens get a lazy preview with key fields, related records and actions (open, print / download the invoice PDF or packing sheet).
  - An empty box shows the person's own recent records (clearable) and quick actions they may use.
  - `/search?q=…` shows all results with category chips, sorting and "Show more". It is not in the sidebar.
  - Sidebar and theme unchanged. Search never changes money or stock data.
- **Engine: PostgreSQL full-text search + `pg_trgm`, no new service.** Measured below at realistic volume: the worst p95 is 90 ms against the 150 ms target, so nothing heavier is justified.
- **Files:**
  - API:
    - `migrations/versions/0029_universal_search.py`
    - `src/pro_trading/search/` (`registry.py` permissions and text, `indexer.py`, `parser.py`, `engine.py`, `preview.py`, `models.py`)
    - `routes/search.py`, `main.py` (router), `worker.py` (task `pro_trading.search_index` every 3 s)
    - `scripts/search_reindex.py`, `scripts/search_benchmark.py`
    - `tests/test_search.py`
    - `pyproject.toml`: per-file ruff ignores for the search SQL
  - Web:
    - `components/QuickJump.tsx` (trigger and phone button)
    - `components/search/{CommandPalette,SearchPreview,Highlight}.tsx`
    - `lib/search.ts` + `lib/search.test.ts`
    - `pages/Search.tsx` and its route in `App.tsx`
    - `e2e/search.spec.ts`
    - `e2e/navigation.spec.ts`, updated for the new box
  - Shell: the phone search button line in `components/Shell.tsx` went in with ffc88c9 (error-handling session).
  - `contracts/openapi.implemented.json` re-exported.
- **Schema (migration 0029, additive, with downgrade; tested up → down to 0028 → up):**
  - `search_documents`, one row per record, keyed by (type, id):
    - `company_id`, `warehouse_id`
    - title / subtitle
    - `identifiers text[]`: upper-cased, as written and compact without spaces or dashes
    - `ident_text`, `title_norm` (accents folded)
    - `names`, `body` (linked records' text), `ocr_text`
    - generated `tsv` (weights A title / B names / C body) and `ocr_tsv`
    - `status`, `doc_date`, `amount`, `currency`
    - `customer_id`, `vendor_id`, `product_ids[]`
    - `attrs`, `url`, `source_updated_at`, `indexed_at`
    - GIN indexes on identifiers, trigram (ident_text, title_norm), both tsvectors and product_ids
  - `search_index_queue`: append-only change list; a FAILED status after 8 tries.
  - `search_recent`: per-employee recent records, newest 20 kept. No foreign keys, so the Trash is unaffected.
  - 25 AFTER triggers on the source tables. Each is one plain INSERT into the queue: no upsert, so two transactions on the same customer never wait on each other.
  - 20 `upper(column)` indexes for the live exact lookups.
  - `pg_trgm` is created if missing and left installed on downgrade.
- **Sync:**
  - Why not `outbox_events`: the triggers write to a separate queue. The search queue gets one row per changed row (about 500k on a bulk load). In the business outbox these rows would:
    - push back invoice emails and Drive copies
    - make existing tests that count pending outbox rows or dispatch with only their own handler fail
    - trigger "job failed" alerts
  - Same pattern, separate table: written in the business transaction, drained by the existing Celery worker, idempotent rebuild from the source rows, retried with backoff (10 s, 20 s, 40 s…), then FAILED.
  - The drain claims rows with SKIP LOCKED and folds repeated changes to one record into one rebuild. A deleted record (including one moved to the Trash) loses its document; a restored one is indexed again.
  - A renamed customer, supplier or product re-indexes its orders / invoices / payments, shipments / supplier payments, or batches.
  - The worker never touches ERP tables. Cost to writers: +0.03 ms per changed row (5,000-row update: 260 ms with the triggers, 111 ms without).
- **Indexed modules and fields (real tables only):**
  - **Products:** name, SKU, aliases, active unit-profile codes, category, packaged twin (SKU+P).
  - **Customers / stores:** name, code (`external_ref`), vendor #, ship-to name (the "store"), email, phone, addresses, group.
  - **Suppliers:** name, code, email.
  - **Batches (`inventory_lots`):** lot code, batch ref, product, warehouse, the supplier / shipment / GRN that received it.
  - **Stock counts:** count no.
  - **Sales orders:** order no, customer, product SKUs / names. The order's captured `source_text` is searched as scanned text.
  - **Packing sheets:** pick code, order no.
  - **Invoices:** invoice no, customer, order no, line SKUs / descriptions, total, date, state.
  - **Receiving shipments:**
    - shipment no, GRN, airway bill, supplier invoice no
    - document refs and count-line batch refs
    - supplier, declared line labels, products, landed-cost kinds (amounts not indexed)
  - **Customer payments:** payment no, customer's reference, bank reference, customer, invoice numbers.
  - **Supplier payments:** payment no, reference, supplier, shipment.
  - **Scans:**
    - job no, file name, note, customer / supplier
    - stored OCR text: text blocks, header values, line labels and raw text of the live reading
    - Gemini is never called at search time
  - **Documents archive:** file name, folder, note.
  - **Gmail:** subject, sender, Gmail's preview, attachment names. The body is not indexed: it needs `gmail.body.read`.
  - **Activity log:** not copied; looked up live in `audit_events` by exact target id or action prefix.
  - **Missing, with an adapter hook** (`registry.MISSING_MODULES`; a new type needs a builder, a `DocType` and a trigger):
    - returns and adjustments (only stock-movement types exist, no document table)
    - a separate store table (stores are customers' ship-to names)
    - Stock movements themselves are not separate results: the batch preview counts them and links the sales that used the batch.
  - Online Orders requests (OC-) are not indexed yet.
- **API:**
  - `GET /api/v1/search?q=&type=&limit=&per_group=&cursor=&sort=relevance|newest|oldest`
  - `GET /api/v1/search/preview/{type}/{id}`
  - `GET/POST/DELETE /api/v1/search/recent`
  - `GET /api/v1/search/status`
  - Rate limit: 240 searches per minute per employee via `ratelimit` (fails open if Redis is down).
  - **Query syntax** (`search/parser.py`): `invoice:` `order:` `batch:`/`lot:` `shipment:` `grn:` `awb:` `scan:` `payment:` `sku:` `customer:` `store:` `supplier:` `status:a,b` `date:2026` / `2026-11` / `2026-11-12` / `a..b` / `>x` `amount:>1000` / `500..2000` `type:invoices,orders`, plus quoted values and any combination.
    - Invalid values give 422 `BAD_QUERY` with how to write it.
    - Control characters are stripped.
    - Unknown `key:` stays text.
    - Only parameterized SQL is used; tsquery words are reduced to letters and digits.
- **Ranking (tiers never overlap):**
  - 1000 exact number / code (live in the ERP tables and in the index)
  - 900 exact name
  - 800 number starts with / 780 name starts with
  - 650 number contains
  - 600 every word in title or names (prefix)
  - 500 filters only
  - 400 words found in linked records
  - 300 activity
  - 200–299 similar spelling (trigram, names only — never for anything containing a digit, so a mistyped invoice number does not bring up its neighbours)
  - 100–199 scanned text
  - Within a tier: invoice > batch > order > shipment > customer > product > … then newest.
  - **Index behind or down:** exact numbers and exact names come from the ERP tables directly, so a record created a second ago is found before the worker indexes it. If the search table fails (3 s statement timeout, savepoint), the answer is marked `degraded` and the palette says "exact numbers and names still work". The palette notes when the queue is more than a minute behind.
- **Permissions (server side, in every SQL branch, counts included):**
  - Each type mirrors its module's read endpoint (`registry.DOC_TYPES`):
    - Invoices: `invoice.read` in the invoice's warehouse.
    - Batches and stock counts: `inventory.read` per warehouse.
    - Orders and packing sheets: `sales_order.read` per warehouse.
    - Receiving: `grn.record` / `grn.approve` per warehouse.
    - Scans: `scan.create` / `scan.review` per warehouse.
    - Documents: `document.read` per warehouse.
    - Customers: `catalog.read` or `price.read`, any scope.
    - Products: `catalog.read`, any scope.
    - Suppliers: `catalog.read` or `grn.*`, any scope.
    - Payments: `payment.record` / `payment.verify`, any scope.
    - Supplier payments: company-wide `supplier.ledger.read`.
    - Gmail: company-wide `gmail.read`.
    - Activity: company-wide `audit.read` (D-39).
  - Previews and related records apply the same rule per related record. Anything not visible, or in another company, is a 404, so nothing is revealed.
  - Amounts are shown and the `amount:` filter applies only on types whose money permission the person holds. Warehouse staff get no invoice or payment links and no prices.
  - Someone with no readable type gets 403 on every search endpoint (`test_security_routes`).
- **Benchmark** (`scripts/search_benchmark.py`, synthetic data in `tami_bench_search`, in-process engine, 40 runs per kind after warm-up, MacBook dev machine):
  - Data: 10,000 products; 5,000 customers; 1,000 suppliers; 50,000 orders / packing sheets / invoices with 100,000 invoice lines; 20,000 batches; 2,000 shipments; 50,000 scans with OCR text.
  - Index: 238,000 documents, 292 MB, full backfill in 312 s.
  - p95 in ms, owner / warehouse-scoped user:

    | Query | Owner | Warehouse |
    |---|---|---|
    | exact invoice no | 39.9 | 43.7 |
    | exact batch | 30.9 | 35.0 |
    | airway bill typed with spaces | 29.5 | 36.4 |
    | invoice-number prefix | 68.4 | 71.2 |
    | customer name, 2 words | 63.8 | 87.3 |
    | product word prefixes | 62.7 | 58.4 |
    | one common word | 90.4 | 66.7 |
    | typo | 25.7 | 27.0 |
    | OCR words | 48.6 | 26.4 |
    | `customer:` + `status:` | 29.3 | 11.9 |
    | `date:` + `amount:` | 14.9 | — |
    | `invoice:` + word | 64.6 | — |

    "—": warehouse staff may not see invoices, so the query returns nothing at once.
  - Preview p95: 5.7 ms.
  - Worst p95: **90.4 ms**; target 150 ms.
  - Reconcile OK after backfill.
- **Tests:**
  - `cd apps/api && DATABASE_URL=…/tami_test_search PYTHONPATH=src uv run --no-sync pytest -o addopts="" -q tests`: **648 passed, 1 skipped, 1 failed**.
    - The failure was `test_security_routes`: a no-role employee could call search (empty answers). Fixed (now 403).
    - After the fix, `test_security_routes`, `test_search`, `test_rbac` and `test_contract` were re-run: all passed.
  - `tests/test_search.py`, 30 tests:
    - parser keys, dates, amounts, errors, tsquery safety
    - exact numbers first (invoice, batch → lot then shipment, airway bill with spaces / without dash, supplier invoice, payment and bank refs, customer / supplier codes, SKU, order, packing sheet)
    - fuzzy never outranks or blurs exact numbers; tiers never interleave
    - prefix, contains, multi-word, alias, highlight terms, groups and counts
    - filters
    - injection strings
    - warehouse vs sales vs finance vs owner vs warehouse-scoped admin
    - money hidden
    - cross-company isolation (same invoice numbers in two companies)
    - previews follow real links
    - recents private and clearable
    - queue sync, rename fan-out, idempotency and coalescing, retry / backoff / FAILED / reconcile / re-arm, deletes, Trash and restore, backfill of missing and orphaned documents
    - exact numbers while the index is behind and when it is down
    - search writes nothing to ERP tables
  - Web:
    - `npx vitest run`: **293 passed** (new `lib/search.test.ts`: highlight, money, query strings, app paths only, latest-request-wins, permitted quick actions)
    - `tsc -b` clean; eslint clean on the touched files
  - Playwright:
    - `e2e/search.spec.ts` + `e2e/navigation.spec.ts`: **8 passed**. They cover ⌘K / Ctrl K, ↑ / ↓ / Enter / Esc, the focus trap, the right route opening, Screens, Ctrl+Enter → `/search`, category chips per role, axe on the open palette, and 390 px full screen with no sideways scroll.
    - reports / settings / suppliers specs re-run after removing `aria-expanded` from the read-only header box (axe `aria-allowed-attr`, reported by the error-handling session): **10 passed**.
- **Setup / backfill / rollback:**
  - Setup: `alembic upgrade head`, then `PYTHONPATH=src uv run python -m scripts.search_reindex backfill`. Dev DB: 28k documents in 31 s.
  - Restart the Celery worker so the `search-index` beat entry runs.
  - Maintenance commands:
    - Check: `… search_reindex reconcile` (exit 1 when out of step).
    - Process now: `… drain`.
    - Re-arm failures: `… retry-failed`.
    - One type or company: `--type`, `--company`.
  - Rollback: `alembic downgrade 0028` drops the triggers, queue, recents, documents and lookup indexes; ERP tables are untouched.
- **Limitations / open:**
  - D-39: who may search the activity log.
  - Backfill rebuilds one record at a time (~1.3 ms each). Fine for these volumes; a bulk builder would be needed for millions.
  - The dev worker runs at concurrency 1, so a long Gmail poll briefly delays indexing. Exact numbers are unaffected.
  - Online Orders requests, returns / adjustments documents and stock movements as separate results are not searchable (see above).
  - Recent records keep the newest 20 per employee.

## 2026-11-12 — Email (Lark Mail) module and the user replacement (owner's master directive)

- **What the owner gets:**
  - A new **Email** menu. It is separate from Gmail Inbox / Scan Center, which are unchanged.
  - **Settings › Email Services** (Super Admin only) to connect each @tamishatrading.com Lark mailbox with its Lark app password.
  - The seven authorized users, created and invited.
  - Details: `docs/modules/16-email-lark.md`.
- **Phase 1, audit:**
  - Six roles. The directive's SUPER_ADMIN is `OWNER_ADMIN` ("Super Admin") and its ADMIN is `MANAGER` ("Admin").
  - 76 foreign keys point at `employees`.
  - The real company `0f766da1…` had 5 accounts (Olivia Owner = dev sign-in, Mark Manager, Sam Sales, Wendy Warehouse, Fiona Finance). The other ~1,300 companies in the dev DB are synthetic test companies.
  - The directive's "Email Scan" = module 15 (`/gmail`, `gmail/`) + Scan Center. It was not modified; its Fernet / beat / sanitizer / sandboxed-iframe patterns were reused.
- **Phase 2, users:**
  - Backup: `python -m pro_trading.user_replacement backup` wrote `var/backups/tami-20261112-112240.dump` (55.8 MB). The restore into a scratch DB matched (employees, invoices, orders, stock movements and audit row counts equal); the scratch DB was dropped.
  - An extra dump was taken before migration 0030: `var/backups/tami-before-0030-20261112-112037.dump`.
  - `report`: 5 accounts and their history rows (Olivia 1,060, Sam 99, Mark 89, Wendy 75, Fiona 5); none is one of the seven people.
  - `users --by user`: created Saddam Hussain + Asha Narine (Super Admin) and Vada Narine, Ajay Narine, Denise G., Nishar Miya, Andy Narine (Admin).
    - Each has an invitation in the dev mailbox. No password was set by us.
    - Eight mailboxes were prepared (NOT_CONFIGURED): seven personal, plus info@ shared by Saddam and Asha.
    - A second run created nothing (idempotent).
  - `archive` refused, as designed: neither new Super Admin has accepted the invitation yet. The 5 old accounts stay active until then (D-44); nothing is deleted.
- **Phases 3–8, Email:**
  - Read-only IMAP adapter with a command guard: refuses SELECT / STORE / COPY / MOVE / EXPUNGE / DELETE / APPEND / CREATE / RENAME, non-PEEK fetches and non-UID FETCH.
  - Resumable non-destructive sync:
    - UIDVALIDITY re-keying, server removals marked
    - lease per mailbox, backoff
    - AUTH_FAILED stops retries
  - Encrypted app passwords. They are saved only after Lark accepts the IMAP login.
  - Per-person read / star state. Mailbox-level access checks on every endpoint (404 + audit).
  - Outgoing queue: exactly-once per Idempotency-Key; UNCERTAIN never auto-retried.
  - Drafts and uploads.
  - Search: tsvector + trigram, scoped to one mailbox. Universal search gets an "Email" group, live, limited to the person's own mailboxes.
  - ERP links (customer, supplier, order, invoice, receiving, payment), explicit and audited.
  - Super Admin endpoints: test, save password, sync now, pause / resume / reconnect, access grants, sync runs, security log.
- **Files:**
  - API:
    - `migrations/versions/0030_lark_mail.py`
    - `src/pro_trading/larkmail/` (`models`, `imap`, `smtp`, `mime`, `sanitize`, `sync`, `outgoing`, `access`, `secrets`, `sniff`, `clamd`)
    - `routes/mail.py`, `user_replacement.py`
    - `scripts/mail_ui_fixture.py`
    - Tests: `tests/lark_fakes.py` (local IMAP / SMTP stand-ins), `tests/test_lark_mail.py`, `tests/test_user_replacement.py`
    - Small hunks: `config.py`, `models.py` (`archived_at`), `permissions.py` (`mail.use` / `mail.manage`), `main.py`, `bodylimit.py`, `worker.py` (beat `mail-sync` 20 s, `mail-send` 5 s), `routes/employees.py` (archived hidden), `search/{registry,engine,parser}.py`, `routes/search.py`
    - `contracts/openapi.implemented.json`
  - Docs: `docs/modules/16-email-lark.md`, D-40…D-46.
- **Commands / results:**
  - `pytest -o addopts="" -q tests` on `tami_test_lark`: **669 passed, 1 skipped, 1 failed**. The failure was `test_contract`, the stale OpenAPI file; after `scripts/export_openapi.py` it passes.
  - Included: `test_gmail.py` (Gmail Inbox regression), `test_search.py`, `test_user_management.py`, `test_rbac.py`, `test_trash*`.
  - New: 20 in `test_lark_mail.py`, 1 in `test_user_replacement.py`.
  - `ruff check` / `ruff format` clean on new files. `mypy` clean on `larkmail`, `routes/mail.py`, `user_replacement.py`, `search`, `worker`.
- **Dev stack:**
  - `alembic upgrade head` applied 0030 to `tami`. The running API had already reloaded the new `Employee` columns, so this was needed at once; it is additive.
  - Celery worker restarted for the two new beat tasks. Both run (`mail_sync` → 0, `mail_send` → 0: no mailbox connected).
- **Only tested against stand-ins:**
  - All IMAP / SMTP behaviour (local servers in `tests/lark_fakes.py`).
  - **No Lark mailbox is connected.** No real email was read or sent.
  - The owner pasted Andy's Lark code in chat; it was not used. Entering it is the owner's step in Settings › Email Services, and since it appeared in chat it should be regenerated in Lark first.
- **Rollback:**
  - `alembic downgrade 0029` drops the mail tables (imported copies only), the permissions and the archive columns.
  - Users: reactivate in User management, or restore the backup.
- **Web (same day, second commit):**
  - Email page `/email`: three panes on desktop, one at a time on phones; account selector; folders; infinite list; sandboxed reading pane with Load images; attachments; Reply / Reply all / Forward; Print.
  - Compose: chips, rich text, uploads, signature, autosaved drafts, idempotent Send; Outbox with status. Live search and ERP links.
  - Settings › Email services (Super Admin only). Email nav entry for `mail.use` with an unread badge. ⌘K deep links `/email?account=…&message=…`.
  - Results:
    - `tsc` clean; `vitest` 299 passed; eslint clean on the touched files.
    - Playwright `email.spec.ts` (12) + `gmail.spec.ts` + `navigation.spec.ts`: 24 passed; `settings` / `smoke` / `search`: 15 passed.
  - Search now also indexes the words of the HTML version; `test_lark_mail` + `test_user_replacement` + `test_gmail`: 56 passed.
  - Open:
    - A printed body is cut at about one page.
    - A reopened draft lists its files by count only.
    - "Create draft order" only links to the customer (no prefill route).
    - The ⌘K screen list shows Email to people without access.

## 2026-11-12 — Owner UI pass: no warehouse choosers, Scan Center without the scanner bridge, auto-hide sidebar

- **Warehouse choosers removed** from Scan Box "Use for OCR", New sales order, New invoice (direct) and Order Connect "Ships from". The first warehouse the person may act in is sent automatically (D-47). No API change.
- **OCR review grid:** the confirm check now sits in the action cell with fixed slots (check · Correct · remove), 28 px controls, vertically centred, action column widened to 11rem so nothing spills into Status.
- **Scan Center:** "Scanner / device" and "Scan now" removed; heading is "New scan". Buttons: Upload files + **Import from Scan Box** (the Gmail picker in a dialog; the chosen file is read through `POST /gmail/attachments/{id}/process` and the result opens like an upload). Scanners tab and the header "Scanner: …" pill removed from the UI; scanner tables, endpoints and paired devices are untouched.
- **Notifications panel:** fixed height `min(45rem, 80dvh, 100dvh − 6.5rem)` on desktop, list scrolls inside.
- **Sidebar:** the collapse button is now "Auto-hide sidebar": the icon rail stays (88 px) and widens to the full sidebar over the page on hover/focus; "Keep sidebar open" pins it again. Remembered per browser (`pt.sidebar.collapsed`).
- **Results:** `tsc -b` clean; `vitest` 299 passed; eslint clean on touched files. Playwright: scans (rewritten for upload/import; bridge tests dropped), navigation (+ auto-hide test), notifications (+ height check), gmail, ocr-review-grid, smoke, orders, order-connect passed. scan-uploads passed run alone. `ocr.spec` and `scan-processing.spec` failed on this dev stack because its worker reads with real Gemini while those specs expect the fake engine (exact readings and step timing); this change only alters layout there.
- Screens checked in the browser at 1440 and 390 px.

## 2026-11-12 — Smoother motion everywhere

- **One easing for the whole app** (`--ease-calm: cubic-bezier(0.22, 1, 0.36, 1)`, fast start, long soft stop) and an exit curve; page fade-in 280 ms, pop-overs 240 ms, drawer 300 ms. Only opacity and transform are animated.
- **Dialogs** (every native `<dialog>`: confirmations, Scan Box picker, document preview, session expired) now fade/lift in **and fade out on close** (CSS `@starting-style` + `allow-discrete`; older browsers just close instantly).
- **Sidebar auto-hide:** revealed with `clip-path` instead of animating width (no re-layout per frame); the full layout is swapped in before opening and swapped back only after the close has finished, with a short cross-fade.
- **Phone drawer** now slides out as well as in (stays mounted, `inert` when closed). **Nav sub-menus** (Inventory, Online Orders) open and close by height instead of popping.
- Reduced-motion users still get no animation (existing global rule).
- Tests: vitest 299 passed; Playwright navigation, smoke, settings, notifications, scans, gmail, shell-scroll, errors passed. `trash.spec` "Undo" now also matches the Customers page's "Undo changes" button (from ffc88c9, not this change).
- Fixed: `trash.spec` clicks the toast's exact "Undo" button (it also matched Customers' "Undo changes"); 5 passed.

## 2026-11-12 — Supplier documents: supplier on top, add a missing supplier, no reader/model names

- **Supplier picker at the top of "Document details"** (supplier documents in scan review): matched supplier preselected; any other supplier can be chosen; "+ Add “<name on the document>” as a new supplier" opens a small form (name, code, currency, prefilled) for people with `catalog.manage` (`POST /vendors`, unchanged API). "Send to Receiving" now uses this choice and no longer has its own select. The "isn't one of yours" warning disappears once a supplier is chosen.
- **No AI reader or model names anywhere in the app:** "Read by Gemini (model) in N s" is now "Read in N s." (and no longer shown twice); reader-disagreement warnings read "A second check…" with plain field names ("2 lines were read, but a second check counted 1…"); Settings › OCR engine shows "Online handwriting reader" / "Built-in reader" and no model row.
- **Results:** `tsc` clean; vitest 304 passed (wording tests updated, +2); eslint clean; Playwright ocr-review-grid, receiving, settings: 10 passed. No e2e covers a supplier document end to end (needs a supplier-document fixture); checked by hand on a real supplier reading in the dev stack.

## 2026-11-12 — Global breadcrumbs

- **One breadcrumb trail above every screen below the first level** (Shell): Home icon › section › sub-screen or record, e.g. `⌂ › Scan Center › To review › Review scan SCN-…`, `⌂ › Products › Case sizes`, `⌂ › Settings › User management`, `⌂ › Inventory › Stock ledger` (tab screens). Built from the address (`lib/breadcrumbs.ts`); a record's name is taken from the page's own heading as it loads, with a generic fallback ("Order", "Shipment"…). Current page is plain text with `aria-current="page"`; links are muted and underline on hover.
- **Phones:** Home, "…", the parent and the current page (truncated) on one line; no sideways scroll.
- The two page-made trails (batch page, price list) were removed so there is only one. Supplier-document reviews no longer repeat their warnings above the page (they are listed once in Document details).
- **Fixed with it:** the phone drawer (smooth-close change) kept a second copy of the menu in the page, which duplicated the user name and Email badge for tests; its contents now exist only while it is open or closing.
- **Results:** `tsc` clean; vitest 309 passed (+5 breadcrumb tests); full Playwright run (minus the two Gemini-dependent OCR specs): 107 passed. Then email, order-connect, navigation, gmail: 33 passed after the drawer fix. Still failing, unrelated to these changes: `reports.spec` (stock movement table empty for the period), `documents.spec` (Drive copy), `scan-uploads` / `scan-processing` duplicate check (live worker reading with the real OCR engine).

## 2026-11-12 — Breadcrumbs move into the top bar, larger

- Wide screens (≥ 1024 px): the trail sits in the top bar, left of search, in a white pill like the search box; 17 px text, 40 px home button, current page in semibold. Phones/tablets: still at the top of the page (the top bar is full there), 15 px.
- Removed the page-made "‹ Order & quote requests" back link on an online-order request (the trail covers it).
- Results: tsc clean; vitest 309 passed; Playwright navigation, smoke, settings, shell-scroll, products, inventory, pricing, order-connect: 28 passed. `order-connect` "quote request (phone width)" fails here with or without the header trail (the live row never arrives in this environment), so it is not caused by this change.

## 2026-11-12 — Sidebar slides and pushes; header spacing

- **Auto-hide sidebar no longer covers the page:** the rail (88 px) slides open to 276 px and pushes the page to the right, and pulls it back when it closes (width transition, same easing as the rest of the app). The page is never overlaid.
- **Header:** even padding above and below (12 px phones, 16 px desktop) so the bar never touches the page; the page starts closer under it. Breadcrumb pill: more inner padding and spacing, same 48 px height and soft shadow as the search box; left edge lines up with the page cards.
- Leftover page-made trails removed (Orders list and order page, New invoice, Supplier page). The product page still has one: `Products.tsx` has another session's uncommitted edits, so it was left alone.
- Results: tsc clean; eslint clean; Playwright navigation (auto-hide test now also checks the page moves 88 → 276 px), smoke, orders, new-invoice, suppliers, order-screen, invoices, shell-scroll, inventory, pricing: 31 passed.

## 2026-11-12 — Order page: readable lines and original request

- **Order lines:** the scanned text under each product reads "On the sheet: “BREADFRUIT” · ticked" (no `| Q: 1 | checkmark`; the quantity is already in its column). "Awaiting invoice" is now "To invoice"; rates show without the repeated "CAD" (the header says it). "Cancel qty" stays on one line, with proper padding.
- **Table headers never wrap** (all DataGrid tables; the grid already scrolls sideways when too narrow).
- **Original request:** one plain sentence (who, when: "Scanned by Olivia Owner on Nov 11, 2026, 6:22 PM. Kept as received; it never changes.") and a tidy #/Product/Qty table of the lines as first received. A scan's raw machine reading (the noisy text block) is folded away under "Show the raw text read from the scan"; typed or website notes show as normal text.
- Results: tsc clean; vitest 310 passed (+1 `writtenText` test); eslint clean; Playwright orders, order-screen, navigation, smoke, invoices, customers, grid-view: 18 passed.
