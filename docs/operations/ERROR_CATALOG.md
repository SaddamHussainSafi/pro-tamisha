# Error catalog — what goes wrong, what people see, what they can do

Owner request (2026-11-12): *"fix everything about the error handling everywhere properly, analyse
every possible error and properly address proper outcome, actions after an error one by one."*

Every failure in Pro Trading now answers three questions in plain words:
**what happened**, **was anything saved**, and **what to do next**. No raw codes, stack traces or
"Something went wrong" dead ends. Business rules (money, stock, invoice locks) were not changed —
only how failures are reported and recovered from.

## How it works

| Layer | Piece | Job |
|---|---|---|
| API | `apps/api/src/pro_trading/errors.py` | One envelope for every error: `{"error": {"code", "message", "request_id", ...}}`. Unexpected errors are classified (database down → 503 `DB_UNAVAILABLE`, deadlock → 409 `BUSY_RETRY`, duplicate row → 409 `CONFLICT`, Redis down → 503 `SERVICE_UNAVAILABLE`, anything else → 500 `INTERNAL`) and never leak SQL, paths or exception text. Every error is logged with its request id (`api error`, `http error`, `validation error`, `unhandled error` with traceback in `exc`). |
| API | `bodylimit.py` | Too-large bodies (413) also carry a request id and say "Nothing was saved". |
| API | `routes/client_errors.py` | `POST /api/v1/client-errors`: the web app's screen crashes land in the server log with the reference the person sees. Signed-in staff only, 20/min, fields capped, nothing stored in the database. |
| Web | `src/api.ts` | Every request: 120 s timeout; no answer → `OFFLINE` / `NETWORK` / `TIMEOUT`; 5xx without an envelope (proxy, API restarting) → `SERVER_UNREACHABLE`; every `ApiError` carries the HTTP method and the request id. Uploads (XHR) do the same. |
| Web | `src/lib/errors.ts` | `describeError(err)` → title, message, saved?, next step, action, request id, fields to highlight. `errorText(err)` → one paragraph. Every feature helper (`receivingMessage`, `paymentMessage`, `gmailMessage`, `catalogMessage`, `pricingMessage`, `ordersMessage`, `ocrMessage`, `inventoryMessage`, `invoiceMessage`, `customerMessage`, `supplierMessage`, `scanMessage`, `documentMessage`, `staffMessage`, `trashErrorText`) handles its own codes and falls back to it. `keepEdits()` keeps typed input after a version conflict. |
| Web | `ui/primitives.tsx` `ErrorState` | Any "Couldn't load …" panel now explains why (from the error, or the failed request of the last 15 s), shows the support reference for server problems, and offers the right button: Try again / Reload page / Go back — none for "ask an admin" or "sign in" (the dialog handles that). |
| Web | `components/ErrorBoundary.tsx` | A crash while drawing a screen shows a card (Try again · Reload page · Go to Overview) with a reference, inside the normal frame (sidebar stays). Whole-app boundary in `main.tsx`. A screen whose code can't download (update installed / offline) reloads once automatically, then explains. |
| Web | `components/SessionExpired.tsx` | Sign-in ends while working → a dialog over the page asks for the password again. The page and what was typed stay; after sign-in you carry on in the same place. "Use a different account" goes to the sign-in page and returns to the same address. |
| Web | `components/ConnectionBanner.tsx` | Offline → banner "You're offline… What you've typed stays here"; back online → "Back online" for 4 s. |

**"Was anything saved?" rule.** The API refuses (4xx) before changing anything, inside a
transaction that is rolled back, so a refused change says *Nothing was saved*. When no answer came
back for a change (network drop, timeout, server crash), the screen says *We couldn't confirm
whether it was saved — check the list before trying again, so it isn't entered twice.* Loading a
page never talks about saving. Changes that were submitted with an idempotency key are safe to send
again with the same key (the server replays the first answer).

**Support reference.** Server problems (5xx) show `Reference for support: 1a2b3c4d` — the first 8
characters of the request id. Search the API log for that request id to find the error and its
traceback. Screen crashes show `web-xxxxxxxx`; search the log for `"reference": "web-xxxxxxxx"`.

## Shared codes (same meaning on every screen)

