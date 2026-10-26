"""The rows of one result as the UI returns them (UI spec §5): JSON-safe, PII-masked, each cell cut like the MCP
server's, and no more than the row cap and the result's character budget allow."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from sherpa_mcp.database import MAX_CELL_CHARS, cap_cell, cell_chars, json_safe

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sherpa_mcp.masking import PiiMasker

# UI spec §5. All cells of one result together, after cutting each to sherpa_mcp's MAX_CELL_CHARS (the length the
# guard lets lpad build); about eight times the widest mart's 1,000 rows (fct_orders, ~250,000 characters).
MAX_RESULT_CHARS: Final = 2_000_000
# After a row this long before cutting, rows are fetched one at a time, so at most one such row is in memory.
LARGE_ROW_CHARS: Final = 1_000_000


class BoundedResult:
    """Collects fetched rows until the row cap or `MAX_RESULT_CHARS` is reached; the row that does not fit marks the
    result `truncated` and is left out, with every row after it."""

    def __init__(self, columns: Sequence[str], masker: PiiMasker, row_cap: int) -> None:
        self._columns = columns
        self._masker = masker
        self._row_cap = row_cap
        self._rows: list[tuple[object, ...]] = []
        self._chars = 0
        self._truncated = False
        self._large_row_seen = False

    @property
    def rows(self) -> tuple[tuple[object, ...], ...]:
        return tuple(self._rows)

    @property
    def truncated(self) -> bool:
        return self._truncated

    def extend(self, batch: Sequence[Sequence[object]]) -> bool:
        """Add a batch of fetched rows in order; False once one does not fit, when the caller stops fetching."""
        # Masked first, so a personal value is never measured, cut or counted.
        masked = self._masker.mask(self._columns, [[json_safe(value) for value in raw] for raw in batch])
        for row in masked:
            # A row past the cap only tells that there were more.
            if len(self._rows) == self._row_cap or not self._append(row):
                self._truncated = True
                return False

        return True

    def fetch_size(self, batch_rows: int) -> int:
        """How many rows the next FETCH asks for: never more than one past the cap, one at a time after a large row."""
        if self._large_row_seen:
            return 1

        return min(batch_rows, self._row_cap + 1 - len(self._rows))

    def _append(self, row: Sequence[object]) -> bool:
        """Cut the row's cells and keep it if it fits the budget; notes a large row either way."""
        cut: list[object] = []
        full_chars = row_chars = 0
        for value in row:
            chars = cell_chars(value)
            full_chars += chars
            cell = value
            if chars > MAX_CELL_CHARS:  # measured once; only a long cell is cut and measured again
                cell = cap_cell(value)
                chars = cell_chars(cell)

            row_chars += chars
            cut.append(cell)

        if full_chars > LARGE_ROW_CHARS:
            self._large_row_seen = True

        if self._chars + row_chars > MAX_RESULT_CHARS:
            return False

        self._chars += row_chars
        self._rows.append(tuple(cut))
        return True
