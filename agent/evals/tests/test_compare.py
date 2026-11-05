from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sherpa_evals import compare
from sherpa_evals.compare import Comparison, Result, normalize

if TYPE_CHECKING:
    import pytest


def result(columns: tuple[str, ...], *rows: tuple[Any, ...]) -> Result:
    return Result(columns=columns, rows=rows)


class TestNormalize:
    def test_equal_numbers_of_any_type_normalise_alike(self) -> None:
        values = [Decimal("1"), 1, 1.0, Decimal("1.004")]

        normalised = {normalize(value) for value in values}

        assert normalised == {Decimal("1.00")}

    def test_half_rounds_up(self) -> None:
        assert normalize(Decimal("1.005")) == Decimal("1.01")

    def test_negative_zero_becomes_zero(self) -> None:
        normalised = normalize(-0.0001)

        assert normalised == Decimal("0.00")
        assert str(normalised) == "0.00"

    def test_float_and_decimal_third_match(self) -> None:
        assert normalize(1 / 3) == normalize(Decimal("0.3333"))

    def test_none_is_not_zero(self) -> None:
        assert normalize(None) is None
        assert normalize(None) != normalize(Decimal("0"))

    def test_bool_stays_a_bool(self) -> None:
        assert normalize(True) is True
        assert normalize(False) is False

    def test_nan_does_not_raise(self) -> None:
        assert normalize(float("nan")) == "nan"

    def test_date_is_iso(self) -> None:
        assert normalize(date(2025, 12, 31)) == "2025-12-31"

    def test_aware_datetime_is_converted_to_utc(self) -> None:
        value = datetime(2025, 3, 1, 10, 0, tzinfo=timezone(timedelta(hours=1)))

        assert normalize(value) == "2025-03-01T09:00:00+00:00"

    def test_naive_datetime_is_kept(self) -> None:
        assert normalize(datetime(2025, 3, 1, 10, 0)) == "2025-03-01T10:00:00"

    def test_aware_midnight_utc_normalises_like_its_date(self) -> None:
        assert normalize(datetime(2025, 3, 1, tzinfo=UTC)) == normalize(date(2025, 3, 1))

    def test_naive_midnight_normalises_like_its_date(self) -> None:
        assert normalize(datetime(2025, 3, 1)) == normalize(date(2025, 3, 1))

    def test_non_midnight_datetime_stays_distinct_from_its_date(self) -> None:
        assert normalize(datetime(2025, 3, 1, 22, 0, tzinfo=UTC)) != normalize(date(2025, 3, 1))
        assert normalize(datetime(2025, 3, 1, 0, 0, 1, tzinfo=UTC)) != normalize(date(2025, 3, 1))

    def test_midnight_in_another_zone_converts_first_and_stays_a_datetime(self) -> None:
        value = datetime(2025, 3, 1, tzinfo=timezone(timedelta(hours=1)))

        assert normalize(value) == "2025-02-28T23:00:00+00:00"

    def test_text_is_case_sensitive(self) -> None:
        assert normalize("ABC") != normalize("abc")


