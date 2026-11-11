# Module 01-catalog: Catalog, units and warehouses

**Priority:** P0 foundation/core

## Business outcome
Canonical product per SKU, aliases on vendor forms, categories, active flags, box/piece/lb/kg units, variable case sizes, lot/warehouse and optional expiry.

## Non-negotiable business rules
Same product may have different case weights and still one product ID; default case weight is only a suggestion.

## Design and integration
Create product_unit_profiles and aliases, immutable IDs; SKU unique; unit conversions strictly product-specific.

## Interfaces / UI
- Desktop-first, keyboard-friendly, dense responsive tables, clear status badges (not color alone), search, multi-filters, bulk operations with confirmation and undo policy.
- Every data mutation is a typed backend API with strong server authorization, idempotency key, audit event and optimistic version/conflict response where relevant.
- Show original source document and change history when decisions depend on scanned text or financial details.

## API/data checklist
- Define request/response schemas and errors in `contracts/openapi.yaml` before building route handlers; generate TS types.
- Add normalized tables, indexes and foreign keys to versioned Alembic migrations based on `db/schema.sql`.
- Implement `GET` list/detail filters with pagination; `POST` create; commands for approve/issue/post; no generic direct PATCH of immutable posted documents.
- Changes that cross invoice/stock/payment boundaries are one DB transaction; external IO via outbox.

## Acceptance tests
Cassava 20/30/40 lb groups calculate correct 205 lb for 7 boxes; reject unknown piece-to-weight conversions.

## Failure / fallback
- Network retries must return same outcome; duplicate actions cannot duplicate postings.
- Unavailable OCR/Drive/printer must not corrupt authoritative records; continue via manual route where safe.
- Logs include request_id / actor and non-sensitive error context.

## Completion evidence
Schema migration, contract, backend unit+integration tests, authorization test, one happy-path E2E, failure injection, operator-facing screenshot and decision approvals.

---

## Implementation notes (Module 01 build)

**Tables:** migration `apps/api/migrations/versions/0002_catalog.py` adds:
- `product_categories`
- `products`: SKU unique per company; three capital letters, or three plus `P` for a packaged twin (migration 0027)
- Packaged items (owner, 2026-11-11): every product has a packaged twin, its own product (`<SKU>P`, "<name> - Packaged", own price and stock, same stock unit, no case size or price until set) in the **Packaged** category, which every list shows last (lists sort by SKU length, then SKU). New products get theirs when created; `scripts/packaged_products.py` fills in an existing catalog.
- `product_aliases`: `alias_norm` unique per company + source
- `product_unit_profiles`: code unique per product, at most one active default (partial unique index)

**Units:** base units are LB/KG. BOX/PIECE profiles carry an optional `weight_lb_per_unit` and `pieces_per_unit`. A null weight means *unknown*: the calculator rejects it (`UNKNOWN_CONVERSION`) and never guesses. Arithmetic lives in `apps/api/src/pro_trading/units.py`; it is Decimal-only, rounds to 4 dp, and uses 1 lb = 0.45359237 kg.

**Variable case sizes:** one product can carry many case profiles (20/30/40/25 lb). The weight calculator sums mixed groups:
- An entered actual case weight beats the profile default.
- A total that uses any profile default is flagged `estimated` ("Estimated — confirm on scale").

**Immutability:** the DB trigger `catalog_reject_identity_change` blocks:
- changes to `products.sku`, `products.stock_unit` and `products.company_id`;
- changes to profile `unit_code`, weight, pieces and code;
- deletes of products and profiles.

  To change a conversion, add a new profile and deactivate the old one. Aliases can be deleted.

**Concurrency:** product edits and profile commands carry `version`. A stale version returns 409 `VERSION_CONFLICT` with `current_version`, and the UI reloads and shows a warning.

**Authorization:**
- `catalog.read` (any scope) for reads.
- A company-wide `catalog.manage` grant for mutations, and only after MFA verification.
- A warehouse-scoped manager can read but not manage.

**Alias resolution:** `GET /product-aliases/resolve?name=&source=`. The name is case-folded with whitespace collapsed. A source-specific alias beats a generic one. Module 07 (OCR) and Module 08 (receiving) will use this.

**Scope deferred:**
- Lots, per-warehouse stock and expiry dates belong to Module 03 (inventory). This module only stores `tracks_expiry`.
- Warehouses were delivered in Module 00.

## Failure recovery

| Failure | Behaviour | Operator action |
|---|---|---|
| Network drop / client retry | Same `Idempotency-Key` replays the stored response (`Idempotent-Replay: true`); no duplicate product/alias/profile | None — retry is safe |
| Crash mid-request (DB, audit write) | Whole transaction rolls back incl. the idempotency claim; retry with same key succeeds once (tested by failure injection) | Retry |
| Two people edit the same product | Second gets 409 VERSION_CONFLICT; UI reloads latest and asks to re-apply | Review latest, re-apply change |
| Duplicate SKU / alias / profile code | 409 DUPLICATE, nothing written | Use existing record or another code |
| Wrong case conversion entered | Cannot edit (trigger). Add corrected profile, deactivate wrong one; history is kept | Manager fixes via UI |
| Unknown piece weight at sale/receipt | 422 UNKNOWN_CONVERSION naming the group | Enter actual weight on scale |
| Key reused for different payload | 409 IDEMPOTENCY_KEY_REUSED | Client bug — new key per action |
