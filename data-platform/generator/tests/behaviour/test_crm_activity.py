from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from helpers import MONTH_END_AT, SIMULATION_SEED, assert_rate, make_campaign, make_customer, shop_snapshot
from sherpa_generator.behaviour.crm_activity import CrmActivitySimulator
from sherpa_generator.records import Campaign, CampaignChannel, EmailSend, Lead, LeadSource, LifecycleStage
from sherpa_generator.simulation.day_changes import DayChanges
from sherpa_generator.simulation.deterministic_random import DeterministicRandom
from sherpa_generator.simulation.primary_keys import PrimaryKeyAllocator
from sherpa_generator.simulation.realism_rules import RealismRules

if TYPE_CHECKING:
    from sherpa_generator.database.connection import Database
    from sherpa_generator.records import Customer
    from sherpa_generator.simulation.store_snapshot import StoreSnapshot

TUESDAY = date(2025, 3, 11)
SATURDAY = date(2025, 3, 15)
STAGE_RANK = {stage: rank for rank, stage in enumerate(LifecycleStage)}
TUESDAY_WEEKDAY = 1
RATE_LEADS = 4_000  # ~1,000 opens and ~120 clicks: enough for assert_rate to tell 3 % and 10 % from 0
LEAD_RATE_SCALE = 250.0  # one day of 16 x 250 new leads
ENGAGEMENT_DAYS = 4  # a send at 08:00-08:30 is opened within 48 h and clicked within 30 minutes of that


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

        converted_by = MONTH_END_AT - timedelta(days=2)
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
            assert created_at <= updated_at < MONTH_END_AT
        assert {LifecycleStage.SUBSCRIBER, LifecycleStage.LEAD, LifecycleStage.CUSTOMER} <= stages

    @pytest.mark.integration
    def test_newsletter_only_on_tuesdays(self, month: Database) -> None:
        with month.connect() as conn:
            sent = [row[0] for row in conn.execute("SELECT sent_at FROM raw_crm.email_sends WHERE campaign_id IS NULL")]

        assert {instant.weekday() for instant in sent} == {TUESDAY_WEEKDAY}
        assert len({instant.date() for instant in sent}) == 4

    @pytest.mark.integration
    def test_email_engagement_is_ordered_and_never_after_the_last_day(self, month: Database) -> None:
        with month.connect() as conn:
            sends = conn.execute("SELECT sent_at, opened_at, clicked_at FROM raw_crm.email_sends").fetchall()

        for sent, opened, clicked in sends:
            assert opened is None or sent <= opened <= sent + timedelta(hours=48)
            assert clicked is None or (opened is not None and opened <= clicked)
            assert max(t for t in (sent, opened, clicked) if t is not None) < MONTH_END_AT

    def test_email_noise_hits_ten_percent_of_new_leads_and_normalizes_back(self) -> None:
        changes = DayChanges()

        _simulator(scale=LEAD_RATE_SCALE).simulate_day(_snapshot(SATURDAY), changes)

        emails = [lead.email for lead in changes.inserted(Lead)]
        noisy = [email for email in emails if email != email.strip().lower()]
        assert_rate(len(noisy), len(emails), 0.10)  # R3, Spec A §4.4
        assert all(email.strip().lower().count("@") == 1 for email in noisy)

    def test_opens_hit_25_percent_and_clicks_3_percent_of_sends_and_every_click_was_opened(self) -> None:
        leads = [_lead(lead_id) for lead_id in range(1, RATE_LEADS + 1)]
        sends = _newsletter_sends_with_engagement(leads)

        opened = [send for send in sends if send.opened_at is not None]
        clicked = [send for send in sends if send.clicked_at is not None]

        assert len(sends) == RATE_LEADS
        assert_rate(len(opened), len(sends), 0.25)  # Spec A §5.2 step 2: open 25 % of sends
        assert_rate(len(clicked), len(sends), 0.03)  # Spec A §5.2 step 2: click 3 % of sends
        assert {send.id for send in clicked} <= {send.id for send in opened}

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
        customer = make_customer(3, email="anna.berg@example.com", created_at=signed_up_at, updated_at=signed_up_at)
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
        customer = make_customer(3, email=lead.email, created_at=signed_up_at)
        changes = [DayChanges() for _ in range(3)]

        for offset, day_changes in enumerate(changes):
            day = signed_up_at.date() + timedelta(days=offset)
            _simulator(scale=0.0).simulate_day(_snapshot(day, leads=[lead], customers=[customer]), day_changes)

        assert all(c.updates == [] for c in changes)


def _simulator(scale: float = 1.0) -> CrmActivitySimulator:
    return CrmActivitySimulator(DeterministicRandom(SIMULATION_SEED), PrimaryKeyAllocator({}), RealismRules(), scale)


def _newsletter_sends_with_engagement(leads: list[Lead]) -> list[EmailSend]:
    """The Tuesday newsletter to `leads`, with the opens and clicks that the following days write onto the sends."""
    simulator = _simulator(scale=0.0)
    first = DayChanges()
    simulator.simulate_day(_snapshot(TUESDAY, leads=leads), first)
    sends = {send.id: send for send in first.inserted(EmailSend)}
    for offset in range(1, ENGAGEMENT_DAYS):
        changes = DayChanges()
        snapshot = _snapshot(TUESDAY + timedelta(days=offset), leads=leads)
        simulator.simulate_day(snapshot.model_copy(update={"recent_email_sends": list(sends.values())}), changes)
        for update in changes.updates:
            if update.record_type is EmailSend:
                key = int(str(update.key["id"]))
                sends[key] = sends[key].model_copy(update=update.values)

    return list(sends.values())


def _snapshot(
    day: date,
    leads: list[Lead] | None = None,
    customers: list[Customer] | None = None,
    campaign: Campaign | None = None,
) -> StoreSnapshot:
    return shop_snapshot(
        day,
        customers=customers or [],
        leads=leads or [],
        active_campaigns={CampaignChannel.EMAIL: [campaign]} if campaign else {},
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
    return make_campaign(
        9,
        name="Spring Newsletter 2025",
        utm_campaign="spring-newsletter-2025",
        channel=CampaignChannel.EMAIL,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=30),
        budget=Decimal(5000),
    )
