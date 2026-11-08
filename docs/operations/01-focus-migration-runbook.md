# Focus V7 migration runbook

0. Obtain written permission, read-only access and backup from authorized Tamisha owner. **Never modify production Focus database**, never upload confidential backup to public AI/chat/tool.
1. Capture Focus version and edition (screenshots: V7.0129 International), machine/DB host and user permissions. Identify backup format and whether Focus vendor can supply schema/official export.
2. Make two verified encrypted copies, record hash, do not attempt destructive restore into live server. Restore in isolated VM/DB. If proprietary, request vendor-assisted export rather than reverse engineering under time pressure.
3. Export per master: customer ledger, vendor list, product/SKU/unit/batch, price table, stock ledger by cutoff date, sales orders, finalized invoices/line items, vendor purchases/receipts, payment receipts and outstanding AR/AP. Use visible Excel/PDF/XML exports if DB inaccessible.
4. Create `legacy_*` read-only staging tables, one row per source record plus raw payload/hash, source file and import batch ID.
5. Build mapping (`legacy_id`, `new_id`, `entity_kind`), normalize SKU and units without losing original codes. Preserve original Focus invoice IDs and related sales order references under legacy namespace; never assign legacy numbers to newly created invoices.
6. Import 100 records and 10 invoices; run exact field comparisons and aggregate totals. Document exceptions (duplicates, unit conversions, soft-deleted records, currency changes, rounding).
7. Opening stock: select cutoff time, compute accepted per warehouse/lot/unit and enter single audited `OPENING_BALANCE` posting only. Do NOT also replay all historical movements into live balances unless specifically designed for that mode.
8. Opening AR: verify invoice balance net of receipts/credits. Historical records are read-only and must not re-email customers or post inventory.
9. Dry-run at least twice with consistent results, reconcile by category and warehouse. Any unexplained stock/AR variance blocks cutover.
10. Parallel operations with one designated system of record and explicit no-double-posting policy. Document final cutover, backups, rollback, aftercare, and later full archival.

## Reconciliation sheet
`entity | Focus_count | Imported_count | difference | source_total | target_total | variance | unit/currency | owner_resolution | status`.

No guarantee can be made that full historical data will be parseable until a real Focus backup/export is inspected.
