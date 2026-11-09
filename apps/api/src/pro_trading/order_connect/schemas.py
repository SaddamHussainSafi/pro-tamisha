"""Typed request bodies for the staff Order Connect API."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..schemas import CustomerIn, OrderLineIn

FieldKind = Literal["TEXT", "TEXTAREA", "EMAIL", "PHONE", "DATE", "SELECT", "CHECKBOX", "PRODUCTS"]
Status = Literal["NEW", "REVIEWING", "APPROVED", "REJECTED", "DUPLICATE", "SPAM"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FormIn(Strict):
    name: str = Field(min_length=1, max_length=120)
    default_warehouse_id: uuid.UUID | None = None


class FormPatch(Strict):
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    accepting: bool | None = None
    standalone_link: bool | None = None
    default_warehouse_id: uuid.UUID | None = None
    archived: bool | None = None


class FieldIn(Strict):
    key: str = Field(min_length=1, max_length=40)
    kind: FieldKind
    label: str = Field(min_length=1, max_length=120)
    help_text: str = Field(default="", max_length=300)
    required: bool = False
    options: list[str] = Field(default_factory=list, max_length=50)


class ThemeIn(Strict):
    accent: str = Field(default="#1B7A30", max_length=7)
    radius: Literal["none", "small", "large"] = "small"


class DraftIn(Strict):
    revision: int = Field(ge=0)  # 0 = start a new draft from the published version
    title: str = Field(min_length=1, max_length=120)
    intro: str = Field(default="", max_length=1000)
    success_message: str = Field(min_length=1, max_length=500)
    theme: ThemeIn = Field(default_factory=ThemeIn)
    logo_id: uuid.UUID | None = None
    show_prices: bool = False
    product_scope: Literal["ALL", "CATEGORIES", "PRODUCTS"] = "ALL"
    scope_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    fields: list[FieldIn] = Field(min_length=1, max_length=30)


class PublishIn(Strict):
    revision: int = Field(ge=1)


class DomainIn(Strict):
    form_id: uuid.UUID | None = None
    host: str = Field(min_length=3, max_length=300)


class SettingsIn(Strict):
    version: int = Field(ge=0)
    notify_staff: bool = True
    ack_email: bool = False
    min_fill_seconds: int = Field(default=3, ge=0, le=120)
    ip_limit_per_hour: int = Field(default=20, ge=1, le=1000)
    form_limit_per_hour: int = Field(default=300, ge=1, le=10000)
    retention_days: int | None = Field(default=None, ge=30, le=3650)
    consent_required: bool = False
    consent_text: str = Field(default="", max_length=1000)


class StatusIn(Strict):
    version: int = Field(ge=1)
    status: Status
    note: str = Field(default="", max_length=1000)


class NoteIn(Strict):
    note: str = Field(min_length=1, max_length=1000)


class EraseIn(Strict):
    version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)


class ConvertLineIn(OrderLineIn):
    item_line_no: int | None = Field(default=None, ge=1)


class ConvertIn(Strict):
    """Staff-confirmed mapping. The submission's own values stay untouched."""

    version: int = Field(ge=1)
    warehouse_id: uuid.UUID
    customer_id: uuid.UUID | None = None
    new_customer: CustomerIn | None = None
    requested_for: datetime | None = None
    note: str = Field(default="", max_length=1000)
    lines: list[ConvertLineIn] = Field(min_length=1, max_length=200)