| Code | HTTP | Cause | What the user sees | Their data | Next action offered |
|---|---|---|---|---|---|
| `OFFLINE` | — | Computer has no internet | Banner + "This computer isn't connected to the internet." | Typed input stays; a change sent without answer: "couldn't confirm whether it was saved" | Reconnect, Try again |
| `NETWORK` | — | Connection dropped / API restarting | "Pro Trading didn't answer — the connection dropped or the server is restarting." | As above | Try again |
| `TIMEOUT` | — | No answer within 120 s (uploads 10 min) | "Pro Trading didn't answer in time." | As above | Try again |
| `SERVER_UNREACHABLE` | 502/503/504 no envelope | API down behind the proxy | "The server is down or restarting." | As above | Wait a minute, try again; tell whoever runs the server |
| `INTERNAL` | 500 | Unexpected bug | "The server hit an unexpected problem." + reference | Change: "couldn't confirm"; load: nothing | Try again; give support the reference |
| `DB_UNAVAILABLE` | 503 | PostgreSQL down / pool exhausted | "The database is not answering right now." | Nothing saved | Wait a minute, try again (`Retry-After: 30`) |
| `SERVICE_UNAVAILABLE` | 503 | Redis down in a request path | "A part of Pro Trading isn't answering right now." | Nothing saved | Wait, try again |
| `BUSY_RETRY` | 409 | Deadlock / serialization failure | "Someone else was saving the same records at the same moment." | Nothing saved | Try again |
| `CONFLICT` | 409 | Unexpected duplicate (constraint not pre-checked) | "This clashes with a record that already exists." | Nothing saved | Reload, try again |
| `UNAUTHENTICATED`, `SESSION_INVALID` | 401 | Signed out / session expired or revoked | Dialog "Your sign-in has ended" over the page | Nothing saved; typed input kept | Sign in again (same page) |
| `CSRF_FAILED` | 403 | Tab's token stale (signed in again elsewhere) | Renewed automatically and resent once; only if the session is gone → sign-in dialog | Nothing saved | Automatic, else sign in again |
| `MFA_REQUIRED` | 403 | Two-step code not entered yet | "Enter your two-step sign-in code first." | Nothing saved | Sign in again with the code |
| `FORBIDDEN` | 403 | Role lacks the permission / warehouse | "Your account doesn't have permission for this." | Nothing saved | Ask an owner or admin (no retry button) |
| `ORIGIN_REJECTED` | 403 | Request from another site | "This request didn't come from the Pro Trading page, so it was blocked." | Nothing saved | Open Pro Trading directly |
| `NOT_FOUND` | 404 | Deleted, in Trash, other company, bad link | "This record doesn't exist any more — it may have been deleted or moved to Trash." | Nothing saved | Go back / Try again |
| `METHOD_NOT_ALLOWED` | 405 | Out-of-date page | "That action isn't available here." | Nothing saved | Reload page |
| `VERSION_CONFLICT` | 409 | Someone else saved first | "Someone else changed this while you were working on it." | Nothing saved | Reload to see theirs; customer form keeps what was typed (`keepEdits`) |
| `VALIDATION` | 422 | Missing / invalid field (Pydantic) | "Some details are missing or not valid. Check the highlighted fields." `fields[].field` names each input | Nothing saved | Fix the highlighted fields |
| `IDEMPOTENCY_IN_PROGRESS` | 409 | Double click while first is running | "Your first click is still being processed." | First one may complete | Wait, reload to see the result |
| `IDEMPOTENCY_KEY_REUSED` | 409 | Same key, different body | "This was already sent with different details." | First one stands | Reload and check |
| `PAYLOAD_TOO_LARGE`, `FILE_TOO_LARGE` | 413 | Body over `MAX_REQUEST_BYTES` / file over the scan limit | "This is too large to send in one go." | Nothing saved | Smaller file / split it |
| `UNSUPPORTED_MEDIA_TYPE`, `UNSUPPORTED_FILE`, `FILE_TYPE_MISMATCH` | 415 | Wrong file type | "That type of file isn't accepted here." | Nothing saved | Use PDF, JPEG or PNG |
| `RATE_LIMITED`, `TOO_MANY_REQUESTS`, `TOO_MANY_ATTEMPTS`, `TOO_MANY_STREAMS` | 429 | Throttles | "Too many requests in a short time." | Nothing saved | Wait, try again |
| `ACCOUNT_LOCKED` | 423 | 5 failed sign-ins | "Too many failed sign-ins, so this account is locked for a while." | — | Wait 15 min, then try again |
| `CLIENT` | — | Bug in the screen code | "Something unexpected went wrong on this screen." | Unknown | Reload to check whether the last change was saved |
| any unknown code | any | New server code the web doesn't know | The server's own text if it is plain words, otherwise safe words for the HTTP status | By status | By status |

## Outages, background jobs and integrations

