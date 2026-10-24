"""PII masking of query results, by column name (agent spec §4.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

MASK: Final = "***"


class PiiMasker:
    """Name-based by design: a PII column aliased to another name is not caught (spec §4.2)."""

    def __init__(self, pii_columns: frozenset[str]) -> None:
        self._pii_columns = frozenset(name.lower() for name in pii_columns)

    def mask(self, columns: Sequence[str], rows: Sequence[Sequence[object]]) -> list[list[object]]:
        masked = [column.lower() in self._pii_columns for column in columns]
        return [
            [MASK if hide and value is not None else value for hide, value in zip(masked, row, strict=True)]
            for row in rows
        ]
