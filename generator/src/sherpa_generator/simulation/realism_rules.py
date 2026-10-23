from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    import random
    from collections.abc import Sequence

    from sherpa_generator.records import TrackedEvent

_SECONDS_PER_DAY = 86_400


class RealismRules:
    """The fixed realism rules R1-R12 of Spec A §4.4: the rates and the noise each rule injects."""

    DUPLICATE_EVENT_RATE: Final = 0.01  # R1
    DUPLICATE_MAX_DELAY_SECONDS: Final = 300  # R1: the copy arrives 0-300 s later, on the same UTC day
    LATE_EVENT_RATE: Final = 0.02  # R2
    LATE_MIN_DAYS: Final = 1  # R2
    LATE_MAX_DAYS_EXCLUSIVE: Final = 4  # R2: received from 1 day up to but excluding 4 days after occurred
    EMAIL_NOISE_RATE: Final = 0.10  # R3
    COUNTRY_NOISE_RATE: Final = 0.03  # R4
    TEST_ACCOUNT_RATE: Final = 0.005  # R5
    TEST_EMAIL_DOMAIN: Final = "sqlsherpa-test.com"  # R5
    SOFT_DELETED_CUSTOMER_RATE: Final = 0.01  # R6
    FAILED_PAYMENT_RATE: Final = 0.03  # R7
    SPLIT_PAYMENT_RATE: Final = 0.05  # R7
    MULTI_DISCOUNT_ORDER_RATE: Final = 0.03  # R8
    RETURN_RATE: Final = 0.12  # R9 (§5.2 step 7)
    FOOTWEAR_RETURN_MULTIPLIER: Final = 2  # R9
    RETURN_APPROVAL_RATE: Final = 0.90  # R9
    GOODWILL_REFUND_RATE: Final = 0.01  # R9
    GOODWILL_REFUND_MIN_SHARE: Final = 0.05  # R9
    GOODWILL_REFUND_MAX_SHARE: Final = 0.20  # R9
    LIST_PRICE_CHANGE_RATE: Final = 0.003  # R10
    COST_PRICE_CHANGE_RATE: Final = 0.001  # R10
    PRICE_CHANGE_MIN_SHARE: Final = 0.05  # R10: +/- 5-20 %
    PRICE_CHANGE_MAX_SHARE: Final = 0.20  # R10
    AD_CLICK_MIN_FACTOR: Final = 0.85  # R11: clicks = paid sessions x U(0.85, 1.15)
    AD_CLICK_MAX_FACTOR: Final = 1.15  # R11
    APP_ORDER_SHARE: Final = 0.35  # R12

    COUNTRY_WEIGHTS: Final = {
        "DE": 0.30,
        "FR": 0.20,
        "ES": 0.15,
        "IT": 0.12,
        "NL": 0.08,
        "GB": 0.08,
        "AT": 0.04,
        "BE": 0.03,
    }
    EXTRA_COUNTRY_VARIANTS: Final = {"GB": ("UK", "uk")}  # R4

    def duplicate_events(self, events: Sequence[TrackedEvent], r: random.Random) -> list[TrackedEvent]:
        """Copies of ~1 % of the events: same event_id, received a few seconds later but on the same UTC day."""
        duplicates: list[TrackedEvent] = []
        for event in events:
            if r.random() >= self.DUPLICATE_EVENT_RATE:
                continue
            delay = timedelta(seconds=r.randint(0, self.DUPLICATE_MAX_DELAY_SECONDS))
            received_at = min(event.received_at + delay, self._last_instant_of_day(event.received_at))
            duplicates.append(event.model_copy(update={"received_at": received_at}))
        return duplicates

    def late_delivery_delay(self, r: random.Random) -> timedelta:
        """Zero for on-time events; otherwise between 1 and 4 days."""
        if r.random() >= self.LATE_EVENT_RATE:
            return timedelta(0)
        low = self.LATE_MIN_DAYS * _SECONDS_PER_DAY
        high = self.LATE_MAX_DAYS_EXCLUSIVE * _SECONDS_PER_DAY
        return timedelta(seconds=r.randrange(low, high))

    def noisy_email(self, email: str, r: random.Random) -> str:
        """Casing or whitespace noise that `lower(trim(email))` undoes."""
        if r.random() >= self.EMAIL_NOISE_RATE:
            return email
        variants = (email.upper(), email.capitalize(), f" {email}", f"{email} ", f" {email.upper()} ")
        return r.choice(variants)

    def noisy_country(self, code: str, r: random.Random) -> str:
        if r.random() >= self.COUNTRY_NOISE_RATE:
            return code
        return r.choice(self.noisy_country_variants()[code])

    def is_test_account(self, r: random.Random) -> bool:
        return r.random() < self.TEST_ACCOUNT_RATE

    def ad_clicks(self, paid_sessions: int, r: random.Random) -> int:
        """Ad-platform clicks never reconcile exactly with tracked paid sessions."""
        return round(paid_sessions * r.uniform(self.AD_CLICK_MIN_FACTOR, self.AD_CLICK_MAX_FACTOR))

    def noisy_country_variants(self) -> dict[str, tuple[str, ...]]:
        return {code: (code.lower(), *self.EXTRA_COUNTRY_VARIANTS.get(code, ())) for code in self.COUNTRY_WEIGHTS}

    @staticmethod
    def _last_instant_of_day(instant: datetime) -> datetime:
        utc = instant.astimezone(UTC)
        return utc.replace(hour=23, minute=59, second=59, microsecond=999_999)
