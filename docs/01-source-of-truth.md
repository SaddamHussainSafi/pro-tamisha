# Decisions, evidence and assumptions

| Status | Decision / fact | Implementation effect |
|---|---|---|
| Confirmed (user) | Morning document is draft order, never numbered invoice | order UUID/public order number only |
| Confirmed (user) | Warehouse confirms what is actually provided | separate pick confirmation; `NO` = not picked |
| Confirmed (user) | Later goods get a **new invoice number** | multiple invoices can link to one order |
| Confirmed (user) | Prices change weekly or daily and differ per store | effective-dated price versions + precedence |
| Confirmed (user) | Same product repeated at different weights/pieces | individual sales lines with same product FK |
| Confirmed (user) | Approved invoice immediately reduces stock | idempotent transactional posting, SSE event |
| Confirmed (user) | Supplier docs single/multi-document with scans or PDFs | shipment group + document attachment + GRN |
| Confirmed (user) | Price versions backed up permanently | immutable change sets and snapshots |
| Confirmed (user) | Google Drive archive per customer/vendor and month | opaque Drive file IDs + controlled permissions |
| Confirmed (user) | Payment manually checked; no banking credentials | manual payment posting / optional instructions |
| Confirmed (user) | Predictions and WordPress form builder later | not V1 |
| Observed in photos | Toshiba e-STUDIO 409CS / FC-4090CS | bench-test scanner local driver |
| Observed in screenshots | Focus V7.0129 International desktop; sales order links, CASE-40/Piece-50, LB/KG, batch fields, Excel/PDF/XML exports | legacy source investigation, no assumptions about physical DB |
| Unverified | Exact invoice print layout | blocked on original PDF |
| Unverified | Invoice correction/legal/tax policy and backdated document rules | decision log |
| Unverified | Exact product stock units / conversions and landed-cost method | decision log |
| Unverified | Barcode/QR and driver-based scanning capabilities | hardware PoC |

When a request conflicts with this file, ask for confirmation rather than overwriting the established accounting flow.
