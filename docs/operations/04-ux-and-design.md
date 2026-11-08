# UI/UX specification — Pro Trading

See `design/UI_DESIGN_REFERENCE.md` (authoritative) and `design/tokens.json` (provisional values pending live-site CSS verification).

## Functional requirements

Left collapsible nav: Overview, Orders, Picking, Invoices, Customers, Vendors, Products, Pricing, Inventory, Receiving, Scan Center, Documents, Payments, Reports, Settings. Top global search, notifications, current company/warehouse, scanner status, account. Breadcrumb and per-role action bar.

## Screens
- `Orders`: table with statuses, latest scans, ordered/picked/dispatched/remaining; click reveals immutable original scan and connected invoices.
- `Picking`: warehouse-friendly quantity boxes, explicit shortage reason, QR code, big save/submit action, scan original preview.
- `Invoice Editor`: customer selector and due date, editable weighted case groups by product, price source badge, calculated line totals, totals sidebar, finalization confirmation. One product can repeat by line. Finalized view is read-only with reprint and authorized correction actions.
- `Price Management`: A–Z product grid, keyboard navigation, per-customer override toggle, changed count, CSV template, upload preview, version compare, activate now/schedule later with approval.
- `Scan Center`: scanner health check, one-click scan, drag/drop upload fallback, OCR progress, original document split-pane, uncertainty highlighting, retry, review/approve; do not auto-post.
- `Receiving`: documents grouped by shipment, vendor match, item list with declared/received/damaged/accepted quantities, GRN approval.
- `Inventory`: stock on hand, reserved, available, actual pounds, boxes, movements by lot with audit link; live events.
- `Analytics`: compact KPI overview + 8 global filters; drill-down tables and comparison periods, no projected profits without cost basis.
- `Documents`: customer/vendor directory, YYYY/MM folder metadata, Drive sync states, download/reprint and signed proofs.

## Interactions
Loading skeletons, empty states, 409 conflict diff, disabled unauthorized actions but enforce server permissions, undo only for drafts, explicit modal for irreversible financial actions; error messages include non-sensitive reference ID. Batch operations show preview and validation. Accessibility keyboard focus order/ARIA, screen reader status, WCAG 2.2 AA target.
