from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from helpers import make_event
from sherpa_generator.simulation.realism_rules import RealismRules

DRAWS = 20_000
TOLERANCE = 0.20
# A 0.5 % rate needs more draws: 20,000 give ~100 hits (sd ~10), so +/-20 % is only 2 sd. With 200,000 draws
# it is ~1,000 hits (sd ~32), so the tolerance is ~6 sd.
RARE_DRAWS = 200_000
AD_CLICK_DRAWS = 2_000


def assert_close(hits: int, expected_rate: float, draws: int = DRAWS) -> None:
    assert hits / draws == pytest.approx(expected_rate, rel=TOLERANCE)


class TestRealismRules:
    def setup_method(self) -> None:
        self.rules = RealismRules()
        self.r = random.Random(1)

    def test_duplicate_rate(self) -> None:
        received = datetime(2025, 7, 1, 10, tzinfo=UTC)
        events = [make_event(f"e{i}", received_at=received) for i in range(DRAWS)]

        duplicates = self.rules.duplicate_events(events, self.r)

        assert_close(len(duplicates), 0.01)

    def test_late_delay_rate_and_range(self) -> None:
        delays = [self.rules.late_delivery_delay(self.r) for _ in range(DRAWS)]
        late = [d for d in delays if d]

        assert_close(len(late), 0.02)
        assert all(timedelta(days=1) <= d < timedelta(days=4) for d in late)
        assert min(late) < timedelta(days=1, hours=6)
        assert max(late) > timedelta(days=3, hours=18)

    def test_email_noise_rate(self) -> None:
        email = "jane.doe@example.com"

        noisy = sum(self.rules.noisy_email(email, self.r) != email for _ in range(DRAWS))

        assert_close(noisy, 0.10)

    def test_country_noise_rate(self) -> None:
        noisy = sum(self.rules.noisy_country("GB", self.r) != "GB" for _ in range(DRAWS))

        assert_close(noisy, 0.03)

    def test_test_account_rate(self) -> None:
        hits = sum(self.rules.is_test_account(self.r) for _ in range(RARE_DRAWS))

        assert_close(hits, 0.005, RARE_DRAWS)

    def test_duplicates_share_event_id_and_received_day(self) -> None:
        received = [datetime(2025, 7, 1, 10, tzinfo=UTC), datetime(2025, 7, 1, 23, 59, 30, tzinfo=UTC)]
        events = [make_event(f"e{i}", received_at=received[i % 2]) for i in range(DRAWS)]
        by_id = {e.event_id: e for e in events}

        duplicates = self.rules.duplicate_events(events, self.r)

        assert duplicates
        for dup in duplicates:
            original = by_id[dup.event_id]
            assert dup.received_at.date() == original.received_at.date()
            assert timedelta(0) <= dup.received_at - original.received_at <= timedelta(seconds=300)
            assert dup.model_copy(update={"received_at": original.received_at}) == original

    def test_duplicates_are_clamped_to_the_end_of_the_day(self) -> None:
        late = datetime(2025, 7, 1, 23, 59, 30, tzinfo=UTC)
        events = [make_event(f"e{i}", received_at=late) for i in range(DRAWS)]

        duplicates = self.rules.duplicate_events(events, self.r)

        assert max(d.received_at for d in duplicates) == datetime(2025, 7, 1, 23, 59, 59, 999999, tzinfo=UTC)

    def test_duplicates_span_the_full_delay_range(self) -> None:
        start = datetime(2025, 7, 1, 10, tzinfo=UTC)
        events = [make_event(f"e{i}", received_at=start) for i in range(DRAWS)]

        delays = [d.received_at - start for d in self.rules.duplicate_events(events, self.r)]

        assert min(delays) < timedelta(seconds=30)
        assert timedelta(seconds=270) < max(delays) <= timedelta(seconds=300)

    def test_duplicate_events_of_empty_list_is_empty(self) -> None:
        assert self.rules.duplicate_events([], self.r) == []

    def test_noisy_email_normalizes_back(self) -> None:
        email = "jane.doe@example.com"

        variants = {self.rules.noisy_email(email, self.r) for _ in range(DRAWS)}

        assert len(variants) > 2
        assert {v.strip().lower() for v in variants} == {email}

    def test_noisy_country_variants(self) -> None:
        variants = self.rules.noisy_country_variants()

        assert set(variants) == set(RealismRules.COUNTRY_WEIGHTS)
        assert variants["DE"] == ("de",)
        assert variants["GB"] == ("gb", "UK", "uk")

    def test_noisy_country_only_emits_known_variants(self) -> None:
        seen = {self.rules.noisy_country("GB", self.r) for _ in range(DRAWS)}

        assert seen == {"GB", "gb", "UK", "uk"}

    def test_noisy_country_non_gb_never_emits_uk(self) -> None:
        seen = {self.rules.noisy_country("DE", self.r) for _ in range(DRAWS)}

        assert seen == {"DE", "de"}

    def test_ad_clicks_zero_sessions(self) -> None:
        assert self.rules.ad_clicks(0, self.r) == 0

    @pytest.mark.parametrize("sessions", [1, 10, 100, 1000])
    def test_ad_clicks_bounds(self, sessions: int) -> None:
        clicks = [self.rules.ad_clicks(sessions, self.r) for _ in range(AD_CLICK_DRAWS)]

        assert round(sessions * 0.85) <= min(clicks)
        assert max(clicks) <= round(sessions * 1.15)
        assert sum(clicks) / len(clicks) == pytest.approx(sessions, rel=0.05)