| Source | What happens | What the user sees | Their data | Next action |
|---|---|---|---|---|
| PostgreSQL down | `DB_UNAVAILABLE` 503; `/health/ready` reports `database: false` | "The database is not answering right now… nothing was saved" | Nothing saved (transaction never committed) | Wait, try again |
| Redis down | Rate limits fail open (sign-in still works); live streams (SSE) reconnect; Redis use inside a request → `SERVICE_UNAVAILABLE` | Live badges pause; actions explain | Nothing lost: jobs live in the database outbox | Try again when back |
| Celery worker down | Outbox events stay `PENDING`; nothing is lost | Emails/OCR/Drive copies wait ("queued") | Saved; will run when the worker returns | Ask whoever runs the server to restart the worker |
| Background job gives up (5 tries) | Outbox event `FAILED`; owners/admins get a notification (`outbox._alert_failed`) | Notification "… failed" | Business record unaffected | Retry from the record (e.g. resend email) |
| Invoice email fails | `email_status = FAILED`, `invoice.email_failed` notification | Invoice shows the email failed | Invoice unchanged (emails never alter it) | Check the address, send again |
| Gmail not set up | `GMAIL_NOT_CONFIGURED` 503 | "Gmail is not set up on this server yet. Ask an owner or admin to set up the Gmail connection." | — | Admin sets it up |
| Gmail token revoked / expired | `GMAIL_REAUTH_REQUIRED` 409 | "Google asked for the mailbox to be connected again. Press Connect Gmail…" | Nothing saved | Connect Gmail again |
| Gmail / Google not answering | `GMAIL_UNAVAILABLE` / `GMAIL_ERROR` 502 | "Gmail did not answer. Try again in a moment." | Nothing saved | Try again |
| Google OAuth callback problems | `GMAIL_STATE_INVALID`, `GMAIL_SCOPE_MISSING`, `GMAIL_NO_REFRESH_TOKEN`, `GMAIL_WRONG_MAILBOX`, `GMAIL_ALREADY_CONNECTED` | Plain sentence for each (`gmailMessage`) | No connection saved | Start again from Gmail Inbox, allow access / use the right mailbox |
| Attachment gone / unreadable | `ATTACHMENT_GONE` 404, `UNREADABLE`, `EMPTY_FILE` 422 | Plain sentence | Nothing saved | Pick another file / ask the sender |
| Gemini / OCR engine fails | Extraction `FAILED` with `error_code`; scan progress shows OCR failed | "Couldn't read this scan" on the review screen | Scan kept; nothing posted (OCR never posts stock or money) | Read again, or enter the lines by hand |
| Scanner bridge | `DEVICE_AUTH_FAILED`/`DEVICE_REVOKED` 401, `DEVICE_NOT_APPROVED` 403, `PAIRING_CODE_INVALID`, `FINGERPRINT_MISMATCH`, `SCANNER_BUSY`, `SCANNER_NOT_CONFIGURED` | `scanMessage` sentences (pair again, confirm fingerprint, wait for the other scan) | Nothing posted | As the message says |
| PDF / print | PDF build error → `INTERNAL` 500 with reference | Plain message + reference | Record unchanged | Try again; give support the reference |
| Unknown route / old link inside the app | Router sends unknown addresses to Overview; old names redirect | — | — | — |
| Screen crash (render error) | `ErrorBoundary`, crash logged via `/client-errors` | "This screen had a problem…" + `web-xxxxxxxx` | Saved work safe; unsaved typing on that screen may be lost | Try again · Reload page · Go to Overview |
| Screen code can't download | Reload once automatically; then explain | "This screen couldn't be loaded" (offline variant too) | — | Reload page |

## Feature codes (generated from every `ApiError(...)` in `apps/api/src/pro_trading`, 2026-11-12)

These codes come with a sentence written for people; the web shows it as written (feature helpers
rephrase some of them) and adds "Nothing was saved." when a change was refused. 421 raise sites,
199 code/status pairs in total (shared codes above are not repeated here).