class TestCompare:
    def test_column_permutation_passes(self) -> None:
        gold = result(("k", "v"), ("a", 1), ("b", 2))
        agent = result(("x", "y"), (1, "a"), (2, "b"))

        assert compare.compare(gold, agent, ordered=False) == Comparison(passed=True, diff=None)

    def test_extra_agent_column_passes(self) -> None:
        gold = result(("id",), (1,), (2,))
        agent = result(("id", "name"), (1, "ann"), (2, "bob"))

        assert compare.compare(gold, agent, ordered=False).passed

    def test_missing_column_fails_with_its_name(self) -> None:
        gold = result(("id", "revenue"), (1, 10), (2, 20))
        agent = result(("id",), (1,), (2,))

        outcome = compare.compare(gold, agent, ordered=False)

        assert outcome == Comparison(passed=False, diff="no agent column matches gold column revenue")

    def test_unordered_rows_in_another_order_pass(self) -> None:
        gold = result(("k", "v"), ("a", 1), ("b", 2))
        agent = result(("k", "v"), ("b", 2), ("a", 1))

        assert compare.compare(gold, agent, ordered=False).passed

    def test_ordered_rows_in_another_order_fail(self) -> None:
        gold = result(("k", "v"), ("a", 1), ("b", 2))
        agent = result(("k", "v"), ("b", 2), ("a", 1))

        outcome = compare.compare(gold, agent, ordered=True)

        assert outcome.passed is False
        assert outcome.diff == "no column mapping matches the gold rows as whole rows"

    def test_ordered_rows_in_the_same_order_pass(self) -> None:
        gold = result(("k", "v"), ("a", 1), ("b", 2))
        agent = result(("k", "v"), ("a", 1), ("b", 2))

        assert compare.compare(gold, agent, ordered=True).passed

    def test_duplicate_rows_count(self) -> None:
        gold = result(("k", "v"), ("a", 1), ("b", 2))
        agent = result(("k", "v"), ("a", 1), ("a", 1))

        assert not compare.compare(gold, agent, ordered=False).passed

    def test_different_row_counts_fail(self) -> None:
        gold = result(("k",), ("a",), ("b",))
        agent = result(("k",), ("a",), ("b",), ("c",))

        outcome = compare.compare(gold, agent, ordered=False)

        assert outcome == Comparison(passed=False, diff="gold has 2 rows, the agent 3")

    def test_nulls_in_the_same_places_pass(self) -> None:
        gold = result(("k", "v"), ("a", None), ("b", 2))
        agent = result(("k", "v"), ("a", None), ("b", 2))

        assert compare.compare(gold, agent, ordered=False).passed

    def test_null_against_zero_fails(self) -> None:
        gold = result(("v",), (None,))
        agent = result(("v",), (0,))

        assert not compare.compare(gold, agent, ordered=False).passed

    def test_swapped_values_across_keys_fail(self) -> None:
        gold = result(("country", "n"), ("DE", 10), ("FR", 20))
        agent = result(("country", "n"), ("DE", 20), ("FR", 10))

        outcome = compare.compare(gold, agent, ordered=False)

        assert outcome == Comparison(passed=False, diff="no column mapping matches the gold rows as whole rows")

    def test_the_agent_column_that_makes_whole_rows_match_is_found(self) -> None:
        gold = result(("k", "v"), ("a", 1), ("b", 2))
        agent = result(("k", "wrong", "right"), ("a", 2, 1), ("b", 1, 2))

        assert compare.compare(gold, agent, ordered=False).passed

    def test_zero_rows_against_zero_rows_pass(self) -> None:
        gold = result(("k", "v"))
        agent = result(("a", "b", "c"))

        assert compare.compare(gold, agent, ordered=False).passed

    def test_zero_rows_with_too_few_agent_columns_fail(self) -> None:
        gold = result(("k", "v"))
        agent = result(("a",))

        assert not compare.compare(gold, agent, ordered=False).passed

    def test_mapping_bound_is_enforced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Gold columns are cyclic shifts of {1, 2, 3}; the agent lists them reversed, so only the
        # last of the 6 assignments (2, 1, 0) reproduces the gold rows in order.
        gold = result(("g1", "g2", "g3"), (1, 2, 3), (2, 3, 1), (3, 1, 2))
        agent = result(("a", "b", "c"), (3, 2, 1), (1, 3, 2), (2, 1, 3))
        monkeypatch.setattr(compare, "MAX_MAPPINGS", 2)

        bounded = compare.compare(gold, agent, ordered=True)
        monkeypatch.undo()
        unbounded = compare.compare(gold, agent, ordered=True)

        assert bounded == Comparison(passed=False, diff="too many candidate column mappings")
        assert unbounded.passed

    def test_zero_rows_with_one_column_too_few_fail_without_searching(self) -> None:
        gold = result(tuple(f"g{i}" for i in range(12)))
        agent = result(tuple(f"a{i}" for i in range(11)))

        outcome = compare.compare(gold, agent, ordered=False)

        assert outcome == Comparison(passed=False, diff="no column mapping matches the gold rows as whole rows")

    def test_dead_end_search_is_bounded(self) -> None:
        gold = result(tuple(f"g{i}" for i in range(10)), (1,) * 10)
        agent = result(tuple(f"a{i}" for i in range(10)), (*(1,) * 9, 2))

        outcome = compare.compare(gold, agent, ordered=False)

        assert outcome == Comparison(passed=False, diff="too many candidate column mappings")


def test_utc_datetime_matches_the_same_instant_in_another_zone() -> None:
    plus_one = datetime(2025, 3, 1, 10, 0, tzinfo=timezone(timedelta(hours=1)))

    assert normalize(plus_one) == normalize(datetime(2025, 3, 1, 9, 0, tzinfo=UTC))


def test_gold_date_column_matches_an_agent_midnight_timestamptz_column() -> None:
    gold = result(("month",), (date(2025, 1, 1),), (date(2025, 2, 1),))
    agent = result(("month",), (datetime(2025, 1, 1, tzinfo=UTC),), (datetime(2025, 2, 1, tzinfo=UTC),))

    assert compare.compare(gold, agent, ordered=True) == Comparison(passed=True)


def test_boolean_gold_column_matches_a_zero_one_agent_column() -> None:
    gold = result(("flag",), (True,), (False,))
    agent = result(("flag",), (1,), (0,))

    assert compare.compare(gold, agent, ordered=False) == Comparison(passed=True)
