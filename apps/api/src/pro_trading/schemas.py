"""Typed request/response bodies (mirrored in contracts/openapi.yaml)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=256)


class EmployeeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    display_name: str
    company_id: uuid.UUID


class GrantOut(BaseModel):
    permission: str
    warehouse_id: uuid.UUID | None


class SessionOut(BaseModel):
    employee: EmployeeOut
    mfa_required: bool  # this sign-in still needs its two-step code (or setup)
    mfa_enrolled: bool
    mfa_setup_required: bool = False  # company requires two-step and it is not set up yet
    roles: list[str] = []
    permissions: list[str]
    grants: list[GrantOut]
    expires_at: datetime


class LoginOut(SessionOut):
    csrf_token: str


class CsrfOut(BaseModel):
    csrf_token: str


class MfaEnrollOut(BaseModel):
    otpauth_uri: str


class MfaVerifyIn(BaseModel):
    """Either the 6-digit code from the authenticator app or one recovery code."""

    code: str | None = Field(default=None, pattern=r"^\d{6}$")
    recovery_code: str | None = Field(default=None, min_length=8, max_length=32)


class MfaEnrollIn(BaseModel):
    # Required when two-step is turned on from My account (re-authentication).
    password: str | None = Field(default=None, min_length=1, max_length=128)


class MfaConfirmIn(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class MfaConfirmOut(BaseModel):
    session: SessionOut
    recovery_codes: list[str]


class MfaChangeIn(MfaVerifyIn):
    """Turning two-step off or making new recovery codes: password plus a current code."""

    password: str = Field(min_length=1, max_length=128)


class RecoveryCodesOut(BaseModel):
    recovery_codes: list[str]


class WarehouseIn(BaseModel):
    code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)


class WarehousePatch(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    active: bool
    version: int = Field(ge=1)


class WarehouseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    name: str
    active: bool
    version: int


class FeatureFlagIn(BaseModel):
    enabled: bool
    description: str = Field(default="", max_length=500)


class FeatureFlagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    key: str
    enabled: bool
    description: str
    version: int


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    actor_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    warehouse_id: uuid.UUID | None
    success: bool
    request_id: str | None
    created_at: datetime


# ---------------------------------------------------------------- Module 01 catalog
# Decimals are serialized as JSON strings (never floats).

UnitCode = Literal["BOX", "PIECE", "LB", "KG"]
Qty = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]


class CategoryIn(BaseModel):
    code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    name: str
    active: bool


class UnitProfileIn(BaseModel):
    code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9_-]+$")
    unit_code: UnitCode
    pieces_per_unit: Qty | None = None
    weight_lb_per_unit: Qty | None = None
    is_default: bool = False


class UnitProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    unit_code: UnitCode
    pieces_per_unit: Decimal | None
    weight_lb_per_unit: Decimal | None
    is_default: bool
    active: bool


class CaseSizeOut(UnitProfileOut):
    """A unit profile listed across products (Products › Case sizes)."""

    product_id: uuid.UUID
    sku: str
    product_name: str
    product_active: bool


class AliasIn(BaseModel):
    alias: str = Field(min_length=1, max_length=200)
    source_label: str = Field(default="", max_length=100)


class AliasOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    alias: str
    source_label: str


def _upper_sku(v: Any) -> Any:
    return v.strip().upper() if isinstance(v, str) else v


# A product code is exactly three letters, e.g. "CAS". Lower case is accepted and stored upper case.
Sku = Annotated[
    str, BeforeValidator(_upper_sku), Field(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$")
]


class ProductIn(BaseModel):
    sku: Sku
    name: str = Field(min_length=1, max_length=200)
    category_id: uuid.UUID | None = None
    stock_unit: UnitCode
    tracks_expiry: bool = False
    unit_profiles: list[UnitProfileIn] = Field(default_factory=list, max_length=20)
    aliases: list[AliasIn] = Field(default_factory=list, max_length=50)


class ProductPatch(BaseModel):
    """sku, stock_unit and conversion values are immutable; only these fields may change."""

    name: str = Field(min_length=1, max_length=200)
    category_id: uuid.UUID | None
    tracks_expiry: bool
    active: bool
    version: int = Field(ge=1)


class VersionIn(BaseModel):
    version: int = Field(ge=1)


class ProductSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    sku: str
    name: str
    category_id: uuid.UUID | None
    stock_unit: UnitCode
    tracks_expiry: bool
    active: bool
    version: int


class ProductOut(ProductSummary):
    created_at: datetime
    updated_at: datetime
    unit_profiles: list[UnitProfileOut]
    aliases: list[AliasOut]


class ProductPage(BaseModel):
    items: list[ProductSummary]
    next_cursor: str | None


class AliasMatch(BaseModel):
    product: ProductSummary
    alias: AliasOut


class WeightGroupIn(BaseModel):
    profile_id: uuid.UUID
    count: Qty
    case_weight_lb: Qty | None = None


class WeightCalcIn(BaseModel):
    groups: list[WeightGroupIn] = Field(min_length=1, max_length=100)


class WeightGroupOut(BaseModel):
    profile_id: uuid.UUID
    count: Decimal
    unit_code: UnitCode
    weight_lb_per_unit: Decimal
    weight_lb: Decimal
    weight_source: Literal["ENTERED", "PROFILE_DEFAULT", "BASE_UNIT"]


class WeightCalcOut(BaseModel):
    groups: list[WeightGroupOut]
    units: dict[str, Decimal]
    total_lb: Decimal
    total_kg: Decimal
    estimated: bool


# ---------------------------------------------------------------- Module 02 pricing

PriceScope = Literal["STANDARD", "GROUP", "CUSTOMER"]
Rate = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=4)]


class CustomerGroupIn(BaseModel):
    code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)


class CustomerGroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    name: str
    active: bool


# Invoices and payment reminders are emailed here, so it must at least look like an address.
# Empty string = no email (and, on PATCH, clears it).
EMAIL_OR_EMPTY = r"^(|[^@\s]+@[^@\s]+\.[^@\s]+)$"


class CustomerIn(BaseModel):
    external_ref: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320, pattern=EMAIL_OR_EMPTY)
    phone: str | None = Field(default=None, max_length=32)
    fax: str | None = Field(default=None, max_length=32)
    bill_to_address: str | None = Field(default=None, max_length=300)
    ship_to_name: str | None = Field(default=None, max_length=200)
    ship_to_address: str | None = Field(default=None, max_length=300)
    vendor_no: str | None = Field(default=None, max_length=32)
    group_id: uuid.UUID | None = None
    payment_terms_days: int = Field(default=0, ge=0, le=365)


class CustomerPatch(BaseModel):
    """Omitted/null = unchanged. Empty text (email, phone, fax, address, vendor no.) = clear."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320, pattern=EMAIL_OR_EMPTY)
    phone: str | None = Field(default=None, max_length=32)
    fax: str | None = Field(default=None, max_length=32)
    bill_to_address: str | None = Field(default=None, max_length=300)
    ship_to_name: str | None = Field(default=None, max_length=200)
    ship_to_address: str | None = Field(default=None, max_length=300)
    vendor_no: str | None = Field(default=None, max_length=32)
    group_id: uuid.UUID | None = None
    clear_group: bool = False
    payment_terms_days: int | None = Field(default=None, ge=0, le=365)
    active: bool | None = None
    version: int = Field(ge=1)


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    external_ref: str | None
    name: str
    email: str | None
    phone: str | None
    fax: str | None
    bill_to_address: str | None
    ship_to_name: str | None
    ship_to_address: str | None
    vendor_no: str | None
    group_id: uuid.UUID | None
    payment_terms_days: int
    active: bool
    version: int


class PriceVersionIn(BaseModel):
    version_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    notes: str = Field(default="", max_length=2000)
    based_on_id: uuid.UUID | None = None


