from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import sys
from datetime import date

from sherpa_generator.simulation.deterministic_random import FAKER_LOCALES, DeterministicRandom, poisson, stable_int

SEED = 42
DAY = date(2025, 1, 1)
FAKER_METHODS = ("first_name", "last_name", "user_name", "free_email_domain", "city", "postcode")
PROBE = (
    "import json; from datetime import date; "
    "from sherpa_generator.simulation.deterministic_random import FAKER_LOCALES, DeterministicRandom; "
    "faker = DeterministicRandom(42).faker_for_day(date(2025, 1, 1), 'traffic'); "
    "print(json.dumps({'random': DeterministicRandom(42).for_day(date(2025, 1, 1)).random(), "
    f"'faker': [getattr(faker[loc], m)() for loc in FAKER_LOCALES for m in {FAKER_METHODS!r} for _ in range(20)]}}))"
)


def test_stable_int_is_first_eight_bytes_of_sha256() -> None:
    expected = int.from_bytes(hashlib.sha256(b"42:2025-01-01").digest()[:8], "big")

    assert stable_int(42, "2025-01-01") == expected


def test_stable_int_distinguishes_part_boundaries() -> None:
    assert stable_int("a", "bc") != stable_int("ab", "c")


class TestDeterministicRandom:
    def test_draws_match_a_subprocess_with_a_fixed_hash_seed(self) -> None:
        """Covers the day stream and every locale and provider the generator draws names, emails and addresses from
        (Spec A §5.1). The subprocess pins PYTHONHASHSEED, so a hash-dependent draw differs from this process."""
        output = subprocess.run(
            [sys.executable, "-c", PROBE],
            env={**os.environ, "PYTHONHASHSEED": "1"},
            capture_output=True,
            text=True,
            check=True,
        ).stdout

        assert json.loads(output) == _draws_in_this_process()

    def test_for_day_differs_by_day_and_seed(self) -> None:
        base = DeterministicRandom(SEED).for_day(DAY).random()

        assert DeterministicRandom(SEED).for_day(date(2025, 1, 2)).random() != base
        assert DeterministicRandom(SEED + 1).for_day(DAY).random() != base

    def test_for_day_returns_a_fresh_generator_each_call(self) -> None:
        rnd = DeterministicRandom(SEED)
        rnd.for_day(DAY).random()

        assert rnd.for_day(DAY).random() == DeterministicRandom(SEED).for_day(DAY).random()

    def test_entity_fraction_depends_on_every_part(self) -> None:
        rnd = DeterministicRandom(SEED)
        base = rnd.entity_fraction("order", 7, "cancel")

        variants = [
            rnd.entity_fraction("customer", 7, "cancel"),
            rnd.entity_fraction("order", 8, "cancel"),
            rnd.entity_fraction("order", 7, "return"),
            DeterministicRandom(SEED + 1).entity_fraction("order", 7, "cancel"),
        ]

        assert all(0.0 <= value < 1.0 for value in [base, *variants])
        assert all(value != base for value in variants)
        assert rnd.entity_fraction("order", 7, "cancel") == base

    def test_entity_fraction_is_roughly_uniform(self) -> None:
        rnd = DeterministicRandom(SEED)

        values = [rnd.entity_fraction("order", i, "x") for i in range(2000)]

        assert 0.45 < sum(values) / len(values) < 0.55

    def test_entity_fraction_stays_below_one_for_extreme_ids(self) -> None:
        rnd = DeterministicRandom(SEED)

        values = [rnd.entity_fraction("k", entity_id, "p") for entity_id in (0, 1, 2**62)]

        assert all(0.0 <= value < 1.0 for value in values)

    def test_faker_for_day_is_reproducible(self) -> None:
        rnd = DeterministicRandom(SEED)
        faker = rnd.faker_for_day(DAY)
        first = [faker.name() for _ in range(5)]

        again = rnd.faker_for_day(DAY)

        assert [again.name() for _ in range(5)] == first

    def test_reused_faker_matches_a_fresh_one(self) -> None:
        """A resumed run starts from a fresh DeterministicRandom, so the reused instance must not carry state over."""
        rnd = DeterministicRandom(SEED)
        used = rnd.faker_for_day(date(2025, 1, 2))
        for _ in range(50):
            used.name()

        reused = rnd.faker_for_day(DAY)

        fresh = DeterministicRandom(SEED).faker_for_day(DAY)
        assert [reused["de_DE"].user_name() for _ in range(5)] == [fresh["de_DE"].user_name() for _ in range(5)]

    def test_faker_for_day_differs_by_day(self) -> None:
        rnd = DeterministicRandom(SEED)
        faker = rnd.faker_for_day(DAY)
        first = [faker.name() for _ in range(5)]

        other = rnd.faker_for_day(date(2025, 1, 2))

        assert [other.name() for _ in range(5)] != first

    def test_streams_of_one_day_are_independent_and_reproducible(self) -> None:
        rnd = DeterministicRandom(SEED)

        traffic, crm = rnd.for_day(DAY, "traffic").random(), rnd.for_day(DAY, "crm").random()
        traffic_faker, crm_faker = rnd.faker_for_day(DAY, "traffic"), rnd.faker_for_day(DAY, "crm")
        traffic_names = [traffic_faker["de_DE"].user_name() for _ in range(5)]
        crm_names = [crm_faker["de_DE"].user_name() for _ in range(5)]

        assert traffic != crm
        assert traffic != rnd.for_day(DAY).random()
        assert traffic == DeterministicRandom(SEED).for_day(DAY, "traffic").random()
        assert traffic_names != crm_names


def test_poisson_mean_and_variance_match_the_rate() -> None:
    r = random.Random(1)

    draws = [poisson(r, 80.0) for _ in range(4000)]
    mean = sum(draws) / len(draws)
    variance = sum((d - mean) ** 2 for d in draws) / len(draws)

    assert 78.0 < mean < 82.0
    assert 72.0 < variance < 88.0


def test_poisson_of_large_mean_does_not_underflow() -> None:
    r = random.Random(1)

    draws = [poisson(r, 1500.0) for _ in range(200)]

    assert 1480 < sum(draws) / len(draws) < 1520


def test_poisson_of_zero_mean_is_zero() -> None:
    assert poisson(random.Random(1), 0.0) == 0


def _draws_in_this_process() -> dict[str, object]:
    faker = DeterministicRandom(SEED).faker_for_day(DAY, "traffic")
    return {
        "random": DeterministicRandom(SEED).for_day(DAY).random(),
        "faker": [getattr(faker[loc], m)() for loc in FAKER_LOCALES for m in FAKER_METHODS for _ in range(20)],
    }
