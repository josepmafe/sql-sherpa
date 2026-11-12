from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from sherpa_agent.outcomes import ModelUsage
from sherpa_agent.prices import PRICES_PATH, Prices

if TYPE_CHECKING:
    from pathlib import Path

PRICES = Prices.model_validate({"models": {"m-priced": {"input_eur_per_mtok": "2", "output_eur_per_mtok": "10"}}})


class TestPrices:
    def test_cost_is_tokens_times_price_per_million(self) -> None:
        usage = (ModelUsage(model="m-priced", input_tokens=500_000, output_tokens=100_000),)

        assert PRICES.cost_eur(usage) == Decimal("2.0")  # 0.5 * 2 + 0.1 * 10

    def test_costs_of_several_models_add_up(self) -> None:
        usage = (
            ModelUsage(model="m-priced", input_tokens=1_000_000, output_tokens=0),
            ModelUsage(model="m-priced", input_tokens=0, output_tokens=1_000_000),
        )

        assert PRICES.cost_eur(usage) == Decimal(12)

    def test_a_model_without_a_price_makes_the_cost_none(self) -> None:
        usage = (
            ModelUsage(model="m-priced", input_tokens=1, output_tokens=1),
            ModelUsage(model="m-unknown", input_tokens=1, output_tokens=1),
        )

        assert PRICES.cost_eur(usage) is None
        assert PRICES.unpriced(usage) == ("m-unknown",)

    def test_no_usage_costs_nothing(self) -> None:
        assert PRICES.cost_eur(()) == Decimal(0)

    def test_the_shipped_price_file_prices_the_three_models(self) -> None:
        prices = Prices.load(PRICES_PATH)

        assert set(prices.models) == {
            "eu.anthropic.claude-sonnet-5-5",
            "eu.anthropic.claude-haiku-5-5",
            "eu.anthropic.claude-opus-5-5",
        }

    def test_a_price_file_missing_a_field_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "prices.yaml"
        path.write_text("models:\n  m:\n    input_eur_per_mtok: 1\n", encoding="utf-8")

        with pytest.raises(ValueError, match="output_eur_per_mtok"):
            Prices.load(path)