class PriceImportIn(BaseModel):
    version_code: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    notes: str = Field(default="", max_length=2000)
    csv: str = Field(min_length=1, max_length=2_000_000)


class PricePreviewIn(BaseModel):
    csv: str = Field(min_length=1, max_length=2_000_000)
    against_version_id: uuid.UUID | None = None


class PriceVersionOut(BaseModel):
    id: uuid.UUID
    version_code: str
    status: Literal["DRAFT", "ACTIVE", "ARCHIVED"]
    state: Literal["DRAFT", "SCHEDULED", "CURRENT", "SUPERSEDED", "ARCHIVED"]
    notes: str
    effective_at: datetime | None
    source_hash: str | None
    based_on_id: uuid.UUID | None
    stale: bool
    current_version_id: uuid.UUID | None
    created_by: uuid.UUID | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    entry_count: int
    version: int
    created_at: datetime


class PriceEntryIn(BaseModel):
    product_id: uuid.UUID
    unit_code: UnitCode
    scope: PriceScope = "STANDARD"
    customer_id: uuid.UUID | None = None
    group_id: uuid.UUID | None = None
    rate: Rate
    version: int = Field(ge=1, description="Price book version's optimistic version")


class PriceEntryRemoveIn(BaseModel):
    entry_id: uuid.UUID
    version: int = Field(ge=1)


class PriceEntryOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    product_name: str
    unit_code: UnitCode
    scope: PriceScope
    customer_id: uuid.UUID | None
    group_id: uuid.UUID | None
    target: str | None
    rate: Decimal
    currency: str


class PriceEntryPage(BaseModel):
    items: list[PriceEntryOut]
    next_cursor: str | None
    total: int


class ActivateIn(BaseModel):
    version: int = Field(ge=1)
    effective_at: datetime | None = Field(
        default=None, description="Defaults to now; must not be in the past"
    )


class RowErrorOut(BaseModel):
    row: int
    field: str | None
    code: str
    message: str


class DiffRow(BaseModel):
    product_id: uuid.UUID
    sku: str
    product_name: str
    unit_code: UnitCode
    scope: PriceScope
    target: str | None
    old_rate: Decimal | None
    new_rate: Decimal | None
    change: Literal["ADDED", "CHANGED", "REMOVED"]


class PriceDiffOut(BaseModel):
    against_version_id: uuid.UUID | None
    added: int
    changed: int
    removed: int
    unchanged: int
    rows: list[DiffRow]
    truncated: bool


class PricePreviewOut(BaseModel):
    valid: bool
    entry_count: int
    source_hash: str | None
    duplicate_of: uuid.UUID | None
    errors: list[RowErrorOut]
    diff: PriceDiffOut | None


class PriceQuoteOut(BaseModel):
    product_id: uuid.UUID
    unit_code: UnitCode
    customer_id: uuid.UUID | None
    rate: Decimal
    currency: str
    scope: PriceScope
    entry_id: uuid.UUID
    version_id: uuid.UUID
    version_code: str
    effective_at: datetime
    resolved_at: datetime


# ------------------------------------------------------------------ Module 03 inventory

QtyNonNeg = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=4)]
LotStatus = Literal["SALEABLE", "DAMAGED", "QUARANTINE", "EXPIRED"]
MovementType = Literal[
    "OPENING_BALANCE",
    "GRN_RECEIPT",
    "INVOICE_DISPATCH",
    "ADJUSTMENT",
    "COUNT_ADJUSTMENT",
    "DAMAGE",
    "RETURN",
    "REVERSAL",
]
AdjustReason = Literal["FOUND", "LOST", "SPOILAGE", "DATA_ENTRY", "OTHER"]
Note = Annotated[str, Field(min_length=3, max_length=500)]


class QtyLineIn(BaseModel):
    unit_code: UnitCode
    qty: Qty


class OpeningBalanceIn(BaseModel):
    warehouse_id: uuid.UUID
    product_id: uuid.UUID
    lot_code: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$"
    )
    batch_ref: str | None = Field(default=None, max_length=64)
    received_at: datetime | None = None
    expires_at: datetime | None = None
    landed_unit_cost: Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=4)] | None = None
    cost_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    lines: list[QtyLineIn] = Field(min_length=1, max_length=4)
    note: str = Field(default="", max_length=500)


class AdjustmentIn(BaseModel):
    lot_id: uuid.UUID
    unit_code: UnitCode
    delta_qty: Annotated[Decimal, Field(max_digits=14, decimal_places=4)]
    reason_code: AdjustReason
    note: Note


class DamageIn(BaseModel):
    lot_id: uuid.UUID
    unit_code: UnitCode
    qty: Qty
    note: Note


class ReturnIn(BaseModel):
    lot_id: uuid.UUID
    unit_code: UnitCode
    qty: Qty
    condition: Literal["SALEABLE", "DAMAGED"]
    source_ref: str = Field(min_length=1, max_length=64)
    note: str = Field(default="", max_length=500)


class ReverseIn(BaseModel):
    reason: Note


class LotStatusIn(BaseModel):
    status: Literal["SALEABLE", "QUARANTINE", "EXPIRED"]
    version: int = Field(ge=1)
    reason: Note


class ReservationIn(BaseModel):
    lot_id: uuid.UUID
    unit_code: UnitCode
    qty: Qty
    source_type: Literal["SALES_ORDER", "MANUAL"] = "MANUAL"
    source_ref: str = Field(min_length=1, max_length=64)


class CountLineIn(BaseModel):
    lot_id: uuid.UUID
    unit_code: UnitCode
    counted_qty: QtyNonNeg


class CountIn(BaseModel):
    warehouse_id: uuid.UUID
    note: str = Field(default="", max_length=500)
    lines: list[CountLineIn] = Field(min_length=1, max_length=500)


class CountDecisionIn(BaseModel):
    version: int = Field(ge=1)
    reason: str = Field(default="", max_length=500)


class BalanceOut(BaseModel):
    lot_id: uuid.UUID
    lot_code: str
    lot_status: LotStatus
    expired: bool
    batch_ref: str | None
    received_at: datetime | None
    expires_at: datetime | None
    product_id: uuid.UUID
    sku: str
    product_name: str
    warehouse_id: uuid.UUID
    warehouse_code: str
    unit_code: UnitCode
    qty: Decimal
    reserved_qty: Decimal
    available_qty: Decimal
    version: int


class BalancePage(BaseModel):
    items: list[BalanceOut]
    next_cursor: str | None
    total: int


class AvailabilityOut(BaseModel):
    product_id: uuid.UUID
    warehouse_id: uuid.UUID
    warehouse_code: str
    unit_code: UnitCode
    on_hand: Decimal
    reserved: Decimal
    available: Decimal
    unavailable: Decimal


class MovementOut(BaseModel):
    id: uuid.UUID
    lot_id: uuid.UUID
    lot_code: str
    product_id: uuid.UUID
    sku: str
    warehouse_id: uuid.UUID
    warehouse_code: str
    unit_code: UnitCode
    delta_qty: Decimal
    qty_after: Decimal
    movement_type: MovementType
    reason_code: str | None
    note: str
    source_type: str
    source_id: uuid.UUID
    reverses_movement_id: uuid.UUID | None
    reversed_by_id: uuid.UUID | None
    actor_name: str | None
    created_at: datetime


class MovementPage(BaseModel):
    items: list[MovementOut]
    next_cursor: str | None
    total: int


class PostingOut(BaseModel):
    """Result of any stock command: the ledger rows it posted (same rows on replay)."""

    source_type: str
    source_id: uuid.UUID
    lot_id: uuid.UUID | None = None
    movements: list[MovementOut]


