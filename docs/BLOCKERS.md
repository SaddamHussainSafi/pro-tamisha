# Blockers and known issues — Pro Trading ERP

Last updated 2026-11-08. Decision numbers refer to `docs/decisions/OPEN_DECISIONS.md`.

## Needs an owner/business decision before production

| Item | Decision | Effect today |
|---|---|---|
| Tax rules by goods/jurisdiction | D-03 | `invoice_tax_verified=false`: production-like environments refuse to issue invoices until an accountant signs off. |
| Landed cost / freight & duty allocation, costing method | D-04 | Costs are recorded per shipment only; Reports show no cost, margin or profit figures. |
| Real customer invoice PDF template | D-12 | The invoice PDF uses a placeholder layout. |
| Payment instructions, reminder dates, verifying roles | D-16 | Module 10 runs on placeholders: instructions text, reminders at -3/+1/+14 days, verify = FINANCE + OWNER_ADMIN. Also undecided: should reminders pause while a reported payment awaits confirmation (today they don't), may the same person record and confirm a payment (today yes), and what to do with overpayments (kept as unapplied credit; no refund flow). |
| Google Workspace / Shared Drive for the archive | D-15 | `DRIVE_PROVIDER=local` copies files to `apps/api/var/drive`. Google Drive is not verified. |
| Focus V7 backup format, history migration scope | D-13, D-17 | Module 11 has not started. |
| OCR accuracy target and review turnaround | D-18 | Every line is reviewed by hand regardless of confidence. |
| Data retention / privacy policy | D-19 | No retention jobs exist. |

## Open questions from Module 12 (Reports)

Built with the choices below; each needs an owner/accountant answer before the figures are relied on.

- **Revenue definition.** Reports use net sales = invoice line amounts before tax, dated by invoice issue date (company timezone), and also show tax and the total with tax. Should management revenue be before or after tax, and by issue date or by payment date?
- **Voids and credits.** Invoices in VOIDED or CREDITED are left out, but no void or credit-note flow exists yet (`invoice.correct` has no handler), so "revenue = issued less credits" cannot be tested with real credits. How should a credit dated in a later month count: against its own month or the original invoice's month?
- **Origin filter.** The module asks for an origin filter, but products have no origin field; only vendors have a country. Is origin the vendor's country, or a new product attribute?
- **Weighted products.** Quantities are reported per invoiced unit (lb lines separately from box lines). Should lb-priced lines also report the number of cases?
- **Inactive customers.** The Customers screen can mark a customer inactive, but nothing refuses new orders, invoices or payments for an inactive customer yet (it only hides them from the default list). Should new orders be refused?
- **Who sees reports.** `report.read` is held by OWNER_ADMIN, MANAGER and FINANCE (catalog default). Should sales staff see their own customers' figures?

## Not verified with real hardware or services

- **Toshiba scanner + NAPS2 on the office Windows PC (D-14).** Only the fake bridge driver is tested. Manual upload is the fallback.
- **OCR accuracy on real paperwork.** Tesseract is measured only on our own typed documents scanned to images. Needs 3–5 real supplier invoices and filled order sheets (handwriting is not measured). Google Document AI remains untested.
- **Email delivery.** The provider is not configured.
- **Official Tamisha logo and assets.** `design/BRAND_VERIFICATION.md` is pending, so the UI uses a text "PT" mark.

## Not built yet

- **OCR of vendor documents into declared shipment lines:** declared lines are entered by hand.
- **Global search box in the header:** it is a placeholder with no behaviour.

## Consequences of owner-directed UI choices (2026-11-08)

- **No warehouse picker anywhere.** Orders, shipments, scans, scanners, counts and opening balances go to the first warehouse the user is allowed to act in. With the demo data that is MAIN, so the second warehouse (EAST) cannot be chosen from the UI. Settings › Warehouses still lists and creates warehouses.
- **Dev auto sign-in is enabled locally** (`apps/api/.env`, git-ignored). It is refused in staging/production by both the route and the settings validation. Remove the line to restore the login.

## Open questions from the supplier ledger (2026-11-08)

- **What we owe a supplier.** Today: billed = the value on their documents (declared quantity × declared unit cost) for posted shipments, minus payments. Should damaged or rejected boxes reduce what we owe (a supplier credit), and how is that agreed and recorded?
- **Currencies.** Balances are kept per currency (USD invoices, CAD payments are separate lines). Do we pay suppliers in their currency only, or convert, and at which rate (D-04)?
- **Who records supplier payments.** Placeholder: Owner/Admin and Finance record; Managers see the ledger. Confirm.
- **Freight, duty, brokerage.** Listed per shipment but not in any balance. Are these paid to the supplier or to other parties (forwarder, broker) who need their own ledger?

## Open questions from direct invoices (2026-11-08)

- **No warehouse check on direct invoices.** The owner asked to invoice small sales without a picking sheet, so nobody counts the goods before the invoice is issued; the issuer confirms the hand-over. Should a second person confirm, or a value limit apply?
- **Weighted products.** A direct invoice bills the unit chosen (box, piece, lb). Billing boxes by scale weight still goes through a picking sheet. Needed for counter sales?

## Known issues

- **New browser tab is signed out on its first save.** The CSRF token is kept in per-tab `sessionStorage`, so a tab opened fresh (not duplicated) sends no token; the first change it saves fails with `CSRF_FAILED` and the app returns to the login screen. Reading works. Found while testing the Customers screen; not fixed (affects every page).

## Known test flakiness

- **`apps/web/e2e/documents.spec.ts`, "warehouse role has no access to the archive":** failed once during a full parallel run. It passed 3 out of 3 when run alone. This looks like timing; it is not investigated further.
- **Fixed this session:** `scans.spec.ts` upload test raced the list's loading placeholder (two `role=status`). It now waits for the list to load.
