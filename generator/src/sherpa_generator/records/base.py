from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Annotated, ClassVar

from pydantic import AfterValidator, BaseModel, ConfigDict

MONEY_QUANTUM = Decimal("0.01")  # Spec A §4: numeric(12,2), ROUND_HALF_UP


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


Money = Annotated[Decimal, AfterValidator(quantize_money)]


class RawRecord(BaseModel):
    """One row of a raw table. Field order is the column order of the table."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    TABLE: ClassVar[str]
    KEY: ClassVar[tuple[str, ...]] = ("id",)

    @classmethod
    def columns(cls) -> tuple[str, ...]:
        return tuple(cls.model_fields)

    def copy_values(self) -> tuple[object, ...]:
        """Field values in column order, ready for COPY: enums as plain values, nested models as jsonb dicts."""
        return tuple(self._copy_value(getattr(self, name)) for name in self.columns())

    @staticmethod
    def _copy_value(value: object) -> object:
        if isinstance(value, BaseModel):
            return value.model_dump(mode="json", exclude_none=True)
        if isinstance(value, Enum):
            return value.value
        return value
