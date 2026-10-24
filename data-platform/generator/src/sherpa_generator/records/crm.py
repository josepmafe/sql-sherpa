from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

from pydantic import AwareDatetime

from sherpa_generator.records.base import RawRecord


class LeadSource(StrEnum):
    NEWSLETTER = "newsletter"
    POPUP = "popup"
    GIVEAWAY = "giveaway"
    REFERRAL = "referral"


class LifecycleStage(StrEnum):
    SUBSCRIBER = "subscriber"
    LEAD = "lead"
    MQL = "mql"
    CUSTOMER = "customer"


class Lead(RawRecord):
    TABLE: ClassVar[str] = "raw_crm.leads"

    id: int
    email: str
    source: LeadSource
    utm_campaign: str | None = None
    lifecycle_stage: LifecycleStage
    created_at: AwareDatetime
    updated_at: AwareDatetime


class EmailSend(RawRecord):
    TABLE: ClassVar[str] = "raw_crm.email_sends"

    id: int
    lead_id: int
    campaign_id: int | None = None
    sent_at: AwareDatetime
    opened_at: AwareDatetime | None = None
    clicked_at: AwareDatetime | None = None
