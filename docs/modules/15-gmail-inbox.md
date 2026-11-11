# Module 15 — Gmail Inbox

One dedicated Gmail mailbox, read-only, kept in step with the database and shown at **Gmail
Inbox** (`/gmail`). Staff choose a scanned document from any stored email and send it to the
existing OCR review; Create invoice gains **Pick from Gmail**.

**Status (2026-11-11, evening): connected for real on the development machine.** The owner's
mailbox `tamishatrading@gmail.com` was connected at 17:01 through Google Cloud project "Tamisha
Trading ERP" (`tamisha-trading-erp`, owned by that account; consent screen External / Testing,
scope `gmail.readonly` only, OAuth client "Pro Trading (local)"). Verified on real mail: OAuth
consent and token exchange, the import of older mail, a new email arriving by itself through the
history feed, the inbox screen, the cleaned message view, the page preview of an emailed PDF, and
one real "Use for OCR" by the owner (Gemini read 1 page into 31 lines, waiting for review; no
order, invoice or stock movement was created). **Still not verified:** the Pub/Sub push (it needs
a public HTTPS address; this machine uses the one-minute timer), watch renewal, token refresh
after the first hour, recovery from an expired cursor and rate limiting against the real service
(covered by the stand-in tests only), and any deployment other than `localhost`.
Because the Google app is in "Testing", Google ends the sign-in after 7 days: the mailbox then
shows "Disconnected" and has to be connected again (OPEN_DECISIONS D-36).

## What it does, and what it never does

| Rule | How |
|---|---|
| Read-only | The only scope requested is `https://www.googleapis.com/auth/gmail.readonly`. The app cannot send, delete, label or mark mail. "Read / unread" is kept in Pro Trading only. |
| Receiving mail does nothing else | A sync stores sender, recipient, subject, snippet, body text, allow-listed HTML and the *names* of attachments. It never downloads a file, never starts OCR, never creates an order or invoice, never moves stock (`tests/test_gmail.py::test_new_mail_arrives_without_ocr_orders_or_stock`). |
| OCR is a person's choice | "Use for OCR" on one attachment fetches it, checks it and queues the **existing** reading (Module 07). The reading is reviewed in the existing screen; accepting it creates a **DRAFT sales order** only. Invoicing still goes order → picking → verified → invoice, unchanged. |
| No duplicates by accident | An attachment that was already used (or the same file from another email or a Scan Center upload, matched by SHA-256) is refused with `ALREADY_PROCESSED` unless a re-process is asked for (`gmail.reprocess`), and with `DRAFT_EXISTS` unless that is explicitly confirmed once a draft came out of it. A re-process is a new attempt: new scan, new reading; the earlier reading and its draft are untouched. The same click twice (same Idempotency-Key) is one request. |
| Untrusted input | Email text, file names and OCR output are stored and displayed, never interpreted. HTML is rebuilt from an allow-list (no script, style, image, form, iframe; links only `http(s)`/`mailto`) and shown in a sandboxed frame with `default-src 'none'`. File types come from the bytes, not from the email. |
| Secrets stay on the server | Client secret and tokens are read from the environment / stored encrypted (Fernet, `GMAIL_TOKEN_KEY`). They are never returned by the API, logged, or written to the audit trail. No Gmail password is ever asked for or stored. |

## Data (migration `0024_gmail_inbox`, additive; `alembic downgrade 0023` drops it)

- `gmail_accounts`: mailbox address, status (`CONNECTED`, `ERROR`, `REAUTH_REQUIRED`,
  `DISCONNECTED`), encrypted refresh/access token, **history cursor** (`history_id`), watch
  expiry, import position (`backfill_page_token`), last sync, last push, failure count, last
  error. One mailbox that is not disconnected per company (partial unique index).
- `gmail_messages`: unique `(account_id, gmail_message_id)`; thread, from, to, subject, snippet,
  `body_text`, `body_html` (already cleaned), received time, labels, attachment and PDF counts,
  read state, `removed_at` (mail deleted in Gmail is marked, never deleted here).
- `gmail_attachments`: unique `(message_id, part_id)` (Gmail's attachment ids are not stable; the
  MIME part id is); file name, declared type, size, `eligible`; after the first fetch: SHA-256,
  real type, page count; `source_document_id` once it was processed.
