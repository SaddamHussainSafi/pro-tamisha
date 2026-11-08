# Product brief / complete discussion recap

## Business
Tamisha Trading Inc., a Toronto-area import/wholesale distributor of variable-weight / variable-piece goods supplied by international vendors and sold to grocery stores. Existing legacy Focus V7.0129 desktop ERP; customers place phone orders and owner writes quantities on fixed printed product forms; warehouse physically packs skids and records missing quantities with numbers or `NO`; operator issues invoices only after warehouse confirmation. The company receives airport/vendor freight paperwork, sometimes a single comprehensive document, sometimes AWB/packing list/commercial invoice separately.

## Terminology
- **Original order / sales-order draft**: morning paper + scanned original, not an invoice. May have many subsequent dispatches.
- **Picking sheet**: printed or original paper used to prepare skid, with confirmed actual boxes/pieces/weights and `NO` where not available.
- **Final invoice**: generated only after confirmation of goods supplied; a separately numbered invoice for each later delivery. Immutable after issuance; correct by authorized adjustment and preserved originals.
- **Backorder**: difference between ordered and committed fulfillment, less explicitly cancelled quantity. `NO` alone never means customer cancellation.
- **Goods Receiving Note (GRN)**: warehouse-verified physically received inventory from vendor; freight document alone cannot post stock.

## V1 features requested
1. Employees, permissions, management dashboard; customers, vendor directories.
2. Products with one canonical product ID; aliases; SKUs; selling/stock units box/piece/lb/kg; configurable per-product default case size plus prior customer/product case suggestion; actual weight required where weight pricing is contractual.
3. Multiple lines of same product, different weights: e.g. two 20lb, two 30lb, two 40lb and one 25lb cassava cases; separate invoice rows, consolidated product analytics.
4. Customer-specific daily/weekly pricing; editable alphabetical price grid; downloadable CSV template; upload, preview differences, approve/activate, permanent versions/rollback-as-new-version; historical invoice rates immutable.
5. Customer phone order -> paper -> Toshiba e-STUDIO 409CS -> NAPS2 local service -> Google Document AI -> structured quantity draft -> manual review -> order draft. Same order sheet or QR-coded picking print -> workers record packed/`NO` -> scan or manual entry -> review -> final invoice for shipped only -> future independent invoice for remainder.
6. Finalized invoice stock deduction once with real-time SSE update; vendor verified GRN stock addition; damaged/lost/returns adjustments; available vs reserved; batch/lot tracking.
7. Vendor docs: printed scanned or native PDF; single or multi doc grouped by shipment reference/AWB, vendor, dates; extracts declared boxes/pieces/weights, origin, prices/currency where present; human validates and approves GRN; save originals.
8. Generate PDFs matching existing business print form **exactly after template supplied**; print on Toshiba via office print path; archive in Google Drive customer/vendor/year/month, stable file IDs; separate finalized snapshots and signed proofs of delivery.
9. Invoice emails: PDF, customer, amount due, due date, reference and reminders; receipts/payments manually verified by staff. Interac Autodeposit instructions may display but no bank connection or payment gateway in V1.
10. Analytics: date/customer/vendor/product/origin/warehouse filters; boxes, pieces, weight, sales, COGS and margins only with validated cost basis; stock, outstanding, customer/vendor performance, seasonal historical comparisons. Predictions deferred.
11. Focus V7 historical migration after backup investigation, dry-run, mapping, reconciled opening balances; Focus stays live through parallel verification.

## Explicit deferrals
- Machine-learning predictions, what-if forecast engine and WordPress form-builder/embedded leads: **future release**.
- Stripe/Apple Pay/Interac Business Request Money API: discussed but **not in current V1**. A secure manual payment ledger is included.
- Complete replacement of Focus on day 60 is **not promised**; day 60 is build/pilot target, production cutover only after parallel reconciliation.

## Constraints and unknowns
- Exact invoice PDF not yet supplied; do not invent fonts/layout/margins.
- NAPS2/TWAIN/WIA connectivity to Toshiba e-STUDIO 409CS not yet bench-tested; use manual upload fallback.
- Focus V7 backup format and historical data relationships unknown; screenshots show XML and report exports, not confirmed full backup format.
- Pricing per lb vs per box, cost method and tax rules may differ per product/customer; require explicit owner signoff.
- These documents contain no real Focus customer database or confidential bank details.
