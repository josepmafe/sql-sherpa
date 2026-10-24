from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Final

from sherpa_generator.behaviour.visitor_traffic import LOCALE_BY_COUNTRY, normalized_email
from sherpa_generator.records import CampaignChannel, EmailSend, Lead, LeadSource, LifecycleStage
from sherpa_generator.simulation.calendar import SimulationCalendar
from sherpa_generator.simulation.deterministic_random import poisson

if TYPE_CHECKING:
    import random
    from datetime import datetime

    from faker import Faker

    from sherpa_generator.records import Campaign
    from sherpa_generator.simulation.day_changes import DayChanges
    from sherpa_generator.simulation.deterministic_random import DeterministicRandom
    from sherpa_generator.simulation.primary_keys import PrimaryKeyAllocator
    from sherpa_generator.simulation.realism_rules import RealismRules
    from sherpa_generator.simulation.store_snapshot import StoreSnapshot

STREAM: Final = "crm"
BASE_DAILY_LEADS: Final = 16  # Spec A §5.2 step 2
LEAD_SOURCE_WEIGHTS: Final = {
    LeadSource.NEWSLETTER: 0.45,
    LeadSource.POPUP: 0.30,
    LeadSource.GIVEAWAY: 0.15,
    LeadSource.REFERRAL: 0.10,
}
NEWSLETTER_WEEKDAY: Final = 1  # Spec A §5.2 step 2: Tuesdays (date.weekday())
NEWSLETTER_SEND_HOUR: Final = 8
CAMPAIGN_SEND_HOUR: Final = 10
SEND_SPREAD_SECONDS: Final = 1800  # a send batch goes out over half an hour
CAMPAIGN_AUDIENCE_SHARE: Final = 0.30  # each campaign send reaches 30 % of the leads
OPEN_RATE: Final = 0.25  # Spec A §5.2 step 2: of sends
CLICK_RATE: Final = 0.03  # Spec A §5.2 step 2: of sends; only opened sends are clicked
OPEN_WINDOW: Final = timedelta(hours=48)
CLICK_DELAY: Final = (timedelta(seconds=10), timedelta(minutes=30))
CUSTOMER_CONVERSION_DELAY: Final = (timedelta(days=1), timedelta(days=2))  # after the matching signup
SECONDS_PER_DAY: Final = 86_400
_STAGE_RANK: Final = {stage: rank for rank, stage in enumerate(LifecycleStage)}


