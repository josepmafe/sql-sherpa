from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from helpers import SIMULATION_SEED, make_snapshot
from sherpa_generator.behaviour.ad_spend import AdSpendSimulator
from sherpa_generator.records import AdSpendDay, Campaign, CampaignChannel
from sherpa_generator.simulation.day_changes import DayChanges
from sherpa_generator.simulation.deterministic_random import DeterministicRandom
from sherpa_generator.simulation.realism_rules import RealismRules

DAY = date(2025, 3, 12)
CAMPAIGN_START = datetime(2025, 3, 1, tzinfo=UTC)


class TestAdSpendSimulator:
    def test_spend_only_for_active_paid_campaigns(self) -> None:
        search = _campaign(1, CampaignChannel.PAID_SEARCH, budget="3000", days=30)
        social = _campaign(2, CampaignChannel.PAID_SOCIAL, budget="6000", days=30)
        email = _campaign(3, CampaignChannel.EMAIL, budget="9000", days=30)
        snapshot = make_snapshot(
            DAY,
            active_campaigns={
                CampaignChannel.PAID_SOCIAL: [social],
                CampaignChannel.EMAIL: [email],
                CampaignChannel.PAID_SEARCH: [search],
            },
        )
        changes = DayChanges()

        _simulator().simulate_day(snapshot, changes, {1: 40, 2: 20, 3: 15})

        rows = changes.inserted(AdSpendDay)
        assert [(r.spend_date, r.campaign_id) for r in rows] == [(DAY, 1), (DAY, 2)]
        search_row, social_row = rows
        assert Decimal("70.00") <= search_row.spend <= Decimal("130.00")  # 3000 / 30 days x U(0.7, 1.3)
        assert Decimal("140.00") <= social_row.spend <= Decimal("260.00")
        assert all(r.spend == r.spend.quantize(Decimal("0.01")) for r in rows)
        assert all(80 * r.spend - 1 <= r.impressions <= 150 * r.spend + 1 for r in rows)
        assert 34 <= search_row.clicks <= 46  # R11: 40 sessions x U(0.85, 1.15)
        assert 17 <= social_row.clicks <= 23

    def test_zero_paid_sessions_gives_zero_clicks_but_spend(self) -> None:
        affiliate = _campaign(4, CampaignChannel.AFFILIATE, budget="2000", days=10)
        snapshot = make_snapshot(DAY, active_campaigns={CampaignChannel.AFFILIATE: [affiliate]})
        changes = DayChanges()

        _simulator().simulate_day(snapshot, changes, {})

        [row] = changes.inserted(AdSpendDay)
        assert row.clicks == 0
        assert Decimal("140.00") <= row.spend <= Decimal("260.00")
        assert row.impressions > 0

    def test_no_active_campaigns_writes_nothing(self) -> None:
        changes = DayChanges()

        _simulator().simulate_day(make_snapshot(DAY), changes, {})

        assert changes.insert_counts() == {}

    def test_spend_varies_by_day_and_averages_the_daily_budget(self) -> None:
        campaign = _campaign(1, CampaignChannel.PAID_SEARCH, budget="3000", days=30)
        spends: list[Decimal] = []
        for offset in range(30):
            changes = DayChanges()
            snapshot = make_snapshot(
                DAY + timedelta(days=offset), active_campaigns={CampaignChannel.PAID_SEARCH: [campaign]}
            )
            _simulator().simulate_day(snapshot, changes, {1: 10})
            spends.extend(r.spend for r in changes.inserted(AdSpendDay))

        assert len(set(spends)) == len(spends)
        assert Decimal("2700") <= sum(spends, Decimal(0)) <= Decimal("3300")


def _simulator() -> AdSpendSimulator:
    return AdSpendSimulator(DeterministicRandom(SIMULATION_SEED), RealismRules())


def _campaign(campaign_id: int, channel: CampaignChannel, budget: str, days: int) -> Campaign:
    return Campaign(
        id=campaign_id,
        name=f"Campaign {campaign_id}",
        utm_campaign=f"campaign-{campaign_id}",
        channel=channel,
        starts_at=CAMPAIGN_START,
        ends_at=CAMPAIGN_START + timedelta(days=days),
        budget=Decimal(budget),
    )