| Code | HTTP | Where (module) | What the user sees (server text, shown as written) | Their data | Next action offered |
|---|---|---|---|---|---|
| `ACCENT_LOW_CONTRAST` | 422 | order_connect/forms | That colour is too light for white button text (needs 4.5:1 contrast) | Nothing saved (refused before any change) | Fix what the message names and try again |
| `ALREADY_ACCEPTED` | 409 | employees | They already set a password | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `ALREADY_ATTACHED` | 409 | receiving | This document is already on the shipment | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `ALREADY_CONVERTED` | 409 | order_connect/review, order_connect | This submission is already a sales order | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `ALREADY_DECIDED` | 409 | trash | This request was already {…} | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `ALREADY_ERASED` | 409 | order_connect/review | Personal data was already erased | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `ALREADY_INVOICED` | 409 | invoices | This packing sheet has already been invoiced | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `ALREADY_PROCESSED` | 409 | gmail/processing | This document was already processed | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `ALREADY_REVERSED` | 409 | payments, inventory | This allocation was already reversed / This posting was already reversed | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `ALREADY_VERIFIED` | 409 | payments | Payment {…} is already confirmed | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `ALREADY_VOIDED` | 409 | suppliers | {…} is already voided | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `ALREADY_WITHDRAWN` | 409 | receiving | That line was already withdrawn / That cost was already withdrawn | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `ATTACHMENT_GONE` | 404 | gmail/processing | This attachment is no longer on the email / This email is no longer in the mailbox | Nothing saved (refused before any change) | Go back to the list; it may be deleted or in Trash |
| `AWAITING_INVOICE` | 409 | orders | Picked goods are awaiting invoice; invoice first | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `CONSENT_TEXT` | 422 | order_connect | Write the privacy notice customers agree to | Nothing saved (refused before any change) | Fix what the message names and try again |
| `COUNT_BELOW_RESERVED` | 409 | inventory | Counted less than reserved; release first | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `CSV_INVALID` | 422 | pricing | {…} row error(s); nothing was saved | Nothing saved (refused before any change) | Fix what the message names and try again |
| `CURRENT_SESSION` | 422 | account, security | Use Sign out to end this session / Use Sign out to end your own session | Nothing saved (refused before any change) | Fix what the message names and try again |
| `CUSTOMER_CHOICE` | 422 | order_connect | Pick an existing customer or create a new one | Nothing saved (refused before any change) | Fix what the message names and try again |
| `CUSTOMER_INACTIVE` | 422 | orders | {…} is inactive | Nothing saved (refused before any change) | Fix what the message names and try again |
| `CUSTOMER_REF_REQUIRED` | 422 | pricing | Give the customer a reference code first (needed for CSV import/export) | Nothing saved (refused before any change) | Fix what the message names and try again |
| `DAMAGED_OVER_ARRIVED` | 422 | purchases | Damaged can't be more than what arrived | Nothing saved (refused before any change) | Fix what the message names and try again |
| `DEVICE_AUTH_FAILED` | 401 | scanning | Scanner bridge is not paired or was revoked | Nothing saved (refused before any change) | Sign in again (dialog over the page) |
| `DEVICE_NOT_ACTIVE` | 409 | scans | This scanner is not approved | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `DISCREPANCY_ACK_REQUIRED` | 422 | receiving | {…} line(s) differ from the vendor documents. Acknowledge them with a note. | Nothing saved (refused before any change) | Fix what the message names and try again |
| `DISPATCH_NOT_CONFIRMED` | 422 | invoices | Confirm the goods were dispatched before issuing the invoice (D-01) / Confirm the goods were handed over before issuing the invoice (D-01) | Nothing saved (refused before any change) | Fix what the message names and try again |
| `DOC_CONFLICT` | 409 | purchases | The supplier documents list different quantities. Withdraw the wrong line first. | Nothing saved (refused before any change) | Withdraw the wrong document line on the old shipment, then add it to stock |
| `DRAFT_EXISTS` | 409 | gmail/processing | A draft was already created from this document. Confirm to read it again. | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `DRIVE_DISABLED` | 409 | documents | Drive copying is turned off for this installation | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `DUPLICATE` | 409 | _common, inventory, warehouses | duplicate_msg / Lot code {…} already exists | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `DUPLICATE_CONFIRMATION` | 409 | payments | This bank reference already confirmed payment {…} | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `DUPLICATE_FIELD` | 422 | order_connect/forms | Field {…}: {…} appears twice | Nothing saved (refused before any change) | Fix what the message names and try again |
| `DUPLICATE_INVOICE` | 422 | payments | Each invoice may appear only once | Nothing saved (refused before any change) | Fix what the message names and try again |
| `DUPLICATE_LINE` | 422 | orders, inventory | Line {…}: {…} in {…} is already on the order / Each lot/unit may be counted once per count | Nothing saved (refused before any change) | Fix what the message names and try again |
| `DUPLICATE_REFERENCE` | 409 | suppliers | This reference is already recorded as {…} | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `DUPLICATE_UNIT` | 422 | inventory | Each unit may appear once per lot | Nothing saved (refused before any change) | Fix what the message names and try again |
| `DUPLICATE_UPLOAD` | 409 | pricing, scans | 'Already uploaded as ' + dup.version_code / This file was already uploaded elsewhere | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `EFFECTIVE_IN_PAST` | 422 | pricing | Effective time cannot be in the past; issued documents must keep the price they were issued with | Nothing saved (refused before any change) | Fix what the message names and try again |
| `EMAIL_QUEUED` | 409 | invoicing | This invoice is already waiting to be emailed | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `EMAIL_TAKEN` | 409 | employees | Someone already signs in with this email | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `EMPTY_COUNT` | 422 | receiving | Enter at least one accepted, damaged or rejected quantity | Nothing saved (refused before any change) | Fix what the message names and try again |
| `EMPTY_FILE` | 422 | gmail/processing, scanning | The attachment is empty / The file is empty | Nothing saved (refused before any change) | Fix what the message names and try again |
| `EMPTY_VERSION` | 422 | pricing | Add at least one price before activating | Nothing saved (refused before any change) | Fix what the message names and try again |
| `EXCEEDS_INVOICE_BALANCE` | 409 | payments | Invoice {…} has only CAD {…} open | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `EXCEEDS_PAYMENT` | 409 | payments | Only CAD {…} of this payment is unapplied | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `EXCEEDS_PENDING` | 409 | orders | Only {…} {…} is pending on line {…} | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `EXPIRY_REQUIRED` | 422 | inventory, receiving, purchases | {…} requires an expiry date / (asked only for goods going into stock now) | Nothing saved (refused before any change) | Fix what the message names and try again |
| `EXTRACTION_FINAL` | 409 | ocr | This scan was already {…} | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `FIELDS_UNRESOLVED` | 409 | ocr | {…} line(s) still need review | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `FILE_TYPE_MISMATCH` | 415 | scanning | The file contents do not match its type | Nothing saved (refused before any change) | Use a PDF, JPEG or PNG |
| `FINGERPRINT_MISMATCH` | 422 | scans | The code does not match the one on the scanner PC | Nothing saved (refused before any change) | Fix what the message names and try again |
| `FORM_CLOSED` | 409 | public_orders | This order form is not taking orders right now | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `FORM_EXPIRED` | 409 | order_connect/intake | This page was open too long. Reload it to continue. | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `FORM_NOT_FOUND` | 404 | public_orders | This order form is not available | Nothing saved (refused before any change) | Go back to the list; it may be deleted or in Trash |
| `FORM_UPDATED` | 409 | public_orders | This form was just updated. Please check your order and send it again. | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `FULLY_APPLIED` | 409 | payments | Payment {…} is fully applied | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `FUTURE_DATE` | 422 | payments, suppliers | The payment date cannot be in the future / The payment date is in the future | Nothing saved (refused before any change) | Fix what the message names and try again |
| `GMAIL_ALREADY_CONNECTED` | 409 | gmail | {…} is connected. Disconnect it before connecting another. | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `GMAIL_ERROR` | 502 | gmail | Google did not say which mailbox this is | Nothing saved | Try again in a moment |
| `GMAIL_NOT_CONFIGURED` | 503 | gmail | Gmail is not set up on this server yet (GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET) / Gmail is not set up on this server yet | Nothing saved | Ask an admin to finish the server setup / try again shortly |
| `GMAIL_NOT_CONNECTED` | 409 | gmail/processing, gmail | This email's mailbox is not connected, so its file cannot be fetched / No mailbox is connected | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `GMAIL_NO_REFRESH_TOKEN` | 400 | gmail | Google did not grant offline access. Remove Pro Trading under the Google account's third-party access, then connect again. | Nothing saved (refused before any change) | Fix the entry / ask for a new link |
| `GMAIL_REAUTH_REQUIRED` | 409 | gmail/processing | The mailbox must be connected again first | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `GMAIL_UNAVAILABLE` | 502 | gmail/processing | Gmail did not answer. Try again in a moment. | Nothing saved | Try again in a moment |
| `GMAIL_WRONG_MAILBOX` | 409 | gmail | Only {…} may be connected; you signed in as {…} | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `IMAGE_TOO_LARGE` | 422 | scanning | This page is too large to show / The image is too large to show | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INACTIVE_PRODUCT` | 422 | pricing | {…} is inactive | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INCOMPLETE_LINE` | 422 | ocr | Choose a product, unit and quantity | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INSUFFICIENT_STOCK` | 409 | inventory, invoicing | Only {…} {…} available in lot {…} / Only {…} {…} of {…} in stock; {…} picked. Check the shelf or count the stock. | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `INVALID_CATEGORY` | 422 | catalog | Category not found | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_CREDENTIALS` | 401 | auth | Email or password is incorrect | Nothing saved (refused before any change) | Sign in again (dialog over the page) |
| `INVALID_CUSTOMER` | 422 | orders, payments, pricing | Customer not found | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_DEVICE` | 422 | scans | Unknown scanner | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_DOCUMENT` | 422 | receiving | That document is not attached to this shipment | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_FIELD` | 422 | order_connect/forms | Field {…}: {…} must be {…} / Field {…}: custom fields use a custom_… key and a text, select or checkbox type | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_FIELDS` | 422 | order_connect/forms | A form has 1 to {…} fields | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_FORM_TOKEN` | 422 | order_connect/intake | Reload the page and try again | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_GROUP` | 422 | customers, pricing | Customer group not found | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_HOST` | 422 | order_connect | Enter a website address like shop.example.com / Use a public website name (no IP addresses or localhost) | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_INVOICE` | 422 | payments | Invoice not found | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_ITEM` | 422 | order_connect | Submission has no line {…} | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_LINE` | 422 | invoicing, orders | Weights given for a line with nothing picked / Line is not on this packing sheet | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_LOGO` | 422 | order_connect | Logo not found; upload it again | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_LOT` | 422 | orders | Lot is not this product in this warehouse | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_OPTIONS` | 422 | order_connect/forms | Field {…}: a choice list needs 2 to 50 different, non-empty options / Field {…}: only choice lists have options | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_PRODUCT` | 422 | line_preview, orders, inventory, ocr | Line {…}: product not found / Product not found | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_PROFILE` | 422 | catalog | {…} is a base unit and takes no conversion factors / Only one default unit profile is allowed | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_QUANTITY` | 422 | catalog, ocr | str(e) / The quantity must be more than zero | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_QUICK_RECEIVE` | 422 | purchases | Quick receive not found | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `INVALID_RANGE` | 422 | reports | The start date is after the end date | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_ROLE` | 422 | employees | Unknown role | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_SCAN` | 422 | receiving | Scan not found | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_SCOPE` | 422 | order_connect/forms, pricing | Pick at least one category or product / Some categories or products were not found | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_SHIPMENT` | 422 | ocr, suppliers | That shipment is not from this supplier | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_STAGE` | 422 | receiving | Unknown stage: {…} | Nothing saved (refused before any change) | Nothing to do: the list falls back to All |
| `INVALID_STATE` | 409 | documents, inventory, payments, ocr | Only failed copies can be retried / Reservation is not active | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `INVALID_THEME` | 422 | order_connect/forms | Accent must be a colour like #1B7A30 / Corner style must be none, small or large | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_TRANSITION` | 409 | order_connect/review | A {…} submission can't become {…} | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `INVALID_UNIT` | 422 | inventory, pricing | {…} is not stocked in {…} / {…} has no active {…} unit profile | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_VENDOR` | 422 | ocr, receiving | Supplier not found / Vendor not found | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVALID_WAREHOUSE` | 422 | inventory, ocr, order_connect, orders | Warehouse not found / Unknown warehouse | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVOICE_ALREADY_ENTERED` | 409 | purchases, ocr | Invoice {…} from {…} was already entered as {…}. Entering it again adds its goods to stock a second time. | Nothing saved (refused before any change) | Open the purchase it names; or confirm “Enter it again” if it really is a second delivery |
| `INVOICE_CUSTOMER_MISMATCH` | 422 | payments | The invoice belongs to another customer / Invoice {…} belongs to another customer | Nothing saved (refused before any change) | Fix what the message names and try again |
| `INVOICE_NOT_OPEN` | 409 | payments | Invoice {…} is {…} | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `IN_USE` | 409 | trash | f"{label} can't be deleted: it is used by {_ref_text(refs)}. {hint}".strip() / Something new started using this record while it was being deleted. Try again. | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `LAST_OWNER` | 422 | employees, trash | Keep at least one active Owner / Admin / Keep at least one active Super Admin | Nothing saved (refused before any change) | Fix what the message names and try again |
| `LINE_NOT_IN_STOCK` | 409 | purchases | That line is not in stock (yet) | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `LOGO_DIMENSIONS` | 422 | order_connect/forms | That image is too large in pixels | Nothing saved (refused before any change) | Fix what the message names and try again |
| `LOGO_TOO_LARGE` | 413 | order_connect/forms | Logos must be 1 MB or smaller / That logo is still over 512 KB as a PNG | Nothing saved (refused before any change) | Use a smaller file |
| `LOGO_TYPE` | 415 | order_connect/forms, order_connect | That file is not a readable PNG, JPEG or WebP / Use a PNG, JPEG or WebP logo | Nothing saved (refused before any change) | Use a PDF, JPEG or PNG |
| `LOT_NOT_SALEABLE` | 409 | inventory, orders | Lot {…} is not saleable | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `LOT_RESERVED` | 409 | inventory | Release reservations on this lot first | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `MATCH_REASON_REQUIRED` | 422 | purchases, ocr | The invoice and what was already added to stock differ. Say why. | Nothing saved (refused before any change) | The screen lists each difference and opens a reason field; give the reason and save again |
| `MFA_ALREADY_ENROLLED` | 409 | auth | Two-step sign-in is already on | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `MFA_INVALID` | 400 | auth | That code is wrong or was already used / That code is wrong. Check the time on your phone. | Nothing saved (refused before any change) | Fix the entry / ask for a new link |
| `MFA_NOT_ENROLLED` | 409 | auth | Two-step sign-in is not turned on | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `MFA_NOT_STARTED` | 409 | auth | Start the setup first | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `MORE_THAN_RECEIVED` | 422 | purchases | Only {…} {…} were received on this line | Nothing saved (refused before any change) | Fix what the message names and try again |
| `NEEDS_CORRECTION` | 422 | ocr | Line {…} was not read clearly. Correct it or remove it. | Nothing saved (refused before any change) | Fix what the message names and try again |
| `NOTHING_COUNTED` | 422 | receiving | Count at least one line first | Nothing saved (refused before any change) | Fix what the message names and try again |
| `NOTHING_TO_INVOICE` | 422 | invoicing | Nothing was packed on this sheet; there is nothing to bill | Nothing saved (refused before any change) | Fix what the message names and try again |
| `NOTHING_TO_PICK` | 409 | orders | Nothing is pending on this order | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `NOTHING_TO_PUBLISH` | 409 | order_connect | There are no unpublished changes | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `NOT_AIRPORT` | 409 / 422 | purchases, receiving | Only airport shipments are verified on arrival / Freight, duty and other landed costs are recorded for airport shipments only | Nothing saved (refused before any change) | Nothing to do for a local purchase |
| `NOT_APPROVED` | 409 | order_connect | Approve the submission before converting it | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `NOT_DELETABLE` | 409 | trash | message | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `NOT_PAIRED` | 409 | scans | The bridge has not used the pairing code yet | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `NOT_PUBLISHABLE` | 422 | order_connect/forms | 'Before publishing, add ' + ', '.join(missing) | Nothing saved (refused before any change) | Fix what the message names and try again |
| `NOT_REVERSIBLE` | 409 | payments, inventory | A reversal cannot itself be reversed | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `NOT_SUPPLIER_DOC` | 409 | ocr | This scan is not a supplier document | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `NOT_VERIFIED` | 409 | payments | Only a verified payment can be applied to invoices | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `NO_ALTERNATIVE` | 422 | trash | Nothing to do instead for a {…} | Nothing saved (refused before any change) | Fix what the message names and try again |
| `NO_CHANGES` | 422 | pricing | Prices are identical to {…}; nothing to publish | Nothing saved (refused before any change) | Fix what the message names and try again |
| `NO_EMAIL_ADDRESS` | 422 | invoicing | The customer has no email address; add one first | Nothing saved (refused before any change) | Fix what the message names and try again |
| `NO_HEADER` | 409 | ocr | Only supplier documents have header values | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `NO_LINES` | 422 | ocr, purchases | There are no lines to put on an order / There are no lines to declare / There are no lines to add to stock / This shipment has no lines to add to stock | Nothing saved (refused before any change) | Fix what the message names and try again |
| `NO_PRICE` | 404 | pricing | e.reason | Nothing saved (refused before any change) | Go back to the list; it may be deleted or in Trash |
| `NO_PRICE` | 409 | invoicing | {…} has no confirmed rate | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `NO_PRICE` | 422 | invoicing, orders | No per-lb price for {…} on the order's confirmation date / msg | Nothing saved (refused before any change) | Fix what the message names and try again |
| `OPEN_PICK_EXISTS` | 409 | orders | Packing sheet {…} is still open | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `ORDER_HAS_PICKS` | 409 | orders | Order has been picked; close it instead | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `OVER_PICK` | 422 | orders | Line {…}: picked {…}, only {…} requested | Nothing saved (refused before any change) | Fix what the message names and try again |
| `PAIRING_CODE_INVALID` | 403 | scans | Pairing code is wrong, used or expired | Nothing saved (refused before any change) | Ask an owner or admin |
| `PASSWORD_REQUIRED` | 422 | auth | Enter your password to turn this on | Nothing saved (refused before any change) | Fix what the message names and try again |
| `PICK_INCOMPLETE` | 422 | orders | {…} line(s) have no packed quantity | Nothing saved (refused before any change) | Fix what the message names and try again |
| `PIECES_DIFFER` | 422 | purchases | {…} pieces counted, {…} expected. Enter what arrived for the lines that differ. | Nothing saved (refused before any change) | The dialog opens the lines: enter what arrived and why |
| `POSTING_KEY_CONFLICT` | 409 | inventory | This event was already posted differently | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `PRODUCT_INACTIVE` | 409 | inventory, receiving | {…} is inactive | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `PRODUCT_INACTIVE` | 422 | orders, ocr | Line {…}: {…} is inactive / {…} is inactive | Nothing saved (refused before any change) | Fix what the message names and try again |
| `PUSH_NOT_CONFIGURED` | 401 | gmail | Push is not set up on this server | Nothing saved (refused before any change) | Sign in again (dialog over the page) |
| `PUSH_REJECTED` | 401 | gmail | This push could not be verified | Nothing saved (refused before any change) | Sign in again (dialog over the page) |
| `QUICK_ALREADY_MATCHED` | 409 | purchases | {…} was already matched to an invoice | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `QUICK_NOT_ON_INVOICE` | 422 | purchases, ocr | The goods already added to stock are not on this invoice: {…}. Add them as lines, or leave that quick receive out. | Nothing saved (refused before any change) | Choose “Different goods” for that quick receive, or add the missing line |
| `RANGE_TOO_LONG` | 422 | reports | Choose a range of at most {…} days | Nothing saved (refused before any change) | Fix what the message names and try again |
| `REASON_REQUIRED` | 422 | ocr, orders, purchases | Say why this line should be removed / Give a reason for cancelling the shortfall / Say why the count differs / Say why it is refused / Say what was done about it | Nothing saved (refused before any change) | Fix what the message names and try again |
| `REBUILD_CONFLICT` | 409 | inventory | Ledger for lot {…} {…} cannot satisfy its reservations | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `RESTORE_CONFLICT` | 409 | trash | {…} can't be restored. / {…} can't be restored: something it belongs to no longer exists (restore that first if it is in the Trash). | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `SAME_PASSWORD` | 422 | employees | Choose a new password | Nothing saved (refused before any change) | Fix what the message names and try again |
| `SCANNER_BUSY` | 409 | scans | This scanner is already scanning another sheet | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `SCANNER_NOT_CONFIGURED` | 503 | scanning | Scanner signing key is not configured on this server | Nothing saved | Ask an admin to finish the server setup / try again shortly |
| `SCAN_NOT_RECEIVED` | 409 | ocr, receiving | This scan has no document yet / That scan has no document yet | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `SECURITY_ALERTS_LOCKED` | 422 | notifications | Account and security alerts can't be turned off; ask an administrator | Nothing saved (refused before any change) | Fix what the message names and try again |
| `SELF_CHANGE` | 422 | employees, trash | You can't {…} your own account here / You can't suspend your own account | Nothing saved (refused before any change) | Fix what the message names and try again |
| `SHORTAGE_REASON_REQUIRED` | 422 | orders | Line {…}: say why less was packed | Nothing saved (refused before any change) | Fix what the message names and try again |
| `SOURCE_DOCUMENT_REQUIRED` | 409 | inventory | {…} is corrected through its source document | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `STALE_COUNT` | 409 | inventory | Stock moved after counting; recount these lots | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `SUPPLIER_NEEDED` | 409 | receiving | Choose the supplier (complete the details) before adding documents | Nothing saved (refused before any change) | Open the quick receive and choose Complete details |
| `SUSPENDED` | 409 | employees | Reactivate them first | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `TAX_RULES_UNVERIFIED` | 409 | invoicing | Invoices cannot be issued until tax rules are verified by the accountant (D-03) | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `TOKEN_INVALID` | 400 | account | This link is invalid, already used or expired. Ask for a new one. | Nothing saved (refused before any change) | Fix the entry / ask for a new link |
| `TOO_MANY_STREAMS` | 429 | inventory, notifications, scan_progress | Too many live stock views open; close a tab / Too many live notification views; close a tab | Nothing saved (refused before any change) | Wait a moment and try again |
| `TRY_AGAIN` | 503 | order_connect/intake | Please try again in a moment | Nothing saved | Ask an admin to finish the server setup / try again shortly |
| `TWO_STEP_REQUIRED` | 403 | trash | Turn on two-step sign-in in My account before deleting or restoring records. | Nothing saved (refused before any change) | Ask an owner or admin |
| `TWO_STEP_REQUIRED` | 409 | auth | Your company requires two-step sign-in | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `UNIT_NOT_DIVISIBLE` | 422 | inventory | {…} quantities must be whole numbers | Nothing saved (refused before any change) | Fix what the message names and try again |
| `UNKNOWN_CONVERSION` | 422 | catalog | str(e) | Nothing saved (refused before any change) | Fix what the message names and try again |
| `UNKNOWN_PRODUCT` | 422 | pricing | Product not found | Nothing saved (refused before any change) | Fix what the message names and try again |
| `UNMAPPED_LABEL` | 422 | receiving | “{…}” isn't a known product name for {…}. Choose the product. | Nothing saved (refused before any change) | Fix what the message names and try again |
| `UNREADABLE` | 422 | gmail/processing, scanning | The file could not be opened (damaged or password-protected) / The PDF could not be opened | Nothing saved (refused before any change) | Fix what the message names and try again |
| `UNSUPPORTED_FILE` | 415 | gmail/processing, scanning | UNSUPPORTED / Only PDF, PNG, JPEG or TIFF files can be uploaded | Nothing saved (refused before any change) | Use a PDF, JPEG or PNG |
| `USE_RECEIVING` | 409 | ocr | Supplier documents go to Receiving, not to an order | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `VALIDATION_FAILED` | 422 | order_connect/intake | Please check the highlighted fields / Some products need attention | Nothing saved (refused before any change) | Fix what the message names and try again |
| `VENDOR_INACTIVE` | 409 | receiving | {…} is inactive | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `WAREHOUSE_INACTIVE` | 409 | inventory, orders | Warehouse {…} is inactive | Nothing saved (refused before any change) | Reload to see the current state, then do what the message says |
| `WEAK_PASSWORD` | 422 | account, employees | problem | Nothing saved (refused before any change) | Fix what the message names and try again |
| `WEIGHTS_MISMATCH` | 422 | invoicing | {…}: weights cover {…} cases but {…} were picked | Nothing saved (refused before any change) | Fix what the message names and try again |
| `WEIGHTS_NOT_ALLOWED` | 422 | invoicing | {…} is sold by {…}; case weights apply to boxes only | Nothing saved (refused before any change) | Fix what the message names and try again |
| `WEIGHT_NOT_ALLOWED` | 422 | orders | Line {…}: a weight is for boxes; {…} is ordered by {…} | Nothing saved (refused before any change) | Fix what the message names and try again |
| `WEIGHT_REQUIRED` | 422 | orders | msg | Nothing saved (refused before any change) | Fix what the message names and try again |
| `WRONG_PASSWORD` | 422 | auth, employees | Your password is not right / Your current password is not right | Nothing saved (refused before any change) | Fix what the message names and try again |
| `WRONG_WAREHOUSE` | 422 | inventory, ocr, scans | Lot {…} is not in {…} / That shipment belongs to a different warehouse | Nothing saved (refused before any change) | Fix what the message names and try again |
| `ZERO_QUANTITY` | 422 | inventory | Quantity must not be zero | Nothing saved (refused before any change) | Fix what the message names and try again |