class CrmActivitySimulator:
    """Spec A §5.2 step 2: new leads, newsletter and campaign sends, and lifecycle progression of leads.

    Opens, clicks and conversions are fixed per send or lead by `entity_fraction`. Those that fall on a later day
    are written on that day, so no timestamp ever lies beyond the simulated day.
    """

    def __init__(
        self, random: DeterministicRandom, keys: PrimaryKeyAllocator, rules: RealismRules, scale: float
    ) -> None:
        self._random = random
        self._keys = keys
        self._rules = rules
        self._scale = scale
        self._calendar = SimulationCalendar()

    def simulate_day(self, snapshot: StoreSnapshot, changes: DayChanges) -> None:
        start, end = self._calendar.day_bounds(snapshot.day)
        day = _CrmDay(
            snapshot=snapshot,
            changes=changes,
            rng=self._random.for_day(snapshot.day, STREAM),
            faker=self._random.faker_for_day(snapshot.day, STREAM),
            start=start,
            end=end,
        )
        for _ in range(poisson(day.rng, BASE_DAILY_LEADS * self._scale)):
            self._insert_lead(day)

        self._send_emails(day)
        self._engage_recent_sends(day)
        self._convert_signed_up_leads(day)
        self._apply_lifecycle_transitions(day)

    def _insert_lead(self, day: _CrmDay) -> None:
        countries = self._rules.COUNTRY_WEIGHTS
        country = day.rng.choices(tuple(countries), weights=tuple(countries.values()))[0]
        faker = day.faker[LOCALE_BY_COUNTRY[country]]
        lead_id = self._keys.next_id(Lead)
        created_at = day.start + timedelta(seconds=day.rng.randrange(SECONDS_PER_DAY))
        day.changes.insert(
            Lead(
                id=lead_id,
                email=self._rules.noisy_email(f"{faker.user_name()}{lead_id}@{faker.free_email_domain()}", day.rng),
                source=day.rng.choices(tuple(LEAD_SOURCE_WEIGHTS), weights=tuple(LEAD_SOURCE_WEIGHTS.values()))[0],
                lifecycle_stage=LifecycleStage.SUBSCRIBER,
                created_at=created_at,
                updated_at=created_at,
            )
        )

    def _send_emails(self, day: _CrmDay) -> None:
        """The Tuesday newsletter goes to every lead; an email campaign goes out on its start weekday."""
        weekday = day.snapshot.day.weekday()
        if weekday == NEWSLETTER_WEEKDAY:
            for lead in day.snapshot.leads:
                self._send(day, lead, None, NEWSLETTER_SEND_HOUR)

        for campaign in day.snapshot.active_campaigns.get(CampaignChannel.EMAIL, []):
            if campaign.starts_at.weekday() != weekday:
                continue

            for lead in day.snapshot.leads:
                if day.rng.random() < CAMPAIGN_AUDIENCE_SHARE:
                    self._send(day, lead, campaign, CAMPAIGN_SEND_HOUR)

    def _send(self, day: _CrmDay, lead: Lead, campaign: Campaign | None, hour: int) -> None:
        send_id = self._keys.next_id(EmailSend)
        sent_at = day.start + timedelta(hours=hour, seconds=day.rng.randrange(SEND_SPREAD_SECONDS))
        opened_at, clicked_at = (
            instant if instant is not None and instant < day.end else None
            for instant in self._engagement(send_id, sent_at)
        )
        day.changes.insert(
            EmailSend(
                id=send_id,
                lead_id=lead.id,
                campaign_id=campaign.id if campaign else None,
                sent_at=sent_at,
                opened_at=opened_at,
                clicked_at=clicked_at,
            )
        )
        if opened_at is not None:
            day.transitions.append((opened_at, lead.id, LifecycleStage.LEAD))
        if clicked_at is not None:
            day.transitions.append((clicked_at, lead.id, LifecycleStage.MQL))

    def _engage_recent_sends(self, day: _CrmDay) -> None:
        """Opens and clicks of earlier sends that fall on this day."""
        for send in day.snapshot.recent_email_sends:
            opened_at, clicked_at = self._engagement(send.id, send.sent_at)
            values: dict[str, object] = {}
            if send.opened_at is None and opened_at is not None and day.start <= opened_at < day.end:
                values["opened_at"] = opened_at
                day.transitions.append((opened_at, send.lead_id, LifecycleStage.LEAD))
            if send.clicked_at is None and clicked_at is not None and day.start <= clicked_at < day.end:
                values["clicked_at"] = clicked_at
                day.transitions.append((clicked_at, send.lead_id, LifecycleStage.MQL))
            if values:
                day.changes.update(EmailSend, send.id, **values)

    def _engagement(self, send_id: int, sent_at: datetime) -> tuple[datetime | None, datetime | None]:
        """(opened_at, clicked_at) of a send, wherever they fall in time. One fraction decides both, so every
        clicked send is also opened."""
        engaged = self._random.entity_fraction("email_send", send_id, "engagement")
        if engaged >= OPEN_RATE:
            return None, None

        opened_at = sent_at + OPEN_WINDOW * self._random.entity_fraction("email_send", send_id, "open_delay")
        if engaged >= CLICK_RATE:
            return opened_at, None

        low, high = CLICK_DELAY
        return opened_at, opened_at + low + (high - low) * self._random.entity_fraction("email_send", send_id, "click")

    def _convert_signed_up_leads(self, day: _CrmDay) -> None:
        """A lead becomes a customer 1-2 days after a signup with its (normalized) email."""
        low, high = CUSTOMER_CONVERSION_DELAY
        open_leads = {
            normalized_email(lead.email): lead
            for lead in day.snapshot.leads
            if lead.lifecycle_stage != LifecycleStage.CUSTOMER
        }
        for customer in day.snapshot.customers:
            lead = open_leads.get(normalized_email(customer.email))
            if lead is None or customer.created_at < day.start - high:
                continue

            delay = low + (high - low) * self._random.entity_fraction("lead", lead.id, "conversion_delay")
            converted_at = customer.created_at + delay
            if day.start <= converted_at < day.end:
                day.transitions.append((converted_at, lead.id, LifecycleStage.CUSTOMER))

    def _apply_lifecycle_transitions(self, day: _CrmDay) -> None:
        """Stages only move forward: a transition to an earlier stage than the current one is ignored."""
        stages = {lead.id: lead.lifecycle_stage for lead in day.snapshot.leads}
        changed: dict[int, datetime] = {}
        for at, lead_id, stage in sorted(day.transitions):
            if _STAGE_RANK[stage] > _STAGE_RANK[stages[lead_id]]:
                stages[lead_id] = stage
                changed[lead_id] = at

        for lead_id in sorted(changed):
            day.changes.update(Lead, lead_id, lifecycle_stage=stages[lead_id], updated_at=changed[lead_id])


@dataclass(slots=True)
class _CrmDay:
    """Mutable state of one simulated CRM day."""

    snapshot: StoreSnapshot
    changes: DayChanges
    rng: random.Random
    faker: Faker
    start: datetime
    end: datetime
    transitions: list[tuple[datetime, int, LifecycleStage]] = field(default_factory=list)