- `gmail_processing_jobs`: one row per "Use for OCR" attempt: attachment, attempt number,
  document type, warehouse, the `scan_jobs` row and first `ocr_extractions` row, who and when,
  whether it was a re-process and whether a duplicate was confirmed. The reading, its lines,
  flags and the resulting draft live in the existing OCR tables.

Attachment bytes are not stored at sync time. They are fetched from Gmail when someone previews,
downloads or processes a file. Processing keeps the file as a `source_documents` row, the app's
existing private store (one row per company and SHA-256).

## Sync

1. `sync_account` reads the saved cursor, asks `users.history.list` what changed since, and
   fetches each new message (`users.messages.get`, format full).
2. One transaction: lock the account row, insert the messages (`ON CONFLICT DO NOTHING`), apply
   label changes and removals, move the cursor. The cursor only moves forward and only together
   with the rows it covers. A crash before the commit means the same history is read again; a
   duplicate or out-of-order push finds nothing new.
3. HTTP 404 from `history.list` (Gmail keeps history "at least one week", sometimes less) starts
   a full resync: the mailbox's current history id becomes the cursor first, then the message
   list is walked page by page; stored messages are skipped by their id.
4. Older mail is imported page by page (`GMAIL_BACKFILL_BATCH` messages per step,
   `GMAIL_BACKFILL_DAYS` back; 0 = the whole mailbox). The page token is saved with each page.
5. 429 / `rateLimitExceeded` / 5xx are retried with exponential backoff (`Retry-After`
   honoured, `GMAIL_MAX_RETRIES`); after three failed runs the account shows **Not syncing** and
   administrators are notified; it keeps trying and loses no mail, because the cursor stays put.
6. A refused refresh token (`invalid_grant`) sets `REAUTH_REQUIRED`, stops the timer for that
   mailbox and raises a critical "Gmail disconnected" notification.

Three things start a sync, all running the same function: the Celery beat task
`pro_trading.gmail_poll` every `GMAIL_POLL_SECONDS` (the fallback, and the catch-up for a push
Google dropped), a verified Pub/Sub push, and the **Refresh** button (`POST /gmail/sync`).
With a topic configured the worker calls `users.watch` at connect and again once a day (Google:
at least every 7 days, daily recommended).

`POST /api/v1/gmail/push` is refused (401, audited, rate-limited per address) unless the caller
proves it is our subscription: Google's signed OIDC token (RS256; issuer, expiry,
`aud == GMAIL_PUSH_AUDIENCE`, `email == GMAIL_PUSH_SERVICE_ACCOUNT`, `email_verified`) and/or
the shared `?token=`. With neither configured every push is refused. The body is only a hint;
the worker reads the changes from its own cursor.

Live updates in the browser: new mail that carries a PDF or picture raises one notification per
sync through the existing Notification Center feed (SSE), which refreshes the open inbox at
once; ordinary mail appears on the page's own 20-second refresh. No timing is promised.

## API (`/api/v1/gmail/…`, company-wide permissions)

| Route | Permission |
|---|---|
| `GET /status` | `gmail.read` |
| `POST /oauth/start`, `POST /oauth/complete`, `POST /disconnect` | `gmail.manage` |
| `POST /sync` | `gmail.read` (6 per minute per person) |
| `GET /messages` (q, range, date_from, date_to, has_attachments, pdf, processed, unread, cursor, limit), `GET /messages/{id}`, `POST /messages/{id}/read` | `gmail.read`; the body is only returned with `gmail.body.read` |
| `GET /attachments/{id}` (with processing history) | `gmail.read` |
| `GET /attachments/{id}/pages/{n}` (PNG) | `gmail.attachment.read` |
| `GET /attachments/{id}/content` (download, audited) | `gmail.attachment.download` |
| `POST /attachments/{id}/process` | `gmail.process` + `scan.create` in the warehouse; `gmail.reprocess` for `reprocess` / `confirm_duplicate` |
| `POST /push` | no session; verified as above |

Default role grants (OPEN_DECISIONS D-33): Super Admin all seven; Admin all but `gmail.manage`;
Sales `read`, `body.read`, `attachment.read`, `attachment.download`, `process`; Warehouse,
Accounting and Viewer none. A role scoped to one warehouse does not open the mailbox.

Notifications: `gmail.message_received` (`gmail.read`), `gmail.sync_failed` and
`gmail.disconnected` (`gmail.manage`); OCR finished / failed use the existing `ocr.ready` /
`ocr.failed`. Audit: `gmail_account.connect`, `.disconnect`, `.reauth_required`,
`gmail_attachment.process`, `gmail_attachment.download`, `gmail.push.rejected`, plus every
`permission.denied`.