## Known gaps / follow-ups

- **Typed input after a version conflict** is kept on the customer form (`keepEdits`). Other edit
  forms (products, price books, receiving, orders, Online Orders) still reload the latest values
  and ask to make the change again; each needs the same small change.
- Field highlighting from server `VALIDATION.fields` is available (`describeError(err).fields`) but
  most forms validate on the screen first and only show the one-line message from the server.
- `ErrorState` without an explicit `error` uses the failed request of the last 15 s; pages that pass
  their own `detail` keep it.

## Tests

- API: `apps/api/tests/test_errors.py` — envelope shape, classification of unexpected errors
  (500/503/409), no internals leaked, request id echoed in body and header, logging with request id
  and traceback, plain framework messages, validation field names, 413 with request id, crash
  reports.
- Web: `apps/web/src/lib/errors.test.ts` — every shared code → title/message/next/action; saved
  wording for writes vs loads; feature + unknown codes; support reference; every feature helper's
  fallback; the fetch wrapper (envelope, no-envelope 5xx, offline/network/timeout); `keepEdits`.
- E2E: `apps/web/e2e/errors.spec.ts` — server 500 with reference and Try again, API down (502),
  forbidden, offline banner and recovery, session expiry with typed text kept, version conflict,
  missing record, screen code that can't download.
