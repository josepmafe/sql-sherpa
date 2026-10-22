from __future__ import annotations

import uuid
from bisect import bisect
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum, auto
from itertools import accumulate
from typing import TYPE_CHECKING, Final

from pydantic import AwareDatetime, BaseModel, ConfigDict

from sherpa_generator.records import (
    Address,
    AddressType,
    Campaign,
    CampaignChannel,
    Customer,
    DeviceType,
    EventContext,
    EventProperties,
    EventType,
    Lead,
    LifecycleStage,
    PendingEvent,
    ProductVariant,
    SalesChannel,
    TrackedEvent,
)
from sherpa_generator.simulation.deterministic_random import poisson, stable_int

if TYPE_CHECKING:
    import random

    from faker import Faker

    from sherpa_generator.simulation.calendar import SimulationCalendar
    from sherpa_generator.simulation.day_changes import DayChanges
    from sherpa_generator.simulation.deterministic_random import DeterministicRandom
    from sherpa_generator.simulation.primary_keys import PrimaryKeyAllocator
    from sherpa_generator.simulation.realism_rules import RealismRules
    from sherpa_generator.simulation.store_snapshot import StoreSnapshot


class TrafficChannel(StrEnum):
    DIRECT = auto()
    ORGANIC = auto()
    PAID_SEARCH = auto()
    PAID_SOCIAL = auto()
    EMAIL = auto()
    AFFILIATE = auto()
    REFERRAL = auto()


STREAM: Final = "traffic"
BASE_DAILY_SESSIONS: Final = 400  # Spec A §5.2 step 3
CHANNEL_WEIGHTS: Final = {  # Spec A §5.2 step 3
    TrafficChannel.DIRECT: 0.25,
    TrafficChannel.ORGANIC: 0.25,
    TrafficChannel.PAID_SEARCH: 0.18,
    TrafficChannel.PAID_SOCIAL: 0.12,
    TrafficChannel.EMAIL: 0.10,
    TrafficChannel.AFFILIATE: 0.05,
    TrafficChannel.REFERRAL: 0.05,
}
# Paid channels need an active campaign; without one their share goes to the other channels.
PAID_CAMPAIGN_CHANNELS: Final = {
    TrafficChannel.PAID_SEARCH: CampaignChannel.PAID_SEARCH,
    TrafficChannel.PAID_SOCIAL: CampaignChannel.PAID_SOCIAL,
    TrafficChannel.AFFILIATE: CampaignChannel.AFFILIATE,
}
CAMPAIGN_CHANNELS: Final = {**PAID_CAMPAIGN_CHANNELS, TrafficChannel.EMAIL: CampaignChannel.EMAIL}
UTM_SOURCES_AND_MEDIUM: Final = {
    TrafficChannel.PAID_SEARCH: (("google",), "cpc"),
    TrafficChannel.PAID_SOCIAL: (("facebook", "instagram"), "paid_social"),
    TrafficChannel.EMAIL: (("newsletter",), "email"),
    TrafficChannel.AFFILIATE: (("awin",), "affiliate"),
}
REFERRERS: Final = {
    TrafficChannel.ORGANIC: ("https://www.google.com/", "https://www.bing.com/", "https://duckduckgo.com/"),
    TrafficChannel.REFERRAL: (
        "https://www.reddit.com/",
        "https://www.runnersworld.com/",
        "https://www.outdoorgearlab.com/",
    ),
}
DEVICE_TYPE_WEIGHTS: Final = {DeviceType.MOBILE: 0.55, DeviceType.DESKTOP: 0.35, DeviceType.TABLET: 0.10}
LOCALE_BY_COUNTRY: Final = {
    "DE": "de_DE",
    "FR": "fr_FR",
    "ES": "es_ES",
    "IT": "it_IT",
    "NL": "nl_NL",
    "GB": "en_GB",
    "AT": "de_AT",
    "BE": "nl_BE",
}