class LotOut(BaseModel):
    id: uuid.UUID
    lot_code: str
    status: LotStatus
    expired: bool
    product_id: uuid.UUID
    sku: str
    product_name: str
    warehouse_id: uuid.UUID
    warehouse_code: str
    batch_ref: str | None
    received_at: datetime | None
    expires_at: datetime | None
    parent_lot_id: uuid.UUID | None
    damaged_lot_id: uuid.UUID | None
    landed_unit_cost: Decimal | None
    cost_currency: str | None
    version: int
    balances: list[BalanceOut]


class ReservationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    lot_id: uuid.UUID
    unit_code: UnitCode
    qty: Decimal
    status: Literal["ACTIVE", "RELEASED", "CONSUMED"]
    source_type: str
    source_ref: str
    created_at: datetime
    released_at: datetime | None


class CountLineOut(BaseModel):
    lot_id: uuid.UUID
    lot_code: str
    sku: str
    unit_code: UnitCode
    expected_qty: Decimal
    counted_qty: Decimal
    variance: Decimal
    current_qty: Decimal | None
    stale: bool
    movement_id: uuid.UUID | None


class CountOut(BaseModel):
    id: uuid.UUID
    count_no: str
    warehouse_id: uuid.UUID
    warehouse_code: str
    status: Literal["SUBMITTED", "POSTED", "REJECTED"]
    note: str
    counted_by_name: str | None
    decided_by_name: str | None
    created_at: datetime
    decided_at: datetime | None
    version: int
    lines: list[CountLineOut]


class DiscrepancyOut(BaseModel):
    lot_id: uuid.UUID
    lot_code: str
    unit_code: UnitCode
    cached_qty: Decimal | None
    ledger_qty: Decimal
    cached_reserved: Decimal | None
    active_reserved: Decimal


class ReconciliationOut(BaseModel):
    checked: int
    discrepancies: list[DiscrepancyOut]
    ledger_rows: int
    rebuilt: bool = False


# ---------------------------------------------------------------- Module 04 sales orders

OrderStatus = Literal[
    "DRAFT",
    "CONFIRMED",
    "PICKING",
    "PARTIALLY_FULFILLED",
    "FULFILLED",
    "CLOSED_WITH_SHORTAGE",
    "CANCELLED",
]
PickStatus = Literal[
    "CREATED", "IN_PROGRESS", "SUBMITTED", "NEEDS_CORRECTION", "VERIFIED", "INVOICED", "ABORTED"
]
ShortageReason = Literal["OUT_OF_STOCK", "DAMAGED", "NOT_FOUND", "OTHER"]
Disposition = Literal["BACKORDER", "CANCEL"]
Reason = Annotated[str, Field(min_length=3, max_length=500)]


class OrderLineIn(BaseModel):
    product_id: uuid.UUID
    unit_code: UnitCode
    qty: Qty
    source_line_text: str = Field(default="", max_length=500)
    note: str = Field(default="", max_length=500)
    # Pounds the whole line weighs (boxes only); the line is then priced per lb x this weight.
    weight_lb: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=4)] | None = None


class OrderIn(BaseModel):
    customer_id: uuid.UUID
    warehouse_id: uuid.UUID
    source: Literal["MANUAL", "SCAN", "WEB"] = "MANUAL"
    source_text: str = Field(default="", max_length=10_000)
    requested_for: datetime | None = None
    note: str = Field(default="", max_length=1000)
    lines: list[OrderLineIn] = Field(min_length=1, max_length=200)


class OrderDraftIn(BaseModel):
    """Replace a DRAFT's editable fields. The original capture is never changed."""

    version: int = Field(ge=1)
    customer_id: uuid.UUID
    warehouse_id: uuid.UUID
    requested_for: datetime | None = None
    note: str = Field(default="", max_length=1000)
    lines: list[OrderLineIn] = Field(min_length=1, max_length=200)


class ReasonIn(BaseModel):
    version: int = Field(ge=1)
    reason: Reason


class LineCancelIn(BaseModel):
    version: int = Field(ge=1)
    qty: Qty
    reason: Reason


class OrderLineOut(BaseModel):
    id: uuid.UUID
    line_no: int
    product_id: uuid.UUID
    sku: str
    product_name: str
    unit_code: UnitCode
    ordered_qty: Decimal
    cancelled_qty: Decimal
    dispatched_qty: Decimal
    awaiting_invoice_qty: Decimal
    remaining_qty: Decimal
    pending_qty: Decimal
    backorder: bool
    source_line_text: str
    rate: Decimal | None
    price_scope: str | None
    price_version_code: str | None
    note: str
    weight_lb: Decimal | None = None
    lb_rate: Decimal | None = None  # per-lb price a box rate was worked out from


class AmendmentOut(BaseModel):
    id: uuid.UUID
    line_no: int | None
    kind: str
    qty: Decimal | None
    reason: str
    actor_name: str | None
    created_at: datetime


class PickSummaryOut(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    order_no: str
    customer_name: str
    warehouse_code: str
    attempt_no: int
    pick_code: str
    status: PickStatus
    line_count: int
    created_at: datetime
    verified_at: datetime | None


class OrderOut(BaseModel):
    id: uuid.UUID
    order_no: str
    customer_id: uuid.UUID
    customer_name: str
    customer_ref: str | None
    warehouse_id: uuid.UUID
    warehouse_code: str
    status: OrderStatus
    source: Literal["MANUAL", "SCAN", "WEB"]
    source_text: str
    source_snapshot: list[dict[str, str]]
    requested_for: datetime | None
    note: str
    created_by_name: str | None
    confirmed_by_name: str | None
    confirmed_at: datetime | None
    close_reason: str | None
    has_backorder: bool
    open_attempt_id: uuid.UUID | None
    version: int
    created_at: datetime
    lines: list[OrderLineOut]
    attempts: list[PickSummaryOut]
    amendments: list[AmendmentOut]


class OrderSummaryOut(BaseModel):
    id: uuid.UUID
    order_no: str
    customer_name: str
    warehouse_code: str
    status: OrderStatus
    line_count: int
    requested_for: datetime | None
    created_at: datetime


class OrderPage(BaseModel):
    items: list[OrderSummaryOut]
    next_cursor: str | None
    total: int


class PickLineIn(BaseModel):
    line_id: uuid.UUID
    picked_qty: QtyNonNeg
    shortage_reason: ShortageReason | None = None
    lot_id: uuid.UUID | None = None
    weight_lb: Qty | None = None
    note: str = Field(default="", max_length=500)


class PickRecordIn(BaseModel):
    version: int = Field(ge=1)
    lines: list[PickLineIn] = Field(min_length=1, max_length=200)


class PickVerifyIn(BaseModel):
    version: int = Field(ge=1)
    dispositions: dict[uuid.UUID, Disposition] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=500)


class PickLineOut(BaseModel):
    id: uuid.UUID
    order_line_id: uuid.UUID
    line_no: int
    sku: str
    product_name: str
    unit_code: UnitCode
    requested_qty: Decimal
    picked_qty: Decimal | None
    outcome: Literal["PICKED", "SHORT", "NO"] | None
    shortage_reason: ShortageReason | None
    disposition: Disposition | None
    lot_id: uuid.UUID | None
    lot_code: str | None
    weight_lb: Decimal | None
    note: str
    units: str  # packing sheet UNITS: CASE-40, PIECE, LB...
    remarks: str  # packing sheet REMARKS: order line note, then the picker's note


