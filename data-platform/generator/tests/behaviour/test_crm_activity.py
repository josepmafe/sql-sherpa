from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from helpers import MONTH_END, SIMULATION_SEED, make_customer
from sherpa_generator.behaviour.crm_activity import CrmActivitySimulator
from sherpa_generator.records import Campaign, CampaignChannel, EmailSend, Lead, LeadSource, LifecycleStage
from sherpa_generator.simulation.day_changes import DayChanges
from sherpa_generator.simulation.deterministic_random import DeterministicRandom
from sherpa_generator.simulation.primary_keys import PrimaryKeyAllocator
from sherpa_generator.simulation.realism_rules import RealismRules
from sherpa_generator.simulation.store_snapshot import StoreSnapshot

if TYPE_CHECKING:
    from sherpa_generator.database.connection import Database
    from sherpa_generator.records import Customer

END_OF_RUN = datetime(MONTH_END.year, MONTH_END.month, MONTH_END.day, tzinfo=UTC)
TUESDAY = date(2025, 3, 11)
SATURDAY = date(2025, 3, 15)
STAGE_RANK = {stage: rank for rank, stage in enumerate(LifecycleStage)}
TUESDAY_WEEKDAY = 1


class TestCrmActivitySimulator:
    @pytest.mark.integration
    def test_lead_match_rate_of_customers(self, month: Database) -> None:
        with month.connect() as conn:
            customers = [row[0] for row in conn.execute("SELECT lower(trim(email)) FROM raw_shop.customers")]
            leads = {row[0] for row in conn.execute("SELECT lower(trim(email)) FROM raw_crm.leads")}

        rate = sum(1 for email in customers if email in leads) / len(customers)

        assert 0.25 <= rate <= 0.45

    @pytest.mark.integration
    def test_lifecycle_never_moves_backwards(self, month: Database) -> None:
        with month.connect() as conn:
            leads = conn.execute(
                "SELECT l.id, l.lifecycle_stage, l.created_at, l.updated_at,"
                " bool_or(s.opened_at IS NOT NULL), bool_or(s.clicked_at IS NOT NULL),"
                " min(c.created_at)"
                " FROM raw_crm.leads l"
                " LEFT JOIN raw_crm.email_sends s ON s.lead_id = l.id"
                " LEFT JOIN raw_shop.customers c ON lower(trim(c.email)) = lower(trim(l.email))"
                " GROUP BY l.id, l.lifecycle_stage, l.created_at, l.updated_at"
            ).fetchall()

        converted_by = END_OF_RUN - timedelta(days=2)
        stages = {row[1] for row in leads}
        for _, stage, created_at, updated_at, opened, clicked, signed_up_at in leads:
            evidence = LifecycleStage.SUBSCRIBER
            if opened:
                evidence = LifecycleStage.LEAD
            if clicked:
                evidence = LifecycleStage.MQL
            if signed_up_at is not None and signed_up_at < converted_by:
                evidence = LifecycleStage.CUSTOMER
            assert STAGE_RANK[LifecycleStage(stage)] >= STAGE_RANK[evidence]
            assert stage != LifecycleStage.CUSTOMER or signed_up_at is not None
            assert stage != LifecycleStage.MQL or clicked
            assert stage != LifecycleStage.LEAD or opened
            assert created_at <= updated_at < END_OF_RUN
        assert {LifecycleStage.SUBSCRIBER, LifecycleStage.LEAD, LifecycleStage.CUSTOMER} <= stages

    @pytest.mark.integration
    def test_newsletter_only_on_tuesdays(self, month: Database) -> None:
        with month.connect() as conn:
            sent = [row[0] for row in conn.execute("SELECT sent_at FROM raw_crm.email_sends WHERE campaign_id IS NULL")]

        assert {instant.weekday() for instant in sent} == {TUESDAY_WEEKDAY}
        assert len({instant.date() for instant in sent}) == 4

    @pytest.mark.integration
    def test_email_engagement_is_ordered_rated_and_never_after_the_last_day(self, month: Database) -> None:
        with month.connect() as conn:
            sends = conn.execute("SELECT sent_at, opened_at, clicked_at FROM raw_crm.email_sends").fetchall()

        settled = [opened for sent, opened, _ in sends if sent < END_OF_RUN - timedelta(days=3)]
        open_rate = sum(1 for opened in settled if opened is not None) / len(settled)

        for sent, opened, clicked in sends:
            assert opened is None or sent <= opened <= sent + timedelta(hours=48)
            assert clicked is None or (opened is not None and opened <= clicked)
            assert max(t for t in (sent, opened, clicked) if t is not None) < END_OF_RUN
        assert 0.17 <= open_rate <= 0.33

    def test_zero_scale_without_leads_writes_nothing(self) -> None:
        changes = DayChanges()

        _simulator(scale=0.0).simulate_day(_snapshot(TUESDAY), changes)

        assert changes.insert_counts() == {}
        assert changes.updates == []

    def test_tuesday_newsletter_reaches_every_lead_once(self) -> None:
        leads = [_lead(lead_id) for lead_id in range(1, 101)]
        changes = DayChanges()

        _simulator(scale=0.0).simulate_day(_snapshot(TUESDAY, leads=leads), changes)

        sends = changes.inserted(EmailSend)
        assert sorted(s.lead_id for s in sends) == [lead.id for lead in leads]
        assert all(s.campaign_id is None and s.sent_at.date() == TUESDAY for s in sends)

    def test_email_campaign_sends_to_30_percent_of_leads_on_its_start_weekday_only(self) -> None:
        leads = [_lead(lead_id) for lead_id in range(1, 401)]
        campaign = _email_campaign(starts=SATURDAY - timedelta(days=7))
        on_day, off_day = DayChanges(), DayChanges()

        _simulator(scale=0.0).simulate_day(_snapshot(SATURDAY, leads=leads, campaign=campaign), on_day)
        _simulator(scale=0.0).simulate_day(
            _snapshot(SATURDAY + timedelta(days=1), leads=leads, campaign=campaign), off_day
        )

        sends = on_day.inserted(EmailSend)
        assert all(s.campaign_id == campaign.id for s in sends)
        assert len({s.lead_id for s in sends}) == len(sends)
        assert 0.24 <= len(sends) / len(leads) <= 0.36
        assert off_day.inserted(EmailSend) == []

    def test_new_leads_follow_the_scaled_daily_rate(self) -> None:
        simulator = _simulator(scale=1.0)
        counts: list[int] = []

        for offset in range(30):
            changes = DayChanges()
            simulator.simulate_day(_snapshot(SATURDAY + timedelta(days=offset)), changes)
            counts.append(len(changes.inserted(Lead)))

        assert 14.0 <= sum(counts) / len(counts) <= 18.0

    def test_signup_converts_matching_lead_one_to_two_days_later(self) -> None:
        signed_up_at = datetime(2025, 3, 12, 15, tzinfo=UTC)
        lead = _lead(7, email=" Anna.Berg@Example.com")
        customer = make_customer(3).model_copy(
            update={"email": "anna.berg@example.com", "created_at": signed_up_at, "updated_at": signed_up_at}
        )
        days = [signed_up_at.date() + timedelta(days=offset) for offset in range(4)]
        conversions: list[tuple[date, dict[str, object]]] = []

        for day in days:
            changes = DayChanges()
            _simulator(scale=0.0).simulate_day(_snapshot(day, leads=[lead], customers=[customer]), changes)
            conversions.extend((day, u.values) for u in changes.updates if u.record_type is Lead)

        assert len(conversions) == 1
        day, values = conversions[0]
        assert values["lifecycle_stage"] == LifecycleStage.CUSTOMER
        assert signed_up_at + timedelta(days=1) <= values["updated_at"] < signed_up_at + timedelta(days=2)
        assert day in days[1:3]

    def test_converted_lead_is_not_converted_again(self) -> None:
        signed_up_at = datetime(2025, 3, 12, 15, tzinfo=UTC)
        lead = _lead(7, stage=LifecycleStage.CUSTOMER)
        customer = make_customer(3).model_copy(update={"email": lead.email, "created_at": signed_up_at})
        changes = [DayChanges() for _ in range(3)]

        for offset, day_changes in enumerate(changes):
            day = signed_up_at.date() + timedelta(days=offset)
            _simulator(scale=0.0).simulate_day(_snapshot(day, leads=[lead], customers=[customer]), day_changes)

        assert all(c.updates == [] for c in changes)


