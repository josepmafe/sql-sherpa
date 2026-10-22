from __future__ import annotations

import copy
import hashlib
import math
import random
from typing import TYPE_CHECKING

from faker import Faker

if TYPE_CHECKING:
    from datetime import date

# Spec A §5.1: stable integers come from sha256, never from the per-process built-in hash().
FAKER_LOCALES = ("de_DE", "fr_FR", "es_ES", "it_IT", "nl_NL", "en_GB", "de_AT", "nl_BE")
POISSON_CHUNK_MEAN = 30.0  # Knuth's method needs exp(-mean), which underflows for large means
_STABLE_INT_BYTES = 8
_FRACTION_BITS = 53  # a float64 mantissa: keeps the fraction strictly below 1.0


def stable_int(*parts: object) -> int:
    digest = hashlib.sha256(":".join(map(str, parts)).encode()).digest()
    return int.from_bytes(digest[:_STABLE_INT_BYTES], "big")


def poisson(r: random.Random, mean: float) -> int:
    """A Poisson draw. Large means are split into chunks, which is exact because Poisson variables add up."""
    count = 0
    remaining = mean
    while remaining > 0:
        chunk = min(remaining, POISSON_CHUNK_MEAN)
        remaining -= chunk
        count += _knuth_poisson(r, chunk)
    return count


class DeterministicRandom:
    """All randomness derives from the seed, so two runs (or a resumed run) see the same numbers.

    Each simulator asks for its own named stream, so two simulators never consume the same sequence.
    """

    def __init__(self, seed: int) -> None:
        self._seed = seed
        self._faker_template: Faker | None = None

    def for_day(self, day: date, stream: str | None = None) -> random.Random:
        return random.Random(stable_int(*self._with_stream((self._seed, day.isoformat()), stream)))

    def faker_for_day(self, day: date, stream: str | None = None) -> Faker:
        # Building a Faker scans its provider modules (~90 ms); a copy of an unused template is ~20x cheaper.
        if self._faker_template is None:
            self._faker_template = Faker(list(FAKER_LOCALES))

        faker = copy.deepcopy(self._faker_template)
        faker.seed_instance(stable_int(*self._with_stream((self._seed, "faker", day.isoformat()), stream)))
        return faker

    def entity_fraction(self, kind: str, entity_id: int, purpose: str) -> float:
        """A value in [0, 1) fixed per (entity, purpose), for decisions about an entity's future.

        Re-derived identically after a resume, unlike a draw from the day's RNG stream.
        """
        bits = stable_int(self._seed, kind, entity_id, purpose) >> (_STABLE_INT_BYTES * 8 - _FRACTION_BITS)
        return bits / (1 << _FRACTION_BITS)

    @staticmethod
    def _with_stream(parts: tuple[object, ...], stream: str | None) -> tuple[object, ...]:
        """Without a stream the parts are the Spec A §5.1 day seed `{seed}:{day}` unchanged."""
        return parts if stream is None else (*parts, stream)


def _knuth_poisson(r: random.Random, mean: float) -> int:
    threshold = math.exp(-mean)
    count = 0
    product = r.random()
    while product > threshold:
        count += 1
        product *= r.random()
    return count
