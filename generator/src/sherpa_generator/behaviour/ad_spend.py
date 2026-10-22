from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING, Final

from sherpa_generator.records import AdSpendDay, CampaignChannel
from sherpa_generator.records.base import quantize_money
from sherpa_generator.simulation.deterministic_random import uniform_decimal

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sherpa_generator.records import Campaign
    from sherpa_generator.simulation.day_changes import DayChanges
    from sherpa_generator.simulation.deterministic_random import DeterministicRandom
    from sherpa_generator.simulation.realism_rules import RealismRules
    from sherpa_generator.simulation.store_snapshot import StoreSnapshot

STREAM: Final = "ad_spend"
UNPAID_CHANNELS: Final = (CampaignChannel.EMAIL,)  # Spec A §4.3: ad spend covers paid channels only
DAILY_SPEND_FACTOR: Final = (0.7, 1.3)  # spend = budget / campaign days x U(0.7, 1.3)
IMPRESSIONS_PER_EUR: Final = (80, 150)  # impressions = round(spend x U(80, 150))


class AdSpendSimulator:
    """Spec A §5.2 step 8: one row per active paid campaign, with ad-platform clicks per R11."""

    def __init__(self, random: DeterministicRandom, rules: RealismRules) -> None:
        self._random = random
        self._rules = rules

    def simulate_day(
        self, snapshot: StoreSnapshot, changes: DayChanges, paid_sessions_by_campaign: Mapping[int, int]
    ) -> None:
        rng = self._random.for_day(snapshot.day, STREAM)
        campaigns = sorted(
            (
                campaign
                for channel, active in snapshot.active_campaigns.items()
                if channel not in UNPAID_CHANNELS
                for campaign in active
            ),
            key=lambda campaign: campaign.id,
        )
        for campaign in campaigns:
            spend = quantize_money(self._daily_budget(campaign) * uniform_decimal(rng, *DAILY_SPEND_FACTOR))
            impressions = spend * uniform_decimal(rng, *IMPRESSIONS_PER_EUR)
            changes.insert(
                AdSpendDay(
                    spend_date=snapshot.day,
                    campaign_id=campaign.id,
                    spend=spend,
                    impressions=int(impressions.to_integral_value(ROUND_HALF_UP)),
                    clicks=self._rules.ad_clicks(paid_sessions_by_campaign.get(campaign.id, 0), rng),
                )
            )

    @staticmethod
    def _daily_budget(campaign: Campaign) -> Decimal:
        return campaign.budget / Decimal((campaign.ends_at - campaign.starts_at).days)