# Sessions from existing customers, chosen by propensity. Tuned (the plan said 0.30) so that web conversion lands on
# Spec A §5.2's ~3.5 %: the funnel rates multiply to 6.6 %, new visitors buy only through the ~14 daily signups
# (~9.8 purchases), so returning visitors may add only ~4.2 purchases per 400 sessions.
RETURNING_VISITOR_SHARE: Final = 0.16
NEW_DEVICE_RATE: Final = 0.10
MAX_DEVICES_PER_CUSTOMER: Final = 3  # Spec A §5.2 step 3: 1-3 devices per visitor
LOGIN_FIRST_RATE: Final = 0.60
PRODUCT_VIEW_RATE: Final = 0.70  # Spec A §5.2 step 3 funnel
MIN_PRODUCT_VIEWS: Final = 1
MAX_PRODUCT_VIEWS: Final = 6
ADD_TO_CART_RATE: Final = 0.30
BEGIN_CHECKOUT_RATE: Final = 0.45
PURCHASE_RATE: Final = 0.70
EVENT_GAP_SECONDS: Final = (10, 240)
DELIVERY_LATENCY_SECONDS: Final = (0.2, 5.0)
# Session start hour (UTC) weights: peaks at 12-14 and 19-22.
HOUR_WEIGHTS: Final = (1, 1, 1, 1, 1, 2, 3, 4, 5, 6, 6, 7, 10, 10, 10, 7, 6, 6, 7, 10, 10, 10, 10, 4)
SECONDS_PER_HOUR: Final = 3600
# page_view, login, the product views, add_to_cart, begin_checkout and purchase: a session spans at most this many
# gaps, so it never runs past midnight when it starts no later than LAST_SESSION_START.
MAX_SESSION_GAPS: Final = MAX_PRODUCT_VIEWS + 4
LAST_SESSION_START: Final = timedelta(days=1) - timedelta(seconds=EVENT_GAP_SECONDS[1] * MAX_SESSION_GAPS)
LANDING_PAGE: Final = "/"
CHECKOUT_PAGE: Final = "/checkout"

# Spec A §5.2 step 4 says ~14 a day before seasonality, but the calendar averages ~1.13x over a year, so 14 lands
# ~5.8k customers against the ~5k the same section expects for 365 days; 12 lands ~5k.
BASE_DAILY_SIGNUPS: Final = 12
LEAD_EMAIL_REUSE_RATE: Final = 0.35  # Spec A §5.2 step 4
MARKETING_OPT_IN_RATE: Final = 0.55
ADDRESS_SLOTS: Final = ((AddressType.SHIPPING, True), (AddressType.BILLING, True), (AddressType.SHIPPING, False))
MIN_ADDRESSES: Final = 1  # Spec A §4.1: 1-3 addresses per customer
_UINT64_RANGE: Final = 2**64
_DEVICE_TYPES: Final = tuple(DEVICE_TYPE_WEIGHTS)
_DEVICE_CUM_WEIGHTS: Final = tuple(accumulate(DEVICE_TYPE_WEIGHTS.values()))


class PurchaseIntent(BaseModel):
    """A purchase decided by traffic; the order lifecycle turns it into an order. App intents have no events."""

    model_config = ConfigDict(frozen=True)

    customer_id: int
    sales_channel: SalesChannel
    purchased_at: AwareDatetime
    session_anonymous_id: str | None
    utm_campaign: str | None
    purchase_event_id: str | None


class TrafficOutcome(BaseModel):
    model_config = ConfigDict(frozen=True)

    purchase_intents: list[PurchaseIntent]  # ordered by purchased_at
    paid_sessions_by_campaign: dict[int, int]
    new_customers: list[Customer]