class PickOut(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    order_no: str
    order_date: date  # the order's date (confirmed, company time zone), printed on the sheet
    customer_name: str
    warehouse_id: uuid.UUID
    warehouse_code: str
    attempt_no: int
    pick_code: str
    status: PickStatus
    status_note: str
    created_by_name: str | None
    picked_by_name: str | None
    verified_by_name: str | None
    submitted_at: datetime | None
    verified_at: datetime | None
    created_at: datetime
    version: int
    lines: list[PickLineOut]


# ---------------------------------------------------------------- Module 05 invoicing

InvoiceState = Literal["DRAFT", "ISSUED", "PARTIALLY_PAID", "PAID", "VOIDED", "CREDITED"]
EmailStatus = Literal["NO_EMAIL", "QUEUED", "SENT", "FAILED"]


class WeightRowIn(BaseModel):
    """`cases` boxes each weighing `weight_lb` on the scale (catch weight, D-08)."""

    cases: int = Field(ge=1, le=100000)
    weight_lb: Qty


class InvoicePreviewIn(BaseModel):
    # pick line id -> weight rows. Only BOX lines may be billed by weight.
    weights: dict[uuid.UUID, list[WeightRowIn]] = Field(default_factory=dict)


class InvoiceHeaderIn(BaseModel):
    """Printed in the invoice's Salesperson / Customer PO No. strip (D-12). A blank
    salesperson uses the company default (e.g. "Andy Narine")."""

    salesperson: str = Field(default="", max_length=100)
    customer_po: str = Field(default="", max_length=64)


class InvoiceIssueIn(InvoicePreviewIn, InvoiceHeaderIn):
    version: int = Field(ge=1)  # picking attempt version
    dispatch_confirmed: bool  # D-01 gate: goods physically handed over / loaded
    note: str = Field(default="", max_length=500)


class InvoiceSettingsOut(BaseModel):
    """Letterhead and defaults the Create invoice form starts from."""

    legal_name: str
    address: str
    phone: str
    fax: str
    email: str
    business_no: str
    default_salesperson: str
    invoice_prefix: str


class InvoiceDraftLineOut(BaseModel):
    pick_line_id: uuid.UUID
    order_line_id: uuid.UUID
    product_id: uuid.UUID
    sku: str
    description: str
    unit_code: UnitCode
    cases: Decimal | None
    case_weight_lb: Decimal | None
    weight_source: Literal["ENTERED"] | None
    billed_qty: Decimal
    unit_rate: Decimal
    line_amount: Decimal
    tax_code: str
    tax_rate: Decimal
    tax_amount: Decimal
    price_version_code: str | None
    price_scope: str | None
    # lb per unit printed in the Units / LBS / KG columns (D-12); None before that template.
    pack_lb: Decimal | None = None


class StockCheckOut(BaseModel):
    pick_line_id: uuid.UUID
    sku: str
    unit_code: UnitCode
    deduct_qty: Decimal
    available_qty: Decimal
    ok: bool
    lots: list[str] = Field(default_factory=list, description="Batches issuing takes (FEFO)")


class InvoicePreviewOut(BaseModel):
    pick_id: uuid.UUID
    pick_code: str
    pick_version: int
    order_id: uuid.UUID
    order_no: str
    customer_name: str
    email_to: str | None
    payment_terms_days: int
    currency: Literal["CAD"]
    lines: list[InvoiceDraftLineOut]
    stock: list[StockCheckOut]
    weighable_pick_line_ids: list[uuid.UUID]
    subtotal: Decimal
    tax_amount: Decimal
    total: Decimal
    tax_note: str
    can_issue: bool


class InvoiceLineOut(InvoiceDraftLineOut):
    id: uuid.UUID
    line_no: int


class InvoiceMovementOut(BaseModel):
    id: uuid.UUID
    lot_id: uuid.UUID
    lot_code: str
    sku: str
    unit_code: UnitCode
    delta_qty: Decimal
    qty_after: Decimal


class InvoiceSummaryOut(BaseModel):
    id: uuid.UUID
    invoice_no: str
    state: InvoiceState
    customer_id: uuid.UUID
    customer_name: str
    customer_ref: str | None
    sales_order_id: uuid.UUID
    order_no: str
    pick_code: str
    warehouse_code: str
    issued_at: datetime
    due_at: datetime
    total: Decimal
    paid_amount: Decimal
    balance: Decimal
    currency: Literal["CAD"]
    email_status: EmailStatus


class InvoiceOut(InvoiceSummaryOut):
    warehouse_id: uuid.UUID
    picking_attempt_id: uuid.UUID
    subtotal: Decimal
    tax_amount: Decimal
    tax_note: str
    issued_by_name: str | None
    pdf_sha256: str
    template_version: str
    email_to: str | None
    email_sent_at: datetime | None
    email_error: str | None
    email_sends: int
    version: int
    lines: list[InvoiceLineOut]
    movements: list[InvoiceMovementOut]


class InvoicePage(BaseModel):
    items: list[InvoiceSummaryOut]
    total: int
    next_cursor: str | None


# ---------------------------------------------------------------------------------------------
# Module 06 — scanner bridge + document intake
# ---------------------------------------------------------------------------------------------

ScanJobType = Literal[
    "SALES_ORDER",
    "PICK_SHEET",
    "VENDOR_DOCUMENT",
    "PURCHASE_ORDER",
    "DELIVERY_NOTE",
    "PACKING_LIST",
    "RECEIPT",
    "OTHER",
]
ScanChannel = Literal["BRIDGE", "MANUAL"]
ScanJobStatus = Literal["REQUESTED", "SCANNING", "UPLOADED", "FAILED", "CANCELLED", "EXPIRED"]
ScanDeviceStatus = Literal["PENDING", "ACTIVE", "REVOKED"]
ScannerState = Literal["READY", "NOT_FOUND", "DRIVER_MISSING", "BUSY", "ERROR", "UNKNOWN"]
ScanFailCode = Literal[
    "SCANNER_NOT_FOUND", "DRIVER_MISSING", "SCAN_TIMEOUT", "NO_PAGES", "OUTPUT_INVALID", "ERROR"
]
DeviceName = Annotated[str, Field(min_length=2, max_length=80)]


class PairingIn(BaseModel):
    warehouse_id: uuid.UUID
    name: DeviceName


class PairingOut(BaseModel):
    device_id: uuid.UUID
    pairing_code: str
    expires_at: datetime


class DeviceApproveIn(BaseModel):
    version: int
    fingerprint: str = Field(min_length=4, max_length=32)


class DeviceRevokeIn(BaseModel):
    version: int
    reason: Reason


class DetectedScanner(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    driver: str = Field(min_length=1, max_length=16)


class ScanDeviceOut(BaseModel):
    id: uuid.UUID
    warehouse_id: uuid.UUID
    warehouse_code: str
    name: str
    status: ScanDeviceStatus
    paired: bool
    pairing_expires_at: datetime | None
    online: bool
    last_seen_at: datetime | None
    scanner_state: ScannerState
    scanner_detail: str | None
    bridge_version: str | None
    discover_requested_at: datetime | None = None
    detected_scanners: list[DetectedScanner] | None = None
    detected_at: datetime | None = None
    approved_by_name: str | None
    approved_at: datetime | None
    revoked_reason: str | None
    version: int


class ScanJobIn(BaseModel):
    warehouse_id: uuid.UUID
    job_type: ScanJobType
    device_id: uuid.UUID
    note: str | None = Field(default=None, max_length=500)


class ScanUploadIn(BaseModel):
    """Upload parameters (query string); the file itself is the raw request body."""

    warehouse_id: uuid.UUID
    job_type: ScanJobType
    filename: str
    note: str | None = None
    sha256: str


class ScanJobOut(BaseModel):
    id: uuid.UUID
    job_no: str
    warehouse_id: uuid.UUID
    warehouse_code: str
    job_type: ScanJobType
    channel: ScanChannel
    status: ScanJobStatus
    device_id: uuid.UUID | None
    device_name: str | None
    note: str | None
    requested_by_name: str
    created_at: datetime
    claimed_at: datetime | None
    completed_at: datetime | None
    error_code: str | None
    error_detail: str | None
    document_id: uuid.UUID | None
    document_sha256: str | None
    document_mime: str | None
    document_size: int | None
    document_filename: str | None
    duplicate: bool
    page_count: int | None = None  # single-scan answers only (lists leave it empty)
    version: int


class ScanJobCreatedOut(ScanJobOut):
    """Returned once to the browser that requested a bridge scan. The capability is valid for
    this job, this device, a few minutes and a single claim."""

    capability: str
    capability_expires_at: datetime


class ScanJobPage(BaseModel):
    items: list[ScanJobOut]
    total: int
    next_cursor: str | None


class ScanCancelIn(BaseModel):
    version: int


# Bridge (device-authenticated) bodies


class BridgePairIn(BaseModel):
    pairing_code: str = Field(min_length=6, max_length=16)
    bridge_version: str = Field(min_length=1, max_length=32)


class BridgePairOut(BaseModel):
    device_id: uuid.UUID
    device_secret: str
    fingerprint: str


class BridgeHeartbeatIn(BaseModel):
    scanner_state: ScannerState
    scanner_detail: str | None = Field(default=None, max_length=300)
    bridge_version: str = Field(min_length=1, max_length=32)
    # Sent only when the server asked (`discover` in the previous answer).
    scanners: list[DetectedScanner] | None = Field(default=None, max_length=20)


class BridgeHeartbeatOut(BaseModel):
    status: ScanDeviceStatus
    server_time: datetime
    discover: bool = False


class BridgeClaimIn(BaseModel):
    capability: str = Field(min_length=20, max_length=2048)


class BridgeClaimOut(BaseModel):
    job_id: uuid.UUID
    job_no: str
    job_type: ScanJobType
    max_bytes: int
    timeout_seconds: int


class BridgeFailIn(BaseModel):
    error_code: ScanFailCode
    detail: str | None = Field(default=None, max_length=300)


# --------------------------------------------------------------------------- Module 07 OCR

OcrStatus = Literal["QUEUED", "NEEDS_REVIEW", "FAILED", "ACCEPTED", "REJECTED", "SUPERSEDED"]
OcrFieldKind = Literal["LINE", "EXTRA", "MANUAL"]
OcrFieldStatus = Literal["OK", "LOW_CONFIDENCE", "UNREADABLE", "UNMAPPED"]
OcrResolution = Literal["NONE", "CONFIRMED", "CORRECTED", "REMOVED"]


class OcrQueueIn(BaseModel):
    scan_job_id: uuid.UUID


class OcrCorrectionOut(BaseModel):
    id: uuid.UUID
    action: str
    before: dict[str, Any]
    after: dict[str, Any]
    reason: str | None
    actor_name: str
    created_at: datetime


class OcrFieldOut(BaseModel):
    id: uuid.UUID
    seq: int
    kind: OcrFieldKind
    label: str
    raw_text: str
    read_product_id: uuid.UUID | None
    read_sku: str | None
    read_unit_code: str | None
    read_qty: str | None
    read_unit_price: str | None = None
    read_amount: str | None = None
    confidence: str | None
    status: OcrFieldStatus
    bbox: dict[str, Any] | None
    # Kept apart from the quantity (handwriting reader): package, weight, case size, notes,
    # crossed-out number, NO, checkmark, supplied / unavailable, and why the line needs a look.
    detail: dict[str, Any] | None = None
    resolution: OcrResolution
    final_product_id: uuid.UUID | None
    final_sku: str | None
    final_product_name: str | None
    final_unit_code: str | None
    final_qty: str | None
    resolved_by_name: str | None
    resolved_at: datetime | None
    corrections: list[OcrCorrectionOut]


class OcrExtractionOut(BaseModel):
    id: uuid.UUID
    scan_job_id: uuid.UUID
    scan_job_no: str
    document_id: uuid.UUID
    document_mime: str
    warehouse_id: uuid.UUID
    warehouse_code: str
    run_no: int
    status: OcrStatus
    provider: str
    template: str | None
    customer_id: uuid.UUID | None
    customer_name: str | None
    warnings: list[str]
    header: dict[str, Any] = Field(default_factory=dict)
    vendor_id: str | None = None
    shipment_id: str | None = None
    # Which engine and model really read it, requests, tokens, seconds, any fallback (no page
    # content; the full record is on the admin OCR test page).
    engine: dict[str, Any] = Field(default_factory=dict)
    page_count: int | None
    error_code: str | None
    error_detail: str | None
    sales_order_id: uuid.UUID | None
    sales_order_no: str | None
    requested_by_name: str | None
    reviewed_by_name: str | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime
    completed_at: datetime | None
    field_count: int
    open_count: int
    version: int


class OcrExtractionDetail(OcrExtractionOut):
    fields: list[OcrFieldOut]
    text_blocks: list[dict[str, Any]]


class OcrExtractionPage(BaseModel):
    items: list[OcrExtractionOut]
    total: int
    next_cursor: str | None


class OcrResolveIn(BaseModel):
    """CONFIRM keeps what was read (only for OK reads). CORRECT replaces product/unit/qty (no
    reason needed). REMOVE drops the line; removing a clear reading needs a reason."""

    version: int = Field(ge=1)
    action: Literal["CONFIRM", "CORRECT", "REMOVE"]
    product_id: uuid.UUID | None = None
    unit_code: UnitCode | None = None
    qty: Qty | None = None
    reason: str | None = Field(default=None, max_length=500)


class OcrConfirmLinesIn(BaseModel):
    """Confirm several clearly read lines at once (the ones the reviewer ticked)."""

    version: int = Field(ge=1)
    field_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class OcrManualLineIn(BaseModel):
    version: int = Field(ge=1)
    product_id: uuid.UUID
    unit_code: UnitCode
    qty: Qty
    reason: Reason


class OcrAcceptIn(BaseModel):
    version: int = Field(ge=1)
    customer_id: uuid.UUID
    requested_for: datetime | None = None
    note: str = Field(default="", max_length=1000)
    reviewed_all: Literal[True]


class OcrEngineCallOut(BaseModel):
    at: datetime
    model: str
    stage: str
    page: int | None
    outcome: str
    http_status: int | None
    total_tokens: int
    seconds: str | None


class OcrEngineAccuracyOut(BaseModel):
    engine: str
    samples: int
    correct: int
    accuracy: str | None


class OcrEngineStatusOut(BaseModel):
    """The OCR engine as configured on the server. Never contains the key."""

    provider: str
    fallback_provider: str
    model: str
    key_set: bool
    data_consent: bool
    target_seconds: int
    requests_per_minute: int
    daily_request_cap: int
    used_today: int
    remaining_today: int
    used_this_minute: int
    breaker_open_seconds: int
    quota_hit_today: bool
    recent_calls: list[OcrEngineCallOut]
    accuracy: list[OcrEngineAccuracyOut]


class OcrEngineReadingOut(BaseModel):
    """One reading for the admin OCR test page: the engine record with each page's validated
    answer, the local (Tesseract) reading of the same pages, and how the review went."""

    extraction_id: uuid.UUID
    status: OcrStatus
    provider: str
    page_count: int | None
    warnings: list[str]
    engine: dict[str, Any]
    lines: int
    uncertain: int
    no_marks: int
    crossed_out: int
    corrections: int
    validation_errors: list[str]
    tesseract_lines: list[dict[str, Any]]


class OcrAccuracyOut(BaseModel):
    samples: int
    correct: int
    accuracy: str | None
    target: str
    minimum_samples: int
    meets_target: bool
    by_status: dict[str, int]


# ---------------------------------------------------------------- Module 08 receiving

Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Money4 = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=4)]
ShipmentStatus = Literal["OPEN", "COUNTED", "POSTED", "CANCELLED"]
ShipmentDocKind = Literal["AWB", "INVOICE", "PACKING_LIST", "COMBINED", "OTHER"]
LandedCostKind = Literal["FREIGHT", "DUTY", "BROKERAGE", "INSURANCE", "OTHER"]
CompareStatus = Literal[
    "MATCH", "SHORT", "OVER", "DAMAGED", "NOT_DECLARED", "NOT_RECEIVED", "DOC_CONFLICT"
]


