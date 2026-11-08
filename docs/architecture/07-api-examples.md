# API command examples and error semantics

**Illustrative contract examples**, not production endpoints until API code exists. UUIDs synthetic. For every POST supply `Idempotency-Key: unique-random-string` and authenticated session + CSRF token where cookie auth is used. `amount`/`quantity` values are decimal strings.

## 1 Create draft original sales order
`POST /api/v1/sales-orders`
```json
{"customer_id":"11111111-1111-4111-8111-111111111111","source_document_id":null,"lines":[{"product_id":"22222222-2222-4222-8222-222222222222","unit_code":"BOX","ordered_qty":"15"}]}
```
Response `201`: `{"order_id":"...","order_no":"ORD-1001","status":"DRAFT","invoice_no":null}`.

## 2 Open picking attempt
`POST /api/v1/sales-orders/{orderId}/pick-attempts` -> `201 {"pick_id":"...","status":"CREATED","attempt_no":1}`.

## 3 Submit and verify picking
`POST /api/v1/pick-attempts/{pickId}/verify`
```json
{"lines":[{"order_line_id":"...","picked_qty":"10","unit_code":"BOX","shortage_reason":"SHORT_STOCK"}],"confirmed_by_employee":true,"source_scan_job_id":null}
```
Response `200`: `{"pick_id":"...","status":"VERIFIED","remaining_by_order_line":[{"order_line_id":"...","qty":"5"}]}`. Server validates actual authorized operator, ignores forged identity flag as authority; the flag only signals UI acknowledgment.

## 4 Issue first real final invoice
`POST /api/v1/pick-attempts/{pickId}/issue-invoice`
```json
{"warehouse_id":"...","dispatch_confirmed":true,"effective_date":"2026-11-07","expected_pick_version":2}
```
Response `201`: `{"invoice_id":"...","invoice_no":"INV-1001","status":"ISSUED","stock_posted":true,"pdf_sync_status":"PENDING"}`. Repeated key returns same invoice.

## 5 Issue next-day second invoice
New picking attempt for remaining 5; same endpoint -> different `invoice_no` `INV-1002` linked to same `sales_order_id`. Original `ORD-1001` retained unchanged. Do **not** reuse first pick.

## 6 Create price-list version
`POST /api/v1/price-books/versions` with `{"source":"CSV","effective_at":"2026-11-09T08:00:00-05:00","entries":[{"sku":"CASSAVA","unit":"LB","rate":"1.35","currency":"CAD"}]}` -> draft + diff/errors. Separate manager activation command.

## 7 Activate price-list version
`POST /api/v1/price-books/versions/{versionId}/activate` -> `200 {"status":"ACTIVE","previous_version":"..."}`. Historical invoice snapshots must not change.

## 8 Create shipment with multiple vendor docs
`POST /api/v1/shipments` -> `201` draft. Upload scanned PDF/AWB/packing list/commercial invoice each as separate document; content hash and source type retained; link to shipment only after employee verified vendor and references.

## 9 Post approved GRN
`POST /api/v1/grns/{grnId}/post` with `{"expected_version":3}` -> `200 {"status":"POSTED","stock_movement_ids":["..."]}`. Retry returns same movements; never duplicate +23 accepted boxes.

## 10 Scan job and human review
`POST /api/v1/scans/jobs` with `{"type":"CUSTOMER_ORDER","input_mode":"SCANNER"}` -> pending job + short-lived device capability issued through separate authorized pairing workflow. `POST /api/v1/scans/jobs/{jobId}/review` with confirmed/corrected fields -> approved **draft** only; never issue invoice or post stock.

## 11 Manual payment
`POST /api/v1/invoices/{invoiceId}/payments` -> reported payment. Authorized Finance explicitly verifies bank deposit in separate command and applies allocation; reported != paid.

## 12 Analytics
`GET /api/v1/analytics/sales?from=2026-11-01&to=2026-11-30&customer_id=...` -> server-calculated totals, `data_as_of` timestamp and unit labels. Do not total quantities across mixed units.

## Error contract
```json
{"error":{"code":"INSUFFICIENT_STOCK","message":"Only 8 BOX available in Warehouse A","request_id":"...","details":{"requested":"10","available":"8"}}}
```
- `400` malformed semantics; `401` unauthenticated; `403` disallowed; `404` wrong scope/unknown; `409` version/stock/idempotency conflict; `422` validation; `429` throttling; `503` external dependency unavailable. No internal stacktrace, secrets, sensitive entity values in errors.
