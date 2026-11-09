# Module 13 — Order Connect (website order forms)

Staff build customer order forms, embed them on the company's WordPress site with a plain iframe
(no plugin), and receive the submissions in Pro Trading. An approved submission becomes **one
DRAFT sales order** — never an invoice, a stock movement or a reservation.

## Screens (sidebar → Sales → Order Connect, `globe` icon, live unread badge)

| Page | Path | What it does |
| --- | --- | --- |
| Overview | `/order-connect` | Open / new counts, received (24 h, 30 d), share converted in 30 d, latest submissions, live forms |
| Form builder | `/order-connect/forms`, `/forms/:id` | Create forms; add, remove, rename, require and reorder fields (drag-and-drop or arrow buttons); text, colour, corners, logo, product scope, standard prices; draft → publish; live desktop/mobile preview |
| Submissions | `/order-connect/submissions`, `/submissions/:id` | Searchable inbox (live), status filters, review actions, notes, timeline, customer matching, conversion, privacy erasure |
| Embed & share | `/order-connect/embed` | iframe code and direct link to copy, allowed websites for a form, WordPress steps |
| Settings | `/order-connect/settings` | Allowed websites for all forms, staff alerts, receipt email, spam limits, privacy notice, retention |

The customer page is `/embed/{public_id}` — served by the API (plain HTML + one script + one
stylesheet, no framework, no third-party code). Steps: products (search, category filters,
photo/initials, units from the catalog, quantity stepper) → details → review → sent (reference
`OC-XXXXXX`). Accessible loading/error states, works at 390 px.

## Data (migration `0019_order_connect`, after `0018_notifications`)

- `order_connect_settings` — one row per company (defaults when absent).
- `order_forms` — `public_id` is 128 random bits (22 url-safe chars), unrelated to any id.
- `order_form_versions` — DRAFT / PUBLISHED / RETIRED; one draft and one published per form
  (partial unique indexes). A trigger keeps published versions immutable (only → RETIRED).
- `order_form_fields` — per version; a trigger refuses changes once the version is not a draft.
- `order_form_allowed_domains` — hosts allowed to frame a form (`form_id` NULL = every form).
- `order_form_logos` — uploads re-encoded to PNG by Pillow (≤ 800 px, ≤ 512 KB; no SVG).
- `order_form_submissions` — the full submission (`raw` + typed columns). Unique
  `(form_id, client_token)` (idempotency), unique `sales_order_id`, and the check
  `(status = 'CONVERTED') = (sales_order_id IS NOT NULL)`. A trigger keeps submitted values
  immutable (one-way privacy erasure excepted) and refuses deletes.
- `order_form_submission_items` — product/unit/qty plus the SKU and name as submitted (append-only).
- `order_form_submission_events` — review timeline (append-only).
- Staff alerts use the Notification Center tables (`notifications`, `notification_recipients`).
- `sales_orders.source` gains `WEB`.

Customers, products, employees and sales orders are the existing tables.

## Permissions

| Code | Owner | Manager | Sales | Others |
| --- | --- | --- | --- | --- |
| `order_connect.read` (see forms and submissions, get alerts) | ✓ | ✓ | ✓ | – |
| `order_connect.review` (reviewing, reject, duplicate, spam, notes) | ✓ | ✓ | ✓ | – |
| `order_connect.approve` (approve, undo approval, convert) | ✓ | ✓ | – | – |
| `order_connect.manage` (forms, publish, websites, settings, erasure) | ✓ | ✓ | – | – |