class VendorIn(BaseModel):
    code: str = Field(min_length=1, max_length=32, pattern=r"^[A-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=200)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    email: str | None = Field(default=None, max_length=320)
    currency: Currency = "USD"


class VendorPatch(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    email: str | None = Field(default=None, max_length=320)
    currency: Currency
    active: bool
    version: int = Field(ge=1)


class VendorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    code: str
    name: str
    country_code: str | None
    email: str | None
    currency: str
    active: bool
    version: int


class ShipmentIn(BaseModel):
    warehouse_id: uuid.UUID
    vendor_id: uuid.UUID
    vendor_invoice_no: str | None = Field(default=None, max_length=64)
    awb_no: str | None = Field(default=None, max_length=64)
    currency: Currency | None = None
    expected_on: date | None = None
    note: str | None = Field(default=None, max_length=1000)


class ShipmentDocIn(BaseModel):
    version: int = Field(ge=1)
    scan_job_id: uuid.UUID
    kind: ShipmentDocKind
    doc_ref: str | None = Field(default=None, max_length=64)


class ShipmentDocUploadIn(BaseModel):
    """Audit/idempotency fingerprint of a direct upload (query params + content hash)."""

    shipment_id: uuid.UUID
    kind: ShipmentDocKind
    doc_ref: str | None
    filename: str
    sha256: str


class DeclaredLineIn(BaseModel):
    version: int = Field(ge=1)
    shipment_document_id: uuid.UUID | None = None
    raw_label: str = Field(min_length=1, max_length=200)
    product_id: uuid.UUID | None = None
    unit_code: UnitCode
    declared_qty: Qty
    unit_cost: Money4 | None = None
    remember_alias: bool = False


class GrnLineIn(BaseModel):
    version: int = Field(ge=1)
    product_id: uuid.UUID
    unit_code: UnitCode
    accepted_qty: QtyNonNeg
    damaged_qty: QtyNonNeg = Decimal(0)
    rejected_qty: QtyNonNeg = Decimal(0)
    batch_ref: str | None = Field(default=None, max_length=64)
    expires_at: datetime | None = None
    note: str | None = Field(default=None, max_length=500)


class GrnLineEditIn(GrnLineIn):
    line_version: int = Field(ge=1)


class LandedCostIn(BaseModel):
    version: int = Field(ge=1)
    kind: LandedCostKind
    amount: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]
    currency: Currency
    shipment_document_id: uuid.UUID | None = None
    note: str | None = Field(default=None, max_length=500)


class GrnPostIn(BaseModel):
    version: int = Field(ge=1)
    acknowledge_discrepancies: bool = False
    discrepancy_note: str | None = Field(default=None, max_length=1000)


class ShipmentDocOut(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    scan_job_id: uuid.UUID | None
    scan_job_no: str | None
    kind: ShipmentDocKind
    doc_ref: str | None
    filename: str
    mime_type: str
    byte_size: int
    sha256: str
    added_by_name: str
    created_at: datetime


class DeclaredLineOut(BaseModel):
    id: uuid.UUID
    shipment_document_id: uuid.UUID | None
    raw_label: str
    product_id: uuid.UUID
    sku: str
    product_name: str
    unit_code: UnitCode
    declared_qty: Decimal
    unit_cost: Decimal | None
    created_by_name: str
    created_at: datetime
    removed_at: datetime | None
    removed_reason: str | None


class GrnLineOut(BaseModel):
    id: uuid.UUID
    seq: int
    product_id: uuid.UUID
    sku: str
    product_name: str
    unit_code: UnitCode
    accepted_qty: Decimal
    damaged_qty: Decimal
    rejected_qty: Decimal
    batch_ref: str | None
    expires_at: datetime | None
    note: str | None
    unit_cost: Decimal | None
    lot_id: uuid.UUID | None
    lot_code: str | None
    counted_by_name: str
    version: int


class LandedCostOut(BaseModel):
    id: uuid.UUID
    kind: LandedCostKind
    amount: Decimal
    currency: str
    shipment_document_id: uuid.UUID | None
    note: str | None
    created_by_name: str
    created_at: datetime
    removed_at: datetime | None
    removed_reason: str | None


class CompareRow(BaseModel):
    product_id: uuid.UUID
    sku: str
    product_name: str
    unit_code: UnitCode
    declared_qty: Decimal | None
    declared_by_document: dict[str, Decimal]
    accepted_qty: Decimal
    damaged_qty: Decimal
    rejected_qty: Decimal
    difference: Decimal | None
    status: CompareStatus


class ShipmentOut(BaseModel):
    id: uuid.UUID
    shipment_no: str
    status: ShipmentStatus
    warehouse_id: uuid.UUID
    warehouse_code: str
    vendor_id: uuid.UUID
    vendor_code: str
    vendor_name: str
    vendor_invoice_no: str | None
    awb_no: str | None
    currency: str
    expected_on: date | None
    note: str | None
    document_count: int
    discrepancy_count: int
    grn_no: str | None
    created_by_name: str
    counted_by_name: str | None
    counted_at: datetime | None
    posted_by_name: str | None
    posted_at: datetime | None
    discrepancy_note: str | None
    cancel_reason: str | None
    version: int
    created_at: datetime


class ShipmentDetail(ShipmentOut):
    documents: list[ShipmentDocOut]
    declared: list[DeclaredLineOut]
    counted: list[GrnLineOut]
    costs: list[LandedCostOut]
    comparison: list[CompareRow]
    cost_totals: dict[str, Decimal]


class ShipmentPage(BaseModel):
    items: list[ShipmentOut]
    total: int
    next_cursor: str | None


# ---------------------------------------------------------------------------------------------
# Module 09 — document archive + Drive sync
# ---------------------------------------------------------------------------------------------

DocFileKind = Literal["INVOICE_PDF", "POD", "VENDOR_DOC", "SCAN"]
DocSyncStatus = Literal["PENDING", "SYNCED", "FAILED", "LOCAL_ONLY"]


class DocumentFileOut(BaseModel):
    id: uuid.UUID
    warehouse_id: uuid.UUID
    kind: DocFileKind
    folder: str
    filename: str
    path: str
    mime_type: str
    sha256: str
    byte_size: int
    sync_status: DocSyncStatus
    drive_file_id: str | None
    sync_attempts: int
    last_error: str | None
    synced_at: str | None
    note: str | None
    invoice_id: uuid.UUID | None
    shipment_id: uuid.UUID | None
    scan_job_id: uuid.UUID | None
    invoice_no: str | None
    shipment_no: str | None
    scan_job_no: str | None
    customer_name: str | None
    vendor_name: str | None
    created_at: str
    version: int


class DocumentPage(BaseModel):
    items: list[DocumentFileOut]
    total: int
    next_cursor: str | None
    counts: dict[str, int]
    drive_provider: str


class DocumentRetryIn(BaseModel):
    version: int = Field(ge=1)


class PodUploadIn(BaseModel):
    """Audit/idempotency fingerprint of a POD upload (query params + content hash)."""

    invoice_id: uuid.UUID
    filename: str
    note: str | None
    sha256: str


# ---------------------------------------------------------------------------------------------
# Module 10 — manual payments, receivables aging (no bank integration)
# ---------------------------------------------------------------------------------------------

PaymentMethod = Literal["INTERAC", "EFT", "CHEQUE", "CASH"]
PaymentStatus = Literal["REPORTED", "VERIFIED", "APPLIED", "REJECTED"]
PayAmount = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
OptText = Annotated[str, Field(max_length=500)] | None


class PaymentRecordIn(BaseModel):
    customer_id: uuid.UUID
    method: PaymentMethod
    amount: PayAmount
    received_on: date
    payer_ref: Annotated[str, Field(max_length=120)] | None = None
    invoice_id: uuid.UUID | None = None
    note: OptText = None


class AllocationIn(BaseModel):
    invoice_id: uuid.UUID
    amount: PayAmount


class PaymentVerifyIn(BaseModel):
    version: int = Field(ge=1)
    verification_ref: str = Field(min_length=3, max_length=120)
    verification_note: OptText = None
    allocations: list[AllocationIn] = Field(default_factory=list, max_length=50)


class PaymentAllocateIn(BaseModel):
    version: int = Field(ge=1)
    allocations: list[AllocationIn] = Field(min_length=1, max_length=50)


class PaymentRejectIn(BaseModel):
    version: int = Field(ge=1)
    reason: Reason


class AllocationReverseIn(BaseModel):
    version: int = Field(ge=1)
    reason: Reason


class PaymentSummaryOut(BaseModel):
    id: uuid.UUID
    payment_no: str
    status: PaymentStatus
    customer_id: uuid.UUID
    customer_name: str
    customer_ref: str | None
    method: PaymentMethod
    amount: Decimal
    allocated_amount: Decimal
    unallocated_amount: Decimal
    currency: Literal["CAD"]
    received_on: date
    payer_ref: str | None
    reported_invoice_id: uuid.UUID | None
    reported_invoice_no: str | None
    note: str | None
    recorded_by_name: str | None
    recorded_at: datetime
    verification_ref: str | None
    verification_note: str | None
    verified_by_name: str | None
    verified_at: datetime | None
    rejected_reason: str | None
    rejected_by_name: str | None
    rejected_at: datetime | None
    version: int


class AllocationOut(BaseModel):
    id: uuid.UUID
    invoice_id: uuid.UUID
    invoice_no: str
    invoice_state: InvoiceState
    invoice_balance: Decimal
    amount: Decimal
    reverses_id: uuid.UUID | None
    reversed: bool
    reason: str | None
    created_by_name: str | None
    created_at: datetime


class PaymentOut(PaymentSummaryOut):
    allocations: list[AllocationOut]


class PaymentPage(BaseModel):
    items: list[PaymentSummaryOut]
    total: int
    next_cursor: str | None
    counts: dict[str, int]


class AgingCustomerOut(BaseModel):
    customer_id: uuid.UUID
    customer_name: str
    customer_ref: str | None
    email: str | None
    current: Decimal
    d1_30: Decimal
    d31_60: Decimal
    d61_90: Decimal
    d90_plus: Decimal
    total: Decimal
    open_invoices: int
    oldest_due: date | None
    unapplied_credit: Decimal
    reported_pending: Decimal


class ReceivablesOut(BaseModel):
    as_of: date
    currency: Literal["CAD"]
    current: Decimal
    d1_30: Decimal
    d31_60: Decimal
    d61_90: Decimal
    d90_plus: Decimal
    total: Decimal
    unapplied_credit: Decimal
    reported_pending: Decimal
    customers: list[AgingCustomerOut]
    payment_instructions: str
    reminder_offsets: list[int]


# ---------------------------------------------------------------- Module 12: reports
# Read-only aggregates. Money is CAD before tax unless named `tax`/`gross`. Quantities are
# always per unit: boxes, pieces, lb and kg are never added together. No cost/margin fields
# until D-04 (costing) is decided.

UnitCodeLit = Literal["BOX", "PIECE", "LB", "KG"]


class UnitQtyOut(BaseModel):
    unit_code: UnitCodeLit
    qty: Decimal


class ReportPeriodOut(BaseModel):
    date_from: date
    date_to: date
    timezone: str
    currency: Literal["CAD"]


class SalesPointOut(BaseModel):
    period: date
    invoice_count: int
    net_sales: Decimal


class SalesReportOut(ReportPeriodOut):
    bucket: Literal["day", "month"]
    invoice_count: int
    customer_count: int
    net_sales: Decimal
    tax: Decimal
    gross: Decimal
    quantities: list[UnitQtyOut]
    series: list[SalesPointOut]


class ProductSalesRowOut(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    unit_code: UnitCodeLit
    qty: Decimal
    net_sales: Decimal
    avg_rate: Decimal
    invoice_count: int
    customer_count: int


class ProductSalesOut(ReportPeriodOut):
    total_rows: int
    net_sales: Decimal
    rows: list[ProductSalesRowOut]


class CustomerSalesRowOut(BaseModel):
    customer_id: uuid.UUID
    name: str
    external_ref: str | None
    invoice_count: int
    net_sales: Decimal
    gross: Decimal
    quantities: list[UnitQtyOut]
    last_invoice_at: datetime


class CustomerSalesOut(ReportPeriodOut):
    total_rows: int
    net_sales: Decimal
    rows: list[CustomerSalesRowOut]


class StockFlowRowOut(BaseModel):
    product_id: uuid.UUID
    sku: str
    name: str
    unit_code: UnitCodeLit
    opening: Decimal
    received: Decimal
    dispatched: Decimal
    other: Decimal
    closing: Decimal


class StockFlowOut(ReportPeriodOut):
    rows: list[StockFlowRowOut]


class VendorReceivingRowOut(BaseModel):
    vendor_id: uuid.UUID
    code: str
    name: str
    country_code: str | None
    unit_code: UnitCodeLit
    shipments: int
    declared: Decimal
    accepted: Decimal
    damaged: Decimal
    rejected: Decimal


class ReceivingReportOut(ReportPeriodOut):
    shipments: int
    rows: list[VendorReceivingRowOut]


# ---------------------------------------------------------------- Settings: users / my account
RoleCode = Literal["OWNER_ADMIN", "MANAGER", "SALES_OPERATOR", "WAREHOUSE", "FINANCE", "VIEWER"]
# Passwords: 12-128 characters. Never logged, audited or kept in idempotency records.
Password = Annotated[str, Field(min_length=12, max_length=128)]
EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


StaffStatus = Literal["ACTIVE", "INVITED", "SUSPENDED"]


class StaffOut(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str
    active: bool
    status: StaffStatus  # INVITED = has not set a password yet; SUSPENDED = cannot sign in
    roles: list[str]
    mfa_enrolled: bool
    locked: bool
    last_seen_at: datetime | None
    last_login_at: datetime | None
    invite_expires_at: datetime | None
    created_at: datetime


class StaffCreateIn(BaseModel):
    """Invitation-based: the person sets their own password from the emailed link."""

    email: str = Field(max_length=320, pattern=EMAIL_RE)
    display_name: str = Field(min_length=1, max_length=200)
    role_code: RoleCode


class StaffPatchIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=200)
    role_code: RoleCode | None = None
    active: bool | None = None


class PasswordSetIn(BaseModel):
    password: Password


class MyAccountIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)


class PasswordChangeIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: Password


# ---------------------------------------------------------------- auth: links, sessions, security
TokenPurpose = Literal["INVITE", "PASSWORD_RESET"]
RawToken = Annotated[str, Field(min_length=20, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


class ForgotPasswordIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class MessageOut(BaseModel):
    message: str


class TokenInspectIn(BaseModel):
    token: RawToken
    purpose: TokenPurpose


class TokenInspectOut(BaseModel):
    email: str
    display_name: str
    purpose: TokenPurpose
    expires_at: datetime


class TokenPasswordIn(BaseModel):
    token: RawToken
    password: Password


class SessionInfoOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    user_agent: str | None
    ip_address: str | None
    current: bool
    employee_id: uuid.UUID | None = None
    employee_name: str | None = None
    employee_email: str | None = None


class SecurityEventOut(BaseModel):
    id: uuid.UUID
    action: str
    success: bool
    created_at: datetime
    actor_id: uuid.UUID | None
    actor_name: str | None
    target_type: str | None
    target_id: str | None
    target_name: str | None
    reason: str | None
    ip_address: str | None
    user_agent: str | None


class RoleOut(BaseModel):
    code: str
    name: str
    description: str
    permissions: list[str]
    members: int


class PermissionOut(BaseModel):
    code: str
    description: str


class RoleMatrixOut(BaseModel):
    roles: list[RoleOut]
    permissions: list[PermissionOut]


class SecuritySettingsOut(BaseModel):
    two_step_required: bool
    session_ttl_minutes: int
    session_idle_minutes: int
    login_max_failures: int
    login_lockout_minutes: int
    login_ip_max_failures: int
    login_ip_window_minutes: int
    invite_ttl_hours: int
    password_reset_ttl_minutes: int
    password_min_length: int


# ---------------------------------------------------------------- suppliers: ledger + payments
SupplierPayMethod = Literal["WIRE", "EFT", "CHEQUE", "CASH", "OTHER"]
CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]


class SupplierPaymentIn(BaseModel):
    vendor_id: uuid.UUID
    shipment_id: uuid.UUID | None = None
    method: SupplierPayMethod
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    currency: CurrencyCode | None = Field(default=None, description="Default: the supplier's")
    paid_on: date
    reference: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=500)


class SupplierPaymentVoidIn(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class SupplierPaymentOut(BaseModel):
    id: uuid.UUID
    payment_no: str
    vendor_id: uuid.UUID
    vendor_name: str
    shipment_id: uuid.UUID | None
    shipment_no: str | None
    method: SupplierPayMethod
    amount: Decimal
    currency: str
    paid_on: date
    reference: str | None
    note: str | None
    recorded_by_name: str | None
    created_at: datetime
    voided_at: datetime | None
    voided_by_name: str | None
    void_reason: str | None


class CurrencyTotalOut(BaseModel):
    currency: str
    billed: Decimal
    paid: Decimal
    balance: Decimal


class SupplierBalanceOut(BaseModel):
    vendor_id: uuid.UUID
    code: str
    name: str
    country_code: str | None
    currency: str
    active: bool
    shipments: int
    last_received_at: datetime | None
    last_paid_on: date | None
    totals: list[CurrencyTotalOut]


class LedgerUnitOut(BaseModel):
    unit_code: Literal["BOX", "PIECE", "LB", "KG"]
    declared: Decimal
    accepted: Decimal
    damaged: Decimal
    rejected: Decimal


class LedgerCostOut(BaseModel):
    kind: str
    amount: Decimal
    currency: str


class LedgerShipmentOut(BaseModel):
    id: uuid.UUID
    shipment_no: str
    grn_no: str | None
    vendor_invoice_no: str | None
    posted_at: datetime
    currency: str
    billed: Decimal
    unpriced_lines: int
    units: list[LedgerUnitOut]
    landed_costs: list[LedgerCostOut]


class LedgerEntryOut(BaseModel):
    on: date
    kind: Literal["SHIPMENT", "PAYMENT"]
    ref: str
    id: uuid.UUID
    currency: str
    billed: Decimal
    paid: Decimal
    balance: Decimal
    voided: bool


class SupplierLedgerOut(BaseModel):
    vendor_id: uuid.UUID
    code: str
    name: str
    country_code: str | None
    email: str | None
    currency: str
    active: bool
    totals: list[CurrencyTotalOut]
    returned: list[LedgerUnitOut]
    shipments: list[LedgerShipmentOut]
    payments: list[SupplierPaymentOut]
    entries: list[LedgerEntryOut]


# ---------------------------------------------------------------- direct invoices (no pick sheet)
class DirectInvoiceLineIn(BaseModel):
    product_id: uuid.UUID
    unit_code: UnitCode
    qty: Qty
    note: str = Field(default="", max_length=500)
    # Pounds the whole line weighs (boxes only): priced per lb x this weight.
    weight_lb: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=4)] | None = None
    lot_id: uuid.UUID | None = Field(
        default=None, description="Batch handed over; none = oldest expiry first (FEFO)"
    )


class DirectInvoiceIn(InvoiceHeaderIn):
    """A counter / small sale billed without a picking sheet (owner direction 2026-11-08).
    Prices resolve now; stock leaves first-expiry-first-out; one transaction."""

    customer_id: uuid.UUID
    warehouse_id: uuid.UUID
    lines: list[DirectInvoiceLineIn] = Field(min_length=1, max_length=100)
    note: str = Field(default="", max_length=500)


class DirectInvoiceIssueIn(DirectInvoiceIn):
    dispatch_confirmed: bool  # D-01 gate: goods handed over / loaded


class DirectStockOut(BaseModel):
    sku: str
    unit_code: str
    deduct_qty: Decimal
    available_qty: Decimal
    ok: bool
    lots: list[str] = Field(default_factory=list, description="Batches issuing takes (FEFO)")


class DirectPreviewOut(BaseModel):
    lines: list[dict[str, Any]]
    subtotal: Decimal
    tax_amount: Decimal
    total: Decimal
    stock: list[DirectStockOut]
    stock_ok: bool


# ---------------------------------------------------------------- line preview (fast entry grid)
class LinePreviewLineIn(BaseModel):
    product_id: uuid.UUID
    unit_code: UnitCode
    qty: Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4)] = Decimal(0)
    lot_id: uuid.UUID | None = None
    weight_lb: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=4)] | None = None


