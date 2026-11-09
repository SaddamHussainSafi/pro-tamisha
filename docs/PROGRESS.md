# Progress — Pro Trading ERP

Last updated: 2026-11-09. Evidence for every item is in `docs/operations/BUILD_LOG.md`.

## Modules

| # | Module | State | Migration |
|---|---|---|---|
| 00 | Foundation: auth, sessions, MFA, RBAC, audit, warehouses | Done | `0001_foundation` |
| 00b | Design system and preview screens | Done (restyled 2026-11-08) | — |
| 01 | Catalog, units and aliases | Done | `0002_catalog` |
| 02 | Pricing and versioning | Done | `0003_pricing` |
| 03 | Transactional inventory engine | Done | `0004_inventory` |
| 04 | Sales order drafts and picking | Done | `0005_sales_orders` |
| 05 | Final invoices, partial dispatch, PDF | Done (PDF layout is a placeholder until D-12) | `0006_invoicing` |
| 06 | Scanner bridge and intake | Done (fake driver only; real Toshiba not verified, D-14) | `0007_scanner` |
| 07 | OCR reading and review | Done (fake OCR engine; Document AI not verified) | `0008_ocr` |
| 08 | Vendor shipments and GRN | Done | `0009_receiving` |
| 09 | Documents, Drive archive, POD | Done (local Drive stand-in; Google Drive not verified, D-15) | `0010_documents` |
| 10 | Payments (manual ledger), receivables, reminders | Done (instructions, reminder days, verifier roles are placeholders until D-16; email dev sink) | `0011_payments` |
| 11 | Focus migration | Deferred by owner until the rest of the app is functional (also blocked on D-13, D-17) | — |
| 12 | Analytics / Reports (read-only) | Done (no profit/margin until D-04; no SSE refresh; credits not testable until a credit-note flow exists) | — (no new tables) |
| — | Customers management UI (`/customers`, `/customers/:id`) | Done (inactive has no effect on new orders yet: open question) | — |
| — | User management & authentication (2026-11-09): six roles, invitations, forgot/reset password, optional two-step sign-in with recovery codes, sessions, security events, bootstrap command | Done (email provider for links pending D-15; "two-step for everyone" built but off) | `0017_user_management` |
| 14 | Notification Center (2026-11-09): bell + panel, `/notifications` history, Settings › Notifications, live SSE with replay, per-person preferences, quiet hours, email + desktop channels | Done (low-stock needs a reorder threshold that doesn't exist yet; email goes to the dev sink until D-15; see `docs/modules/14-notification-center.md`) | `0018_notifications` |
| — | Owner menu changes 2026-11-08: Suppliers, Products › Case sizes + Prices, one warehouse, no previews, customer tabs, Settings (My account, Users), supplier ledger + payments, Create invoice (sheet or direct), Overview panels, scanner discovery | Done (supplier/direct-invoice/discovery questions in BLOCKERS; Toshiba discovery unverified, D-14) | `0012_supplier_payments`, `0013_scanner_discovery` |

The dev database `tami` is at migration `0017` (2026-11-09; `0018`–`0019` belong to the Order Connect session, still in progress).

## Owner-directed changes on 2026-11-08 (UI and dev)

- **Look:** the layout follows the reference dashboards, with the colour set to Tamisha green `#28A745` and its "apple" ramp (`design/tokens.json` schemaVersion 3). The note at the top of `design/UI_DESIGN_REFERENCE.md` records the override.
- **Overview:** a visual dashboard built from API data only: KPI cards, a striped bar chart, an orders-by-stage donut and progress bars.
- **Navigation:** a grouped, collapsible sidebar. Every entry is live (Payments with Module 10, Reports with Module 12, Customers with the Customers UI).
- **Warehouses:** the picker and all warehouse codes are removed from the UI. New records go to the first warehouse the user is allowed to act in.
- **Scan Center:** the new-scan card has start, in-progress and completed states. After a sales-order scan or upload, the **Recognized lines** editor opens on its own so OCR mistakes are fixed in place.
- **Recognized lines style:** applied to the OCR review page, the receiving preview and the real receiving comparison.
- **Dev auto sign-in:** setting `DEV_AUTO_LOGIN_EMAIL` plus the hidden route `POST /api/v1/auth/dev-login`. It returns 404 when unset, and the API refuses to start with it set in staging/production. Locally it is enabled in the git-ignored `apps/api/.env` as the demo owner.

## Last verified test results

All from 2026-11-08, on this machine, after the card grid view (API last run clean at 220 before it; the change has no API code).

### API (`apps/api`)
- `ruff check`, `ruff format --check`, `mypy src`: clean.
- `PYTHONPATH=src uv run pytest`: **220 passed**.

### Web (`apps/web`)
- `eslint`, `tsc -b`: clean.
- `vitest`: **117 passed**.
- `playwright test`: **40 passed**, including axe WCAG 2.2 AA (new `e2e/grid-view.spec.ts`).
- Known flake (not seen this run): `documents.spec.ts` "warehouse role has no access to the archive" failed once in an earlier parallel run; passed 3/3 alone.

## Not built / not verified

These are tracked in `docs/BLOCKERS.md`:
- OCR of vendor documents into declared lines;
- a working global search box (it is a placeholder);
- the real scanner, real OCR engine, Google Drive and email provider;
- the final invoice PDF template.
