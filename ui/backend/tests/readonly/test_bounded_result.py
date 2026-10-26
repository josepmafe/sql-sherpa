from __future__ import annotations

from datetime import date
from decimal import Decimal

from sherpa_mcp.masking import PiiMasker
from sherpa_ui.readonly.bounded_result import BoundedResult

CUT = " …[cut at 10000 chars]"
NO_PII = PiiMasker(frozenset())
CELL = "b" * 10_000  # the longest cell kept whole


class TestBoundedResult:
    def test_values_become_json_safe_and_nulls_stay_null(self) -> None:
        result = BoundedResult(("d", "day", "n"), NO_PII, row_cap=10)

        result.extend([(Decimal("1.50"), date(2025, 3, 1), None)])

        assert result.rows == (("1.50", "2025-03-01", None),)

    def test_a_cell_over_the_cap_is_cut_with_a_visible_marker(self) -> None:
        result = BoundedResult(("at_cap", "over"), NO_PII, row_cap=10)

        result.extend([(CELL, CELL + "a")])

        assert result.rows == ((CELL, CELL + CUT),)
        assert not result.truncated

    def test_rows_that_fill_the_budget_exactly_are_all_kept(self) -> None:
        result = BoundedResult(("n",), NO_PII, row_cap=1000)

        added = result.extend([(CELL,)] * 200)  # 200 x 10,000 = 2,000,000 characters

        assert added
        assert len(result.rows) == 200
        assert not result.truncated

    def test_the_row_past_the_budget_is_left_out_and_marks_the_result_truncated(self) -> None:
        result = BoundedResult(("n",), NO_PII, row_cap=1000)
        result.extend([(CELL,)] * 200)

        added = result.extend([("x",)])

        assert not added
        assert len(result.rows) == 200
        assert result.truncated

    def test_a_first_row_over_the_budget_leaves_no_rows(self) -> None:
        columns = tuple(f"c{index}" for index in range(201))  # 201 cells of 10,000 characters: 2,010,000
        result = BoundedResult(columns, NO_PII, row_cap=1000)

        added = result.extend([(CELL,) * 201])

        assert not added
        assert result.rows == ()
        assert result.truncated

    def test_cut_cells_count_as_their_cut_length(self) -> None:
        result = BoundedResult(("n",), NO_PII, row_cap=1000)

        # Cut to 10,022 characters, 199 rows fit (1,994,378) and a 200th does not; uncut, only 20 would.
        added = result.extend([(CELL * 10,)] * 200)

        assert not added
        assert len(result.rows) == 199
        assert result.truncated

    def test_pii_is_masked_before_it_is_cut_or_counted(self) -> None:
        result = BoundedResult(("Email", "n"), PiiMasker(frozenset({"email"})), row_cap=1000)

        added = result.extend([("x" * 3_000_000, 1)])

        assert added
        assert result.rows == (("***", 1),)

    def test_the_row_past_the_cap_only_marks_the_result_truncated(self) -> None:
        result = BoundedResult(("n",), NO_PII, row_cap=2)

        added = result.extend([(1,), (2,), (3,)])

        assert not added
        assert result.rows == ((1,), (2,))
        assert result.truncated

    def test_exactly_the_cap_is_not_truncated(self) -> None:
        result = BoundedResult(("n",), NO_PII, row_cap=2)

        added = result.extend([(1,), (2,)])

        assert added
        assert not result.truncated

    def test_zero_columns(self) -> None:
        result = BoundedResult((), NO_PII, row_cap=10)

        result.extend([()])

        assert result.rows == ((),)


class TestFetchSize:
    def test_asks_for_a_batch_but_never_more_than_one_past_the_cap(self) -> None:
        result = BoundedResult(("n",), NO_PII, row_cap=120)
        first = result.fetch_size(100)
        result.extend([(value,) for value in range(100)])

        assert first == 100
        assert result.fetch_size(100) == 21

    def test_after_a_row_over_a_million_characters_it_asks_for_one_row_at_a_time(self) -> None:
        result = BoundedResult(("n",), NO_PII, row_cap=1000)
        result.extend([("a" * 1_000_000,)])
        at_the_limit = result.fetch_size(100)

        result.extend([("a" * 1_000_001,)])  # cut to 10,022 characters, but its full length counts here

        assert at_the_limit == 100
        assert result.fetch_size(100) == 1