## Screens

- **Gmail Inbox** (`/gmail`, sidebar entry after Scan Center, added in `nav.ts` only): mailbox,
  connection chip, last synchronized, Refresh, Settings (administrators). Filters: All emails,
  Today, Yesterday, Last 7 days, Last 30 days, Custom range; Has attachments, PDF documents,
  Processed, Not processed; search by sender, subject, file name or a date (the body is not
  searched). Server-side filtering and paging. An email opens beside the list (one pane at a
  time on a phone) with its message and files: Preview (page by page, zoom, rotate, full
  screen), Download, Use for OCR.
- **Create invoice › Pick from Gmail** (`/invoices/new?from=gmail`): the same browser as a
  picker in a large dialog. Choose an email, select one attachment, **Select document**, then
  document type and warehouse, then the existing review screen.
- **Previously processed**: date, staff member, reading status, draft order; View draft, View
  extraction, Reprocess document (administrators; a tick is required when a draft exists).

## Setup (owner / administrator, in Google Cloud) — not done yet

1. **Project**: console.cloud.google.com → create a project (e.g. "Tamisha Trading ERP").
   Enable **Gmail API** and **Cloud Pub/Sub API**.
2. **OAuth consent screen** (Google Auth Platform → Branding / Audience):
   - If the scanning mailbox is in a Google Workspace organisation: user type **Internal**.
     Google lists internal-only apps as exempt from restricted-scope verification.
   - If it is a plain `@gmail.com` account: user type **External**. In publishing status
     **Testing**, add the mailbox as a test user; Google then issues refresh tokens that
     **expire after 7 days**, so the mailbox must be reconnected weekly. To avoid that the app
     must be set to "In production"; `gmail.readonly` is a **restricted** scope, and Google
     lists "personal use" (only a few users, all known to you) as a case where verification is
     not required, otherwise verification and a yearly security assessment apply. Which case
     this deployment falls under is OPEN_DECISIONS D-36; confirm on Google's "Restricted scope
     verification" page before relying on it.
   - Scope to add: `https://www.googleapis.com/auth/gmail.readonly`. Nothing else.
3. **OAuth client**: Credentials → Create credentials → OAuth client ID → **Web application**.
   Authorized redirect URI, exactly: `https://<the app's address>/gmail/oauth/callback`
   (development: `http://localhost:5173/gmail/oauth/callback`). Copy the client ID and secret.
4. **Server settings** (`apps/api/.env`, never committed), then restart the API **and** the
   Celery worker and beat:
   ```
   GMAIL_CLIENT_ID=…apps.googleusercontent.com
   GMAIL_CLIENT_SECRET=…
   GMAIL_TOKEN_KEY=…          # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   GMAIL_ALLOWED_ADDRESS=scans@…   # optional: only this mailbox may be connected
   # GMAIL_REDIRECT_URI=…     # only if it is not {FRONTEND_ORIGIN}/gmail/oauth/callback
   # GMAIL_POLL_SECONDS=60  GMAIL_BACKFILL_DAYS=0  GMAIL_BACKFILL_BATCH=50
   ```
   Keep `GMAIL_TOKEN_KEY` safe: without it the stored sign-in cannot be read and the mailbox
   has to be connected again.
5. **Connect**: Gmail Inbox → Settings → Connect Gmail (Super Admin). Sign in as the scanning
   mailbox on Google's page and allow read-only access. Mail starts arriving within a poll.
6. **Live push (optional; needs a public HTTPS address)**:
   - Pub/Sub → create topic, e.g. `gmail-inbox`. On the topic, grant **Pub/Sub Publisher** to
     `gmail-api-push@system.gserviceaccount.com`.
   - Create a service account for push (e.g. `gmail-push@<project>.iam.gserviceaccount.com`).
   - Create a **push** subscription on the topic: endpoint
     `https://<the app's address>/api/v1/gmail/push` (optionally `?token=<long random>`),
     **Enable authentication**, that service account, audience = the endpoint URL.
   - Server settings, then restart API and worker:
     ```
     GMAIL_PUBSUB_TOPIC=projects/<project>/topics/gmail-inbox
     GMAIL_PUSH_AUDIENCE=https://<the app's address>/api/v1/gmail/push
     GMAIL_PUSH_SERVICE_ACCOUNT=gmail-push@<project>.iam.gserviceaccount.com
     # GMAIL_PUSH_TOKEN=<the same long random value>   # optional second check
     ```
   Without this step the mailbox is checked every `GMAIL_POLL_SECONDS`. Locally (no public
   address) only the timer can run.
