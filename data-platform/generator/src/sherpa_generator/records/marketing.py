from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import ClassVar

from pydantic import AwareDatetime

from sherpa_generator.records.base import Money, RawRecord


class CampaignChannel(StrEnum):
    PAID_SEARCH = "paid_search"
    PAID_SOCIAL = "paid_social"
    EMAIL = "email"
    AFFILIATE = "affiliate"
    DISPLAY = "display"


class Campaign(RawRecord):
    TABLE: ClassVar[str] = "raw_marketing.campaigns"

    id: int
    name: str
    utm_campaign: str
    channel: CampaignChannel
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    budget: Money


class AdSpendDay(RawRecord):
    TABLE: ClassVar[str] = "raw_marketing.ad_spend_daily"
    KEY: ClassVar[tuple[str, ...]] = ("spend_date", "campaign_id")

    spend_date: date
    campaign_id: int
    spend: Money
    impressions: int
    clicks: int