class LinePreviewIn(BaseModel):
    """Rows being typed on Create invoice › Direct or New sales order. Nothing is written."""

    customer_id: uuid.UUID | None = None
    warehouse_id: uuid.UUID
    lines: list[LinePreviewLineIn] = Field(min_length=1, max_length=200)


class LotOptionOut(BaseModel):
    lot_id: uuid.UUID
    lot_code: str
    batch_ref: str | None
    expires_at: datetime | None
    available_qty: Decimal


class LineProblemOut(BaseModel):
    code: str
    message: str


class LinePreviewLineOut(BaseModel):
    line: int
    product_id: uuid.UUID
    sku: str
    unit_code: UnitCode
    qty: Decimal
    rate: Decimal | None
    line_amount: Decimal | None
    basis: str | None
    price_scope: str | None
    price_version_code: str | None
    pack_lb: Decimal | None
    weight_lb: Decimal | None
    lb_rate: Decimal | None = None  # per-lb price a box rate was worked out from
    available_qty: Decimal
    stock_ok: bool
    lots: list[LotOptionOut]
    problem: LineProblemOut | None


class LinePreviewOut(BaseModel):
    lines: list[LinePreviewLineOut]
    subtotal: Decimal
    tax_code: str
    tax_amount: Decimal
    total: Decimal
    complete: bool


class OcrToReceivingIn(BaseModel):
    """Approve a reviewed supplier document into Receiving (declared lines only)."""

    version: int = Field(ge=1)
    vendor_id: uuid.UUID
    shipment_id: uuid.UUID | None = Field(default=None, description="An OPEN shipment; else new")
    document_no: str | None = Field(default=None, max_length=64)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    reviewed_all: Literal[True]


class OcrHeaderFixIn(BaseModel):
    version: int = Field(ge=1)
    field: Literal[
        "supplier",
        "document_no",
        "document_date",
        "due_date",
        "currency",
        "subtotal",
        "tax",
        "total",
        "awb_no",
        "carrier",
        "arrival_date",
    ]
    value: str | None = Field(default=None, max_length=120)
    reason: str = Field(default="", max_length=300)