def _simulator(scale: float = 1.0) -> CrmActivitySimulator:
    return CrmActivitySimulator(DeterministicRandom(SIMULATION_SEED), PrimaryKeyAllocator({}), RealismRules(), scale)


def _snapshot(
    day: date,
    leads: list[Lead] | None = None,
    customers: list[Customer] | None = None,
    campaign: Campaign | None = None,
) -> StoreSnapshot:
    customers = customers or []
    return StoreSnapshot(
        day=day,
        variants=[],
        variant_popularity={},
        customers=customers,
        customer_propensity={c.id: 1.0 for c in customers},
        customer_devices={},
        leads=leads or [],
        active_campaigns={CampaignChannel.EMAIL: [campaign]} if campaign else {},
        recent_email_sends=[],
        active_promotions=[],
        coupon_codes=[],
        shipping_address_ids={},
        open_orders=[],
        open_shipments=[],
        captured_payments=[],
        products=[],
        product_category_l1={},
        recently_delivered_orders=[],
        recently_delivered_items=[],
        recently_delivered_payments=[],
        open_returns=[],
    )


def _lead(lead_id: int, email: str | None = None, stage: LifecycleStage = LifecycleStage.SUBSCRIBER) -> Lead:
    created_at = datetime(2025, 2, 1, tzinfo=UTC)
    return Lead(
        id=lead_id,
        email=email or f"lead{lead_id}@example.com",
        source=LeadSource.NEWSLETTER,
        lifecycle_stage=stage,
        created_at=created_at,
        updated_at=created_at,
    )


def _email_campaign(starts: date) -> Campaign:
    starts_at = datetime(starts.year, starts.month, starts.day, tzinfo=UTC)
    return Campaign(
        id=9,
        name="Spring Newsletter 2025",
        utm_campaign="spring-newsletter-2025",
        channel=CampaignChannel.EMAIL,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=30),
        budget=5000,
    )
