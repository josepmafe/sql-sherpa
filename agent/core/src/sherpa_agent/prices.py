"""Model prices (agent spec §7.3), shared by the agent and the eval report."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Final

import yaml
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sherpa_agent.outcomes import ModelUsage

PRICES_PATH: Final = Path(__file__).resolve().parents[2] / "prices.yaml"
TOKENS_PER_MTOK: Final = Decimal(1_000_000)


class ModelPrice(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_eur_per_mtok: Decimal
    output_eur_per_mtok: Decimal


class Prices(BaseModel):
    model_config = ConfigDict(frozen=True)

    models: dict[str, ModelPrice]

    @classmethod
    def load(cls, path: Path) -> Prices:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))

    def cost_eur(self, usage: Sequence[ModelUsage]) -> Decimal | None:
        """`None` when a model has no price: an unknown cost is not zero."""
        if self.unpriced(usage):
            return None
        total = Decimal(0)
        for item in usage:
            price = self.models[item.model]
            total += item.input_tokens * price.input_eur_per_mtok + item.output_tokens * price.output_eur_per_mtok
        return total / TOKENS_PER_MTOK

    def unpriced(self, usage: Sequence[ModelUsage]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.model for item in usage if item.model not in self.models))
