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
