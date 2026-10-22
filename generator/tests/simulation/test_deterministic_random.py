from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import date

from sherpa_generator.simulation.deterministic_random import DeterministicRandom, stable_int

SEED = 42
DAY = date(2025, 1, 1)


def test_stable_int_is_first_eight_bytes_of_sha256() -> None:
    expected = int.from_bytes(hashlib.sha256(b"42:2025-01-01").digest()[:8], "big")

    assert stable_int(42, "2025-01-01") == expected


def test_stable_int_distinguishes_part_boundaries() -> None:
    assert stable_int("a", "bc") != stable_int("ab", "c")


class TestDeterministicRandom:
    def test_for_day_is_stable_across_processes(self) -> None:
        expected = DeterministicRandom(SEED).for_day(DAY).random()
        code = (
            "from datetime import date; "
            "from sherpa_generator.simulation.deterministic_random import DeterministicRandom; "
            "print(repr(DeterministicRandom(42).for_day(date(2025, 1, 1)).random()))"
        )

        results = [
            float(
                subprocess.run(
                    [sys.executable, "-c", code],
                    env={**os.environ, "PYTHONHASHSEED": hashseed},
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout
            )
            for hashseed in ("1", "2")
        ]

        assert results == [expected, expected]

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
        faker_a, faker_b = rnd.faker_for_day(DAY), rnd.faker_for_day(DAY)

        assert [faker_a.name() for _ in range(5)] == [faker_b.name() for _ in range(5)]

    def test_faker_for_day_differs_by_day(self) -> None:
        rnd = DeterministicRandom(SEED)
        faker_a, faker_b = rnd.faker_for_day(DAY), rnd.faker_for_day(date(2025, 1, 2))

        assert [faker_a.name() for _ in range(5)] != [faker_b.name() for _ in range(5)]
