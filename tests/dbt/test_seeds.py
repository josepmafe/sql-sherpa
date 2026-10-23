from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from sherpa_generator.behaviour.visitor_traffic import UTM_SOURCES_AND_MEDIUM
from sherpa_generator.simulation.realism_rules import RealismRules

SEEDS_DIR = Path(__file__).parents[2] / "dbt" / "seeds"


def test_every_noisy_country_variant_is_in_seed() -> None:
    rules = RealismRules()
    expected = set(rules.COUNTRY_WEIGHTS)
    expected |= {variant for variants in rules.noisy_country_variants().values() for variant in variants}

    mapping = _read_variants_seed()

    assert expected <= mapping.keys()


def test_country_seed_maps_every_variant_back_to_its_canonical_code() -> None:
    rules = RealismRules()

    mapping = _read_variants_seed()

    for code, variants in rules.noisy_country_variants().items():
        assert mapping[code] == code
        assert {mapping[variant] for variant in variants} == {code}


def test_countries_seed_has_exactly_one_row_per_canonical_code() -> None:
    rules = RealismRules()

    codes = Counter(row["code"] for row in _read_seed("countries"))

    assert codes == dict.fromkeys(rules.COUNTRY_WEIGHTS, 1)


def test_every_variant_maps_to_a_code_in_countries() -> None:
    countries = {row["code"] for row in _read_seed("countries")}

    mapping = _read_variants_seed()

    assert set(mapping.values()) <= countries


def test_channel_mapping_seed_is_exactly_the_generator_utm_pairs() -> None:
    expected = {
        (source, medium): str(channel)
        for channel, (sources, medium) in UTM_SOURCES_AND_MEDIUM.items()
        for source in sources
    }

    rows = _read_seed("channel_mapping")

    assert {(row["utm_source"], row["utm_medium"]): row["marketing_channel"] for row in rows} == expected
    assert len(rows) == len(expected)


def _read_variants_seed() -> dict[str, str]:
    return {row["raw_value"]: row["code"] for row in _read_seed("country_code_variants")}


def _read_seed(name: str) -> list[dict[str, str]]:
    with (SEEDS_DIR / f"{name}.csv").open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
