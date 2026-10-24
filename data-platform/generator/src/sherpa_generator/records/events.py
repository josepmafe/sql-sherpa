from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, ClassVar

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict

from sherpa_generator.records.base import Money, RawRecord

# ISO-8601 date-time with an explicit UTC offset (Spec A §4.2: occurred_at is text with the client offset).
OCCURRED_AT_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


class EventType(StrEnum):
    PAGE_VIEW = "page_view"
    PRODUCT_VIEW = "product_view"
    ADD_TO_CART = "add_to_cart"
    REMOVE_FROM_CART = "remove_from_cart"
    BEGIN_CHECKOUT = "begin_checkout"
    PURCHASE = "purchase"
    SIGNUP = "signup"
    LOGIN = "login"


class DeviceType(StrEnum):
    DESKTOP = "desktop"
    MOBILE = "mobile"
    TABLET = "tablet"


class EventContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    device_type: DeviceType
    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    referrer: str | None = None


class EventProperties(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    page_path: str | None = None
    product_id: int | None = None
    variant_id: int | None = None
    order_id: int | None = None
    cart_value: Money | None = None


def _require_offset(value: str) -> str:
    if not OCCURRED_AT_PATTERN.match(value):
        msg = f"occurred_at must be ISO-8601 with a UTC offset, got {value!r}"
        raise ValueError(msg)
    return value


class TrackedEvent(RawRecord):
    TABLE: ClassVar[str] = "raw_events.events"
    KEY: ClassVar[tuple[str, ...]] = ("event_id",)

    event_id: str
    anonymous_id: str
    user_id: str | None = None
    event_type: EventType
    occurred_at: Annotated[str, AfterValidator(_require_offset)]
    received_at: AwareDatetime
    context: EventContext
    properties: EventProperties


class PendingEvent(TrackedEvent):
    """A late event waiting for the simulated day that contains its `received_at`."""

    TABLE: ClassVar[str] = "raw_meta.pending_events"
