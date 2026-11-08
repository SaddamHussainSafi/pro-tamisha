# Blockers and known issues — Pro Trading ERP

Last updated 2026-11-08. Decision numbers refer to `docs/decisions/OPEN_DECISIONS.md`.

## Needs an owner/business decision before production

| Item | Decision | Effect today |
|---|---|---|
| Tax rules by goods/jurisdiction | D-03 | `invoice_tax_verified=false`: production-like environments refuse to issue invoices until an accountant signs off. |
| Landed cost / freight & duty allocation, costing method | D-04 | Costs are recorded per shipment only; no profit figures are shown. |
| Real customer invoice PDF template | D-12 | The invoice PDF uses a placeholder layout. |
| Payment instructions, reminder dates, verifying roles | D-16 | Module 10 config holds placeholders. |
| Google Workspace / Shared Drive for the archive | D-15 | `DRIVE_PROVIDER=local` copies files to `apps/api/var/drive`. Google Drive is not verified. |
| Focus V7 backup format, history migration scope | D-13, D-17 | Module 11 has not started. |
| OCR accuracy target and review turnaround | D-18 | Every line is reviewed by hand regardless of confidence. |
| Data retention / privacy policy | D-19 | No retention jobs exist. |

## Not verified with real hardware or services

- **Toshiba scanner + NAPS2 on the office Windows PC (D-14).** Only the fake bridge driver is tested. Manual upload is the fallback.
- **Google Document AI OCR.** Only the `fake` engine is tested (`OCR_PROVIDER=fake`).
- **Email delivery.** The provider is not configured.
- **Official Tamisha logo and assets.** `design/BRAND_VERIFICATION.md` is pending, so the UI uses a text "PT" mark.

## Not built yet

- **Module 10 Payments:** only the migration and models exist (see `docs/SESSION_HANDOFF.md`).
- **Customers management UI:** the sidebar shows "Soon". Customers come from seed and demo data.
- **Reports / Module 12 Analytics:** the sidebar shows "Soon".
- **OCR of vendor documents into declared shipment lines:** declared lines are entered by hand.
- **Global search box in the header:** it is a placeholder with no behaviour.

## Consequences of owner-directed UI choices (2026-11-08)

- **No warehouse picker anywhere.** Orders, shipments, scans, scanners, counts and opening balances go to the first warehouse the user is allowed to act in. With the demo data that is MAIN, so the second warehouse (EAST) cannot be chosen from the UI. Settings › Warehouses still lists and creates warehouses.
- **Dev auto sign-in is enabled locally** (`apps/api/.env`, git-ignored). It is refused in staging/production by both the route and the settings validation. Remove the line to restore the login.

## Known test flakiness

- **`apps/web/e2e/documents.spec.ts`, "warehouse role has no access to the archive":** failed once during a full parallel run. It passed 3 out of 3 when run alone. This looks like timing; it is not investigated further.
- **Fixed this session:** `scans.spec.ts` upload test raced the list's loading placeholder (two `role=status`). It now waits for the list to load.