class VisitorTrafficSimulator:
    """Spec A §5.2 steps 3-4: sessions with their tracked events, signups, and the day's purchase intents."""

    def __init__(
        self,
        random: DeterministicRandom,
        keys: PrimaryKeyAllocator,
        rules: RealismRules,
        calendar: SimulationCalendar,
        scale: float,
    ) -> None:
        self._random = random
        self._keys = keys
        self._rules = rules
        self._calendar = calendar
        self._scale = scale
        self._canonical_country = {
            variant: code for code, variants in rules.noisy_country_variants().items() for variant in (code, *variants)
        }

    def simulate_day(self, snapshot: StoreSnapshot, changes: DayChanges) -> TrafficOutcome:
        day = self._start_day(snapshot, changes)
        volume = self._scale * self._calendar.seasonality(snapshot.day)
        sessions = poisson(day.rng, BASE_DAILY_SESSIONS * volume)
        day.signups_left = poisson(day.rng, BASE_DAILY_SIGNUPS * volume)
        for _ in range(sessions):
            self._session(day)

        for event in [*day.landed, *self._rules.duplicate_events(day.landed, day.rng), *day.pending]:
            changes.insert(event)

        intents = [*day.web_intents, *self._app_intents(day)]
        return TrafficOutcome(
            purchase_intents=sorted(intents, key=lambda intent: intent.purchased_at),
            paid_sessions_by_campaign=dict(sorted(day.paid_sessions.items())),
            new_customers=day.new_customers,
        )

    def _start_day(self, snapshot: StoreSnapshot, changes: DayChanges) -> _TrafficDay:
        rng = self._random.for_day(snapshot.day, STREAM)
        start, end = self._calendar.day_bounds(snapshot.day)
        channels = [
            channel
            for channel in CHANNEL_WEIGHTS
            if channel not in PAID_CAMPAIGN_CHANNELS or snapshot.active_campaigns.get(PAID_CAMPAIGN_CHANNELS[channel])
        ]
        taken_emails = {normalized_email(c.email) for c in snapshot.customers}
        viewable = [v for v in snapshot.variants if v.id in snapshot.variant_popularity]
        return _TrafficDay(
            snapshot=snapshot,
            changes=changes,
            rng=rng,
            faker=self._random.faker_for_day(snapshot.day, STREAM),
            start=start,
            end=end,
            channels=channels,
            channel_cum_weights=list(accumulate(CHANNEL_WEIGHTS[c] for c in channels)),
            propensity_cum_weights=list(accumulate(snapshot.propensity(c.id) for c in snapshot.customers)),
            viewable=viewable,
            viewable_cum_weights=list(accumulate(snapshot.variant_popularity[v.id] for v in viewable)),
            open_leads=[
                lead
                for lead in snapshot.leads
                if lead.lifecycle_stage != LifecycleStage.CUSTOMER and normalized_email(lead.email) not in taken_emails
            ],
        )

    def _session(self, day: _TrafficDay) -> None:
        channel = day.rng.choices(day.channels, cum_weights=day.channel_cum_weights)[0]
        campaign = self._campaign(day, channel)
        customer, anonymous_id, country = self._visitor(day)
        session = _Session(
            anonymous_id=anonymous_id,
            country=country,
            context=self._context(day.rng, channel, campaign, anonymous_id),
            at=day.start + self._start_offset(day.rng, latest=LAST_SESSION_START),
        )
        if campaign is not None and channel in PAID_CAMPAIGN_CHANNELS:
            day.paid_sessions[campaign.id] += 1

        self._emit(day, session, EventType.PAGE_VIEW, EventProperties(page_path=LANDING_PAGE), advance=False)
        if customer is not None and day.rng.random() < LOGIN_FIRST_RATE:
            session.user_id = str(customer.id)
            self._emit(day, session, EventType.LOGIN, EventProperties(page_path=LANDING_PAGE))

        viewed = self._product_views(day, session)
        if not viewed or day.rng.random() >= ADD_TO_CART_RATE:
            return

        variant = day.rng.choice(viewed)
        self._emit(day, session, EventType.ADD_TO_CART, self._product_properties(variant, cart=True))
        if day.rng.random() >= BEGIN_CHECKOUT_RATE:
            return

        checkout = EventProperties(page_path=CHECKOUT_PAGE, cart_value=variant.list_price)
        self._emit(day, session, EventType.BEGIN_CHECKOUT, checkout)
        customer_id = self._identify_at_checkout(day, session, customer)
        if customer_id is None:
            return  # a new visitor beyond the day's signups leaves without an account

        if day.rng.random() >= PURCHASE_RATE:
            return

        purchase = self._emit(day, session, EventType.PURCHASE, checkout)
        day.web_intents.append(
            PurchaseIntent(
                customer_id=customer_id,
                sales_channel=SalesChannel.WEB,
                purchased_at=session.at,
                session_anonymous_id=session.anonymous_id,
                utm_campaign=session.context.utm_campaign,
                purchase_event_id=purchase.event_id,
            )
        )

    def _campaign(self, day: _TrafficDay, channel: TrafficChannel) -> Campaign | None:
        """An active campaign of the channel; email sessions without one come from the weekly newsletter."""
        campaign_channel = CAMPAIGN_CHANNELS.get(channel)
        campaigns = day.snapshot.active_campaigns.get(campaign_channel, []) if campaign_channel else []
        return day.rng.choice(campaigns) if campaigns else None

    def _visitor(self, day: _TrafficDay) -> tuple[Customer | None, str, str]:
        """(returning customer or None, anonymous id, country) of the session's visitor."""
        if day.snapshot.customers and day.rng.random() < RETURNING_VISITOR_SHARE:
            customer = day.rng.choices(day.snapshot.customers, cum_weights=day.propensity_cum_weights)[0]
            return customer, self._device(day, customer), self._canonical_country[customer.country_code]

        countries = self._rules.COUNTRY_WEIGHTS
        return None, _uuid(day.rng), day.rng.choices(tuple(countries), weights=tuple(countries.values()))[0]

    def _device(self, day: _TrafficDay, customer: Customer) -> str:
        devices = day.devices.setdefault(customer.id, list(day.snapshot.customer_devices.get(customer.id, [])))
        if not devices or (len(devices) < MAX_DEVICES_PER_CUSTOMER and day.rng.random() < NEW_DEVICE_RATE):
            devices.append(_uuid(day.rng))
            return devices[-1]

        return day.rng.choice(devices)

    def _context(
        self, rng: random.Random, channel: TrafficChannel, campaign: Campaign | None, anonymous_id: str
    ) -> EventContext:
        device_type = _device_type(anonymous_id)
        if channel in UTM_SOURCES_AND_MEDIUM:
            sources, medium = UTM_SOURCES_AND_MEDIUM[channel]
            return EventContext(
                device_type=device_type,
                utm_source=rng.choice(sources),
                utm_medium=medium,
                utm_campaign=campaign.utm_campaign if campaign else None,
            )
        if channel in REFERRERS:
            return EventContext(device_type=device_type, referrer=rng.choice(REFERRERS[channel]))
        return EventContext(device_type=device_type)

    def _product_views(self, day: _TrafficDay, session: _Session) -> list[ProductVariant]:
        if not day.viewable or day.rng.random() >= PRODUCT_VIEW_RATE:
            return []

        count = day.rng.randint(MIN_PRODUCT_VIEWS, MAX_PRODUCT_VIEWS)
        viewed = day.rng.choices(day.viewable, cum_weights=day.viewable_cum_weights, k=count)
        for variant in viewed:
            self._emit(day, session, EventType.PRODUCT_VIEW, self._product_properties(variant, cart=False))
        return viewed

    def _identify_at_checkout(self, day: _TrafficDay, session: _Session, customer: Customer | None) -> int | None:
        """The buyer's customer id: logs in a known customer or signs up a new visitor while signups remain."""
        page = EventProperties(page_path=CHECKOUT_PAGE)
        if customer is not None:
            if session.user_id is None:
                session.user_id = str(customer.id)
                self._emit(day, session, EventType.LOGIN, page)
            return customer.id

        if day.signups_left <= 0:
            return None

        session.at += self._gap(day.rng)
        new_customer = self._signup(day, session)
        session.user_id = str(new_customer.id)
        self._emit(day, session, EventType.SIGNUP, page, advance=False)
        return new_customer.id

    def _signup(self, day: _TrafficDay, session: _Session) -> Customer:
        day.signups_left -= 1
        faker = day.faker[LOCALE_BY_COUNTRY[session.country]]
        customer_id = self._keys.next_id(Customer)
        is_test_account = self._rules.is_test_account(day.rng)
        email = self._signup_email(day, faker, customer_id, is_test_account=is_test_account)
        customer = Customer(
            id=customer_id,
            email=self._rules.noisy_email(email, day.rng),
            first_name=faker.first_name(),
            last_name=faker.last_name(),
            country_code=self._rules.noisy_country(session.country, day.rng),
            marketing_opt_in=day.rng.random() < MARKETING_OPT_IN_RATE,
            is_test_account=is_test_account,
            created_at=session.at,
            updated_at=session.at,
        )
        day.changes.insert(customer)
        day.new_customers.append(customer)
        for address_type, is_default in ADDRESS_SLOTS[: day.rng.randint(MIN_ADDRESSES, len(ADDRESS_SLOTS))]:
            day.changes.insert(
                Address(
                    id=self._keys.next_id(Address),
                    customer_id=customer_id,
                    type=address_type,
                    country_code=self._rules.noisy_country(session.country, day.rng),
                    city=faker.city(),
                    postal_code=faker.postcode(),
                    is_default=is_default,
                )
            )
        return customer

    def _signup_email(self, day: _TrafficDay, faker: Faker, customer_id: int, *, is_test_account: bool) -> str:
        """A fresh address, or (Spec A §5.2 step 4) the address of a lead that has not signed up yet."""
        if is_test_account:
            return f"{faker.user_name()}{customer_id}@{self._rules.TEST_EMAIL_DOMAIN}"

        if day.open_leads and day.rng.random() < LEAD_EMAIL_REUSE_RATE:
            return normalized_email(day.open_leads.pop(day.rng.randrange(len(day.open_leads))).email)

        return f"{faker.user_name()}{customer_id}@{faker.free_email_domain()}"

    def _app_intents(self, day: _TrafficDay) -> list[PurchaseIntent]:
        """R12: app orders top web orders up to their share; they come from existing customers and leave no events."""
        share = self._rules.APP_ORDER_SHARE
        count = round(len(day.web_intents) * share / (1 - share))
        if not day.snapshot.customers:
            return []

        buyers = day.rng.choices(day.snapshot.customers, cum_weights=day.propensity_cum_weights, k=count)
        return [
            PurchaseIntent(
                customer_id=buyer.id,
                sales_channel=SalesChannel.APP,
                purchased_at=day.start + self._start_offset(day.rng, latest=timedelta(days=1)),
                session_anonymous_id=None,
                utm_campaign=None,
                purchase_event_id=None,
            )
            for buyer in buyers
        ]

    def _emit(
        self,
        day: _TrafficDay,
        session: _Session,
        event_type: EventType,
        properties: EventProperties,
        *,
        advance: bool = True,
    ) -> TrackedEvent:
        """Track one event; it lands today unless its delivery (R2 lateness included) runs past the day."""
        if advance:
            session.at += self._gap(day.rng)

        latency = timedelta(seconds=day.rng.uniform(*DELIVERY_LATENCY_SECONDS))
        received_at = session.at + latency + self._rules.late_delivery_delay(day.rng)
        lands_today = received_at < day.end
        event = (TrackedEvent if lands_today else PendingEvent)(
            event_id=_uuid(day.rng),
            anonymous_id=session.anonymous_id,
            user_id=session.user_id,
            event_type=event_type,
            occurred_at=self._calendar.local_iso(session.at, session.country),
            received_at=received_at,
            context=session.context,
            properties=properties,
        )
        (day.landed if lands_today else day.pending).append(event)
        return event

    @staticmethod
    def _product_properties(variant: ProductVariant, *, cart: bool) -> EventProperties:
        return EventProperties(
            page_path=f"/products/{variant.product_id}",
            product_id=variant.product_id,
            variant_id=variant.id,
            cart_value=variant.list_price if cart else None,
        )

    @staticmethod
    def _start_offset(rng: random.Random, latest: timedelta) -> timedelta:
        """A weighted hour, then a uniform second within that hour's part before `latest` (no clamping spike)."""
        hour = rng.choices(range(len(HOUR_WEIGHTS)), weights=HOUR_WEIGHTS)[0]
        hour_start = hour * SECONDS_PER_HOUR
        hour_end = min(hour_start + SECONDS_PER_HOUR, int(latest.total_seconds()))
        return timedelta(seconds=rng.randrange(hour_start, hour_end))

    @staticmethod
    def _gap(rng: random.Random) -> timedelta:
        return timedelta(seconds=rng.randint(*EVENT_GAP_SECONDS))


