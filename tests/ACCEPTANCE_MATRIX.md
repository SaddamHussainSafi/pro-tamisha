# Acceptance test matrix: code required, not yet implemented

| ID | Given | Action | Expected |
|---|---|---|---|
| INV-001 | SO requested 15 boxes, pick verified 10 | issue first invoice | inv #A 10 boxes, stock -10, order remaining 5 |
| INV-002 | Same SO remaining 5, next pick verified 5 | issue second invoice | inv #B (not #A) 5 boxes, total stock -15, remaining 0 |
| INV-003 | 10 simultaneous retries of INV-001 same key | issue | only one invoice + one movement per line |
| INV-004 | stock 8, two verified picks of 6 each | concurrent issue | at most one succeeds; balance never negative |
| INV-005 | issued invoice then price update | read issued doc | amount and PDF hash identical |
| INV-006 | same product 7 cases 20/30/40/25 | price per lb 1.50 | 205 lb and CAD 307.50 |
| GRN-001 | docs declared 25, accepted 23, damaged 2 | post verified GRN | +23 saleable, no +25 |
| GRN-002 | same GRN post 100x | replay | +23 total only |
| OCR-001 | handwritten NO + 5 | review scan | no accidental automatic invoice; needs confirm |
| OCR-002 | scan job repeated hash | reupload | one source record, auditable reprocess versions |
| SEC-001 | warehouse role | POST invoice issue | 403 and denial audit |
| SEC-002 | arbitrary site | call bridge localhost | denied; not scanned |
| MIG-001 | 100 Focus sample rows | import dry-run | preserved source fields, explicit mapping, detailed variances |
| DOC-001 | finalized invoice 30 rows | generate print PDF | matches real signed-off template once uploaded |
| OPS-001 | backed-up database | restore staging | all FK/ledger/invoice invariants check out |

Each test should be implemented in pytest/Playwright under codebase tests once application exists. A checklist is **not** a passing test suite.
