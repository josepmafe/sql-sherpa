from __future__ import annotations

import csv
from pathlib import Path

from sherpa_generator.simulation.realism_rules import RealismRules

SEEDS_DIR = Path(__file__).parents[2] / "dbt" / "seeds"


def test_every_noisy_country_variant_is_in_seed() -> None:
    rules = RealismRules()
    expected = set(rules.COUNTRY_WEIGHTS)
    expected |= {variant for variants in rules.noisy_country_variants().values() for variant in variants}

    mapping = _read_country_seed()

    assert expected <= mapping.keys()


def test_country_seed_maps_every_variant_back_to_its_canonical_code() -> None:
    rules = RealismRules()

    mapping = _read_country_seed()

    for code, variants in rules.noisy_country_variants().items():
        assert mapping[code] == code
        assert {mapping[variant] for variant in variants} == {code}


def _read_country_seed() -> dict[str, str]:
    with (SEEDS_DIR / "country_codes.csv").open(newline="", encoding="utf-8") as f:
        return {row["raw_value"]: row["code"] for row in csv.DictReader(f)}
