# Owner sign-off decision log (do not guess these)

| ID | Decision required | Default until approved |
|---|---|---|
| D-01 | What triggers legal invoice issuance? confirmed packing vs physical dispatch; same-day process? | treat warehouse-verified *dispatch confirmation* as finalization gate |
| D-02 | Finalized invoice correction process: credit note, void/reissue, time windows, HST compliance | no direct edit, manager-only corrective documents |
| D-03 | Tax rules by goods and jurisdiction; exempt/zero-rated cases | block production issue until verified by accountant |
| D-04 | Stock costing: FIFO/batch actual/weighted average, freight/duties allocation/currency conversion | record costs, do not show unsupported profit |
| D-05 | Negative stock, oversell and warehouse reservation | refuse negative stock |
| D-06 | How should `NO` be handled: backorder until closed, or ask manager to cancel? | pending backorder, never auto-cancel |
| D-07 | Per-product invoice price basis: fixed per case, per lb, per kg or per piece; split-case pricing | explicit product configuration. **Owner, 2026-11-10:** a box with no box price is priced per lb × the box weight (the size picked on the online order form, else the product's default case size); an explicit box price always wins |
| D-08 | Actual case weights: scale recorded per box, weighted total, or default estimated | estimated is clearly labeled; cannot book actual-weight price as verified |
| D-09 | Product units case-to-piece or case-to-weight, storage measurement policy | conversions require product-specific validated rule |
| D-10 | Physical inventory/dispatch and invoice timing, returned goods, rejection and spoils | recorded separately; require warehouse signoff |
| D-11 | Invoice numbering, number prefix, historical Focus numbers, fiscal periods | **Format set by the owner 2026-11-09:** `TT2026-000001` = prefix `TT` + fiscal year + 6-digit yearly sequence (room for 999,999 a year; ~44,000 expected). New invoices only; the year's sequence carries on after numbers already issued. Historical Focus numbers and fiscal periods still open |
| D-12 | Real customer invoice PDF template, paper size, fonts, signature/multicolumn totals | **Settled 2026-11-09 (owner's design):** template `sales-invoice-1`, US Letter, black and white only (greyscale logo, grey fills), Helvetica; header with letterhead, Invoice No./Date/Bus #/Vendor #; Bill To / Ship To; Salesperson / Customer PO No. / Terms; Qty, Units, Description, LBS, KG, Unit Price, Amount (no S/N column); totals of quantity and weight; three notes; Gross/Tax/Net; one signature, the receiver's. See `docs/modules/05-invoicing.md` |
| D-13 | Actual Focus V7 backup format and export permissions | investigative only |
| D-14 | Current Toshiba driver/network and whether click-to-scan works on Windows office PC | manual upload fallback |
| D-15 | Google Workspace account vs personal Drive, storage residency and employee access | company-controlled Shared Drive preferred |
| D-16 | Payment instructions/reminder dates and authorized verification roles | simple manual ledger, no API |
| D-17 | Historic transaction migration vs only opening balances and 12-24 months reports | proceed only with reconciliation evidence |
| D-18 | OCR accuracy target and operator review turnaround | 95% quantity-field target in pilot, full review regardless |
| D-19 | Data retention, audit log retention and Canadian privacy obligations | must approve compliance policy |
| D-20 | Client's preferred actual application name (Tami vs Tamisha One) | working name **Pro Trading** only |
| D-21 | Order Connect: who may approve website orders and convert them into draft sales orders | Owner/Admin and Manager approve + convert; Sales may review, reject, mark spam/duplicate only |
| D-22 | Order Connect public address: same domain as the staff app, or a separate one (e.g. orders.tamishatrading.com) | `PUBLIC_APP_ORIGIN` empty = staff app origin; a separate subdomain is recommended (isolates customer pages from staff cookies); reverse proxy must route `/embed/*` and `/api/*` there |
| D-23 | Units customers may order in | **Owner, 2026-11-09:** box or per lb for every product (plus pieces where packed so); box size chosen from the product's case sizes |
| D-24 | Show prices on the public form? which price book, currency and tax wording | off by default; when on, only the STANDARD rate of the price book in effect, labelled as a guide; prices never taken from the browser |
| D-25 | Product photos on the public form (source, licensing in `apps/web/public/products/CREDITS.md`) | uses `/products/<SKU>.webp` when present, initials otherwise |
| D-26 | Captcha service (Cloudflare Turnstile / hCaptcha) on the public form | none: honeypot, minimum fill time, per-IP/per-form hourly limits; spam kept as SPAM for review |
| D-27 | Website-order retention period and privacy-notice wording (part of D-19) | keep (no automatic erasure), no consent checkbox; both switchable in Order Connect settings |
| D-28 | Receipt email to customers: wording, sender, provider (D-15) | off; when on, a plain "received, not confirmed, not an invoice" note via the outbox |
| D-29 | Form publishing rules, duplicate window, reopening rejected/spam | publish needs required Products + a name + a contact; same contact+items within 24 h flagged (not blocked); rejected/spam/duplicate can be reopened |
| D-30 | Customers created from a website order: group, terms, external ref | created with the submitted name/email/phone, no group, 0 payment-terms days; staff complete it in Customers |
| D-31 | Online Orders quote requests: should Pro Trading produce a quote document (PDF/email with prices) or is a reply by phone/email enough? Reference prefix stays `OC-`? | quote requests use the same review flow and convert to a DRAFT order; no quote document; `OC-` references |
| D-32 | Standard box sizes offered for products with no recorded case weight (until the real product list with case sizes is imported); one BOX price per product whatever the size | 10, 20, 25, 40, 50 lb (`STANDARD_BOX_SIZES_LB` in `order_connect/forms.py`); a product's own BOX case sizes replace the list automatically; the chosen size goes on the draft order line as a note |

For each decision record approver, date, selected option, affected schema/APIs and test updates in an ADR. Never silently infer legal/financial answers.
