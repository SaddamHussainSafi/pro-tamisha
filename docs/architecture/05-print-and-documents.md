# Printing, invoice template fidelity, archival and revision rules

## Hard requirement
**Generated invoice PDF must visually and semantically match the user-supplied existing PDF template.** The actual sample has not been provided; do NOT invent layout or claim pixel accuracy. Obtain blank/sample/redacted invoice PDF, expected paper size, physical margins, fonts, invoice line wrapping behavior, tax/totals placements, second-page handling and Toshiba 409CS print output.

## PDF implementation
- Render server-side using HTML/CSS+headless Chromium or WeasyPrint; select engine after pixel-print proof. Templates version-controlled as assets, tested against approved golden PDF snapshots.
- Invoice data source = immutable issued invoice snapshot, never mutable product master data. Include invoice number, sold quantities by case/weight/pieces, customer address, agreed unit rate, due date, taxes, customer PO/reference, notes and page totals as required by official template.
- A final invoice has one issued PDF snapshot; reprint the **same bytes** for unchanged document. A correction is a formally linked revised/credit document with reason, actor, timestamps and preserved prior snapshot.
- Print from Chrome through normal browser print or office print agent if true one-click silent printing required; browsers generally cannot silently print to arbitrary devices without kiosk policy or local agent. Printer path tested on Toshiba e-STUDIO 409CS, correct paper selection and scaling (100%, not fit-to-page unless reference requires).
- Long invoices: verify repeated headers, page number, summary only at end, row split policy, signature fields and final page. Generate test PDFs with 1, 15, 40, 100 rows.

## Storage
- Postgres = source of truth; Google Drive = document archive. Create company-owned Shared Drive if eligible; folder pattern Customers/{customer-id-name}/{YYYY}/{MM} and Vendors/{vendor-id-name}/{YYYY}/{MM}/{shipment-id}; IDs unique and stable, names sanitized.
- Save Drive `file_id` and `sync_status`. Updating a **draft** PDF may update a known file ID; finalized original is never overwritten. Keep source scan separate from final invoice and signed POD.
- Worker uses outbox and retries; Drive 429 with backoff; show `Pending Sync`, `Synced`, `Failed` on UI, never assume success without provider acknowledgement. Maintain independent encrypted offsite backups of documents; Drive is not itself sufficient backup.
- Emails use generated finalized PDF and invoice details, due date, customer name, contact; retries must not send duplicates (provider dedupe token and send state). Suppress reminders after fully paid.

## Template acceptance
1. Owner supplies original PDF and sign-off copy.
2. Match dimensions/fields and visual diff at 150+ DPI for 1/15/40-line cases.
3. Print actual paper on Toshiba and compare with current Focus output.
4. Approve template hash/version; any future change requires sign-off.