7. **First real check** (replaces "not verified"): send a test email with a PDF to the mailbox;
   it should appear in Gmail Inbox, open, preview, and go through Use for OCR. Record the result
   in `docs/operations/BUILD_LOG.md`.

Staging / production refuse to start with Gmail configured and the placeholder token key, a
non-HTTPS redirect, or a Pub/Sub topic without a way to verify pushes.

### Ending the 7-day reconnect (looked into 2026-11-11, nothing changed in Google)

- The 7-day limit is stated by Google only for apps in "Testing". The console's **Publish app**
  button (Audience page) is greyed out until the Branding page has an **application home page**
  and a **privacy policy link** (its tooltip: "Valid app name, support email, homepage url, and
  privacy policy url are required for switching the app to external production mode").
- Published but unverified: Google shows its "hasn't verified this app" warning when connecting
  and allows 100 users over the project's lifetime (one is needed). Google lists "personal use"
  (only you, or a few people you know) as a case where no review has to be requested. Its help
  pages are not consistent on whether a restricted scope such as `gmail.readonly` is then only
  warned about or refused; that can only be settled by publishing and reconnecting once, and
  "Back to testing" undoes it.
- After publishing, connect the mailbox again once: the sign-in granted while in Testing still
  ends after its 7 days.
- Full verification (privacy policy on a verified domain, written justification, demo video and
  a security assessment by an outside assessor, repeated every 12 months; Google says several
  weeks, possibly months) is only needed if the app must be verified. A Google Workspace mailbox
  with an Internal app avoids both the limit and verification.
- Google also ends a sign-in when the mailbox password changes or the connection is unused for
  six months; the page then shows "Disconnected" and the mailbox is connected again.

## Rollback

Unset `GMAIL_CLIENT_ID` (the feature is off; stored mail stays visible) or disconnect in
Settings (tokens wiped and withdrawn at Google). Schema: `alembic downgrade 0023`. Scans, OCR
readings and draft orders made from Gmail attachments live in their own tables and stay.

## Tests

- API: `apps/api/tests/test_gmail.py` (33): OAuth connect, refusals, allowed mailbox, token
  refresh, disconnect, revoked sign-in, new mail, duplicate and out-of-order signals, resumable
  import, expired cursor, rate limits, removed/relabelled mail, watch renewal, worker wiring,
  inbox filters/search/paging/read state, HTML cleaning and an injected "instruction",
  10-page preview and download, unsupported / disguised / oversized / broken files, OCR success
  through to a DRAFT order, OCR failure and re-read, duplicate protection, variable-weight
  lines, the Trash keeping a processed scan, role access, body permission, push verification (10 forged variants), shared token,
  production settings. `tests/test_security_routes.py` sweeps the new routes.
- Web: `src/lib/gmail.test.ts` (11), `e2e/gmail.spec.ts` (9) on a synthetic inbox from
  `apps/api/scripts/gmail_ui_fixture.py` (its mailbox row is DISCONNECTED: no connection is
  simulated; one test mocks the status answer in the browser to check the connected header and
  says so in its name).

## Known limits

- Exercised against the real service on 2026-11-11: OAuth, profile, message list, message get,
  history list, attachment get. Not exercised: watch / stop, Pub/Sub push, revoke; those follow
  Google's push, sync and Pub/Sub authentication guides as read that day.
- Found on the first real mailbox and fixed: Gmail gives ordinary attachments a `Content-ID`, so
  that header no longer marks a file as "inside the message"; only a picture with an inline
  disposition is.
- Ordinary mail (no document attached) appears on the next 20-second refresh, not instantly.
- The email body is not searchable. Download is limited to PDF, PNG, JPEG and TIFF; other
  attachment types are listed with the reason and can be opened in Gmail itself. Files are not
  virus-scanned (no scanner exists in the project).
- A scan made from a Gmail attachment cannot be moved to the Trash (the Trash already refuses a
  scan that has an OCR reading; the processing record points at it too).
- Document splitting (one PDF into several orders) is not offered: the existing review does
  not support it.
