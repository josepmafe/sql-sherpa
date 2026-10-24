from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sherpa_generator.records import RawRecord

FIRST_ID = 1


class PrimaryKeyAllocator:
    """Per-table counters persisted in simulation_state (Postgres sequences would leak on rolled-back days)."""

    def __init__(self, next_ids: Mapping[str, int]) -> None:
        self._next_ids = dict(next_ids)

    def next_id(self, record_type: type[RawRecord]) -> int:
        allocated = self._next_ids.get(record_type.TABLE, FIRST_ID)
        self._next_ids[record_type.TABLE] = allocated + 1
        return allocated

    def next_ids(self) -> dict[str, int]:
        return dict(self._next_ids)
