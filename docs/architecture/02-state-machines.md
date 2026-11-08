# State machines and business transitions

## SalesOrder
`DRAFT` -> `CONFIRMED` -> `PICKING` -> `PARTIALLY_FULFILLED` -> `FULFILLED`; `CONFIRMED|PICKING|PARTIALLY_FULFILLED` -> `CLOSED_WITH_SHORTAGE`; `DRAFT|CONFIRMED` -> `CANCELLED` if no postings. Original scanned values persist as immutable source snapshot; subsequent corrections become tracked amendments, never overwriting source.

- `CONFIRMED`: reviewed customer request; **not an invoice**.
- `PICKING`: warehouse issued picking sheet/attempt; counted quantities may be zero with `NO` code.
- `PARTIALLY_FULFILLED`: sum of verified dispatched quantities >0 and remaining >0.
- `FULFILLED`: all ordered units either dispatched (if no cancelled) or explicit closure policy; avoid automatic closure if shortages unresolved.
- Track per line `ordered`, `cancelled`, `dispatched`; `remaining = max(ordered - cancelled - dispatched, 0)`. `reserved/picked` are separate and cannot exceed remaining/available as policy permits.

## PickingAttempt
`CREATED` -> `IN_PROGRESS` -> `SUBMITTED` -> `VERIFIED` -> `INVOICED`; `SUBMITTED` -> `NEEDS_CORRECTION` -> `SUBMITTED`; `CREATED|IN_PROGRESS` -> `ABORTED`. A scanned quantity is not verified until human confirms. Verification requires count/unit and shortage disposition. `NO` is 0 picked, reason out-of-stock, **not** cancelled.

## Invoice
`DRAFT` -> `ISSUED` -> `PARTIALLY_PAID` -> `PAID`; `ISSUED|PARTIALLY_PAID` -> `VOIDED/CREDITED` through manager-authorized compensating documents/stock movements as permitted by tax/accounting policy; no state transition changing issued line payload. A corrected document must preserve previous issued PDF and trace references. Invoice numbers assigned atomically only on `ISSUED`.

## VendorShipment / GRN
Shipment `DRAFT` -> `DOCUMENTS_REVIEWED` -> `ARRIVED` -> `RECEIVING` -> `RECONCILED` -> `CLOSED`. `CANCELLED` before stock posting; after that, corrections are adjustments.
GRN `DRAFT` -> `COUNTED` -> `APPROVED` -> `POSTED`; rejected docs -> `NEEDS_REVIEW`. Vendor-declared quantity and physically accepted quantity stored separately. Damaged/spoiled/rejected quantities recorded explicitly. Only `POSTED` increases on-hand saleable inventory; never raw OCR.

## Payment (external, manual)
`REPORTED` -> `VERIFIED` -> `APPLIED` or `REJECTED`. Manual verification needs bank/receipt evidence reference, employee and timestamp. Partial payment applies separately, cannot exceed invoice open amount without explicit credit/refund policy.

## Trigger rules
- Order draft: no stock effect, no invoice number.
- Picking confirmation: records physical preparation; may reserve stock if configured; not deduct physical on hand.
- **Invoice issue at dispatch**: one deterministic inventory movement per issued invoice line/lot allocation (business-selected posting moment). Invoice issue and on-hand deduction same DB transaction; physical dispatch confirmation must be part of issue workflow.
- New day partial delivery: new picking attempt + new invoice from remaining original order lines; **no duplicate sale of original draft**.
- GRN approval: post movement once; external email/Drive async.

## Invariant examples
- `0 <= dispatched + cancelled <= ordered` per normalized stock unit, except explicit manager-approved adjustments with compensating lines.
- Any movement has valid stock-item/warehouse/lot unit & source id; posting key unique; no direct ledger delete/update.
- Never convert lb to pieces without explicit product conversion rule; a case's actual catch weight overrides default when verified.