def normalized_email(email: str) -> str:
    """The matching key of an email: R3 noise (casing, whitespace) removed, as dbt does with lower(trim())."""
    return email.strip().lower()


@dataclass(slots=True)
class _Session:
    anonymous_id: str
    country: str
    context: EventContext
    at: datetime  # the instant of the latest event
    user_id: str | None = None


@dataclass(slots=True)
class _TrafficDay:
    """Mutable state of one simulated day of traffic."""

    snapshot: StoreSnapshot
    changes: DayChanges
    rng: random.Random
    faker: Faker
    start: datetime
    end: datetime
    channels: list[TrafficChannel]
    channel_cum_weights: list[float]
    propensity_cum_weights: list[float]
    viewable: list[ProductVariant]
    viewable_cum_weights: list[float]
    open_leads: list[Lead]
    signups_left: int = 0
    devices: dict[int, list[str]] = field(default_factory=dict)
    paid_sessions: Counter[int] = field(default_factory=Counter)
    landed: list[TrackedEvent] = field(default_factory=list)
    pending: list[TrackedEvent] = field(default_factory=list)  # PendingEvent instances
    web_intents: list[PurchaseIntent] = field(default_factory=list)
    new_customers: list[Customer] = field(default_factory=list)


def _uuid(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def _device_type(anonymous_id: str) -> DeviceType:
    """Fixed per device, so a returning device keeps its type."""
    fraction = stable_int(anonymous_id, "device_type") / _UINT64_RANGE
    return _DEVICE_TYPES[min(bisect(_DEVICE_CUM_WEIGHTS, fraction), len(_DEVICE_TYPES) - 1)]
