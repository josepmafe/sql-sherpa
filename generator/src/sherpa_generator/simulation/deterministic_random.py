from __future__ import annotations

import hashlib
import random
from typing import TYPE_CHECKING

from faker import Faker

if TYPE_CHECKING:
    from datetime import date

# Spec A §5.1: stable integers come from sha256, never from the per-process built-in hash().
FAKER_LOCALES = ("de_DE", "fr_FR", "es_ES", "it_IT", "nl_NL", "en_GB", "de_AT", "nl_BE")
_STABLE_INT_BYTES = 8
_FRACTION_BITS = 53  # a float64 mantissa: keeps the fraction strictly below 1.0


def stable_int(*parts: object) -> int:
    digest = hashlib.sha256(":".join(map(str, parts)).encode()).digest()
    return int.from_bytes(digest[:_STABLE_INT_BYTES], "big")


class DeterministicRandom:
    """All randomness derives from the seed, so two runs (or a resumed run) see the same numbers."""

    def __init__(self, seed: int) -> None:
        self._seed = seed

    def for_day(self, day: date) -> random.Random:
        return random.Random(stable_int(self._seed, day.isoformat()))

    def faker_for_day(self, day: date) -> Faker:
        faker = Faker(list(FAKER_LOCALES))
        faker.seed_instance(stable_int(self._seed, "faker", day.isoformat()))
        return faker

    def entity_fraction(self, kind: str, entity_id: int, purpose: str) -> float:
        """A value in [0, 1) fixed per (entity, purpose), for decisions about an entity's future.

        Re-derived identically after a resume, unlike a draw from the day's RNG stream.
        """
        bits = stable_int(self._seed, kind, entity_id, purpose) >> (_STABLE_INT_BYTES * 8 - _FRACTION_BITS)
        return bits / (1 << _FRACTION_BITS)