All are company-level grants (warehouse-scoped grants don't count). Converting also needs
`sales_order.manage` for the chosen warehouse, and `catalog.manage` to create a new customer.

## Review workflow

`NEW → REVIEWING → APPROVED → CONVERTED`; `NEW/REVIEWING → REJECTED | DUPLICATE | SPAM`;
`APPROVED → REVIEWING | REJECTED`; `REJECTED/DUPLICATE/SPAM → REVIEWING` (reopen).
`CONVERTED` is final. Conversion locks the submission row, runs inside `mutate()` (idempotency
key + audit + one commit), calls the same `insert_order` as the order form (`source='WEB'`, lines
carry "Website order OC-… line n: …" as their source text), and the unique `sales_order_id` stops a
second order even if two requests race.

## Intake protections (public API `/api/v1/public/order-forms/{public_id}`)

- No staff cookie is read: a submission never has staff rights.
- Signed form-load token (HMAC-SHA256 with `ORDER_CONNECT_KEY`, 12 h) bound to the form and the
  published version; republishing asks the customer to check and resend (`FORM_UPDATED`).
- Minimum fill time (default 3 s) and a honeypot field: hits are stored as SPAM silently.
- Redis limits per connection (hashed IP) and per form, per hour (settings); 600 config loads
  per IP per hour. Body cap 64 KiB (`ORDER_CONNECT_MAX_BODY_BYTES`).
- Strict schema: unknown keys (prices, totals, customer ids) are refused. Every value is
  re-validated against the published version: required fields, email/phone/date rules, choice
  lists, products active + in scope + same company, unit offered by the catalog, whole BOX/PIECE.
- Text is NFC-normalised, control characters removed, stored as typed and rendered as text only
  (React escaping; `textContent` in the embed script).
- Idempotency: the browser's `client_token` returns the same reference on retry (and under races).
- Possible duplicates (same contact + items in 24 h) are flagged, not blocked.
- One transaction stores the submission, items, timeline event, audit row, staff notifications and
  the outbox event `order_connect.submission_received` (the worker sends the optional receipt
  email through the dev mailbox / provider with a dedupe key).

## Embedding

- Snippet: `<iframe src="{PUBLIC_APP_ORIGIN}/embed/{public_id}" …>` — no secret in it.
- `Content-Security-Policy: frame-ancestors 'self' https://<allowed hosts>`; HTTPS only in
  staging/production (`http://localhost:*` and `http://127.0.0.1:*` are added in dev/test).
  `X-Frame-Options` is not sent on `/embed/*`. Referer/CORS are not relied on.
- "Direct link" off → a top-level visit (`Sec-Fetch-Dest: document`) shows "order on our
  website". This is a convenience, not a security boundary.
- Builder preview: `/embed/preview`, framable by the staff app only; it renders a draft received
  by `postMessage` from the staff origin and never sends anything.
- **Deployment:** the reverse proxy must route `/embed/*` and `/api/*` on `PUBLIC_APP_ORIGIN` to
  the API (Vite does this in dev). Production needs `ORDER_CONNECT_KEY` (≥ 32 chars) and an
  https `PUBLIC_APP_ORIGIN`, or the API refuses to start.

## Privacy

Optional privacy-notice checkbox (consent time stored). "Erase personal data" (manage) blanks
name, contact, address, notes, custom answers, raw values, user agent and IP hash; products,
status and the order link stay. A daily Celery beat task applies the company retention period to
finished submissions (converted, rejected, duplicate, spam). IPs are only stored as keyed hashes.

## Tests

- API: `tests/test_order_connect.py` (57) — forms/versions/immutability, builder rules, logo
  re-encoding, public config (no stock or customer prices; standard prices only when enabled),
  embed CSP and escaping, closed/link-disabled forms, intake validation, unknown products/units,
  spam, tokens, rate limit, body cap, foreign origin, staff cookie, duplicates, immutability
  triggers, notification persistence and per-person read state, review roles, customer matching,
  one-time and concurrent conversion, idempotent replay, company isolation, erasure, retention,
  consent, receipt email via outbox, overview.
- `tests/test_security_routes.py` treats `/api/v1/public/*` as anonymous-by-design.
- Web: `src/lib/orderConnect.test.ts` (14 vitest); `e2e/order-connect.spec.ts` (6 Playwright) —
  build/preview/publish, settings + website validation, a non-allowed site refused by CSP, a
  customer ordering inside a third-party page at 390 px with the staff inbox, badge and toast
  updating live, review → approve → convert, axe WCAG 2.2 AA on staff screens.
- **Not tested:** a real WordPress page on a staging site. The e2e "website" is a local page.

## Open decisions (owner)

See `docs/decisions/OPEN_DECISIONS.md` D-21 … D-28.
