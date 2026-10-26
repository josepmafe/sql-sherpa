from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError
from sherpa_mcp.masking import PiiMasker
from sherpa_ui.readonly.sort_filter import (
    DuplicateColumnsError,
    FilterSpec,
    PiiColumnError,
    SortSpec,
    UnknownColumnError,
    build_wrapped_query,
    strip_terminators,
)

if TYPE_CHECKING:
    from sherpa_ui.readonly.sort_filter import WrappedQuery

NO_PII = PiiMasker(frozenset())
PII = PiiMasker(frozenset({"email", "first_name"}))


def test_wrapper_strips_semicolon_and_survives_trailing_comment() -> None:
    wrapped = build_wrapped_query(
        "select 1 as a -- note\n;\n  ", ["a"], NO_PII, sort=[SortSpec(column="a", direction="asc")]
    )

    # The newline before `)` ends the comment, so the closing parenthesis and the order by stay outside it.
    assert _text(wrapped) == 'select * from (\nselect 1 as a -- note\n) as q order by q."a" asc'
    assert wrapped.params == ()


def test_semicolon_before_a_trailing_comment_is_blanked() -> None:
    wrapped = build_wrapped_query("select 1 as a; -- note", ["a"], NO_PII)

    assert _text(wrapped) == "select * from (\nselect 1 as a  -- note\n) as q"


def test_unknown_column_rejected() -> None:
    with pytest.raises(UnknownColumnError, match="column 'b' is not a column of the result"):
        build_wrapped_query("select 1 as a", ["a"], NO_PII, sort=[SortSpec(column="b", direction="asc")])

    with pytest.raises(UnknownColumnError, match="column 'b' is not a column of the result"):
        build_wrapped_query("select 1 as a", ["a"], NO_PII, filters=[FilterSpec(column="b", operator="is_null")])


def test_column_names_match_exactly() -> None:
    # Postgres keeps a quoted "A" apart from a; the result's names are what it reported.
    with pytest.raises(UnknownColumnError, match="column 'a' is not a column of the result"):
        build_wrapped_query('select 1 as "A"', ["A"], NO_PII, sort=[SortSpec(column="a", direction="asc")])


def test_zero_result_columns_accept_no_sort() -> None:
    with pytest.raises(UnknownColumnError, match="column 'a' is not a column of the result"):
        build_wrapped_query("select from marts.fct_orders", [], NO_PII, sort=[SortSpec(column="a", direction="asc")])


@pytest.mark.parametrize(
    ("result_columns", "sort", "filters"),
    [
        (["customer_id", "email"], [SortSpec(column="email", direction="asc")], []),
        (["customer_id", "email"], [], [FilterSpec(column="email", operator="contains", value="@hotmail")]),
        (["customer_id", "EMAIL"], [], [FilterSpec(column="EMAIL", operator="is_null")]),  # masked as well
        (
            ["customer_id", "first_name"],
            [SortSpec(column="customer_id", direction="asc")],
            [FilterSpec(column="first_name", operator="=", value="Ann")],
        ),
    ],
    ids=["sort", "contains", "quoted_upper_case", "filter_next_to_a_sort"],
)
def test_masked_columns_are_neither_sorted_nor_filtered(
    result_columns: list[str], sort: list[SortSpec], filters: list[FilterSpec]
) -> None:
    # The hidden values would still decide the order and which rows match: a `contains` filter is an oracle.
    column = [*sort, *filters][-1].column

    with pytest.raises(PiiColumnError) as error:
        build_wrapped_query("select 1", result_columns, PII, sort=sort, filters=filters)

    assert str(error.value) == (
        f"column {column!r} holds personal data, shown as ***; the server does not sort or filter by it, since the "
        "order or the matching rows would reveal the hidden values"
    )


def test_columns_the_masking_leaves_alone_can_be_sorted_and_filtered() -> None:
    # By result-column name, exactly like the masking: an alias is not masked, so nothing is hidden to reveal.
    wrapped = build_wrapped_query(
        "select email as e, customer_id from marts.dim_customers",
        ["e", "customer_id"],
        PII,
        sort=[SortSpec(column="e", direction="asc")],
        filters=[FilterSpec(column="customer_id", operator=">", value="1")],
    )

    assert _text(wrapped).endswith(' where q."customer_id" > %s order by q."e" asc')


def test_an_unknown_column_is_reported_before_a_pii_one() -> None:
    with pytest.raises(UnknownColumnError, match="column 'email' is not a column of the result"):
        build_wrapped_query("select 1 as a", ["a"], PII, sort=[SortSpec(column="email", direction="asc")])


def test_contains_is_a_bound_parameter() -> None:
    wrapped = build_wrapped_query(
        "select 'x' as name",
        ["name"],
        NO_PII,
        filters=[FilterSpec(column="name", operator="contains", value="o'brien; drop")],
    )

    # `%%` is psycopg's escape for a literal `%` once the query has parameters.
    assert (
        _text(wrapped) == "select * from (\nselect 'x' as name\n) as q where q.\"name\"::text ilike '%%' || %s || '%%'"
    )
    assert wrapped.params == ("o'brien; drop",)


def test_contains_matches_wildcards_literally() -> None:
    wrapped = build_wrapped_query(
        "select 'x' as name",
        ["name"],
        NO_PII,
        filters=[FilterSpec(column="name", operator="contains", value="50%_off\\x")],
    )

    assert wrapped.params == ("50\\%\\_off\\\\x",)


@pytest.mark.parametrize(
    ("operator", "rendered"), [("=", "="), ("!=", "<>"), ("<", "<"), ("<=", "<="), (">", ">"), (">=", ">=")]
)
def test_comparisons_bind_their_value(operator: str, rendered: str) -> None:
    wrapped = build_wrapped_query(
        "select 1 as a",
        ["a"],
        NO_PII,
        filters=[FilterSpec(column="a", operator=operator, value="7")],  # type: ignore[arg-type]
    )

    assert _text(wrapped) == f'select * from (\nselect 1 as a\n) as q where q."a" {rendered} %s'
    assert wrapped.params == ("7",)


def test_filters_and_sorts_combine_in_order() -> None:
    wrapped = build_wrapped_query(
        "select 1 as a, 2 as b",
        ["a", "b"],
        NO_PII,
        sort=[SortSpec(column="b", direction="desc"), SortSpec(column="a", direction="asc")],
        filters=[
            FilterSpec(column="a", operator=">=", value="1"),
            FilterSpec(column="b", operator="is_not_null"),
            FilterSpec(column="b", operator="is_null"),
            FilterSpec(column="a", operator="!=", value="3"),
        ],
    )

    assert _text(wrapped) == (
        "select * from (\nselect 1 as a, 2 as b\n) as q"
        ' where q."a" >= %s and q."b" is not null and q."b" is null and q."a" <> %s'
        ' order by q."b" desc, q."a" asc'
    )
    assert wrapped.params == ("1", "3")


def test_quotes_in_column_names_are_escaped() -> None:
    wrapped = build_wrapped_query('select 1 as "a""b"', ['a"b'], NO_PII, sort=[SortSpec(column='a"b', direction="asc")])

    assert _text(wrapped).endswith(' order by q."a""b" asc')


def test_percent_signs_in_user_sql_are_doubled() -> None:
    # The wrapped query always runs with its parameters, so psycopg reads every `%` in it as a placeholder.
    wrapped = build_wrapped_query("select 5 % 3 as m, '%s' as t", ["m", "t"], NO_PII)

    assert _text(wrapped) == "select * from (\nselect 5 %% 3 as m, '%%s' as t\n) as q"
    assert wrapped.prefix_length == len("select * from (\n")


def test_duplicate_columns_raise() -> None:
    with pytest.raises(DuplicateColumnsError, match="duplicate column names: a, b"):
        build_wrapped_query(
            "select 1 as a, 2 as a, 3 as b, 4 as b, 5 as c",
            ["a", "a", "b", "b", "c"],
            NO_PII,
            sort=[SortSpec(column="c", direction="asc")],
        )


def test_prefix_length_matches_text_before_user_sql() -> None:
    sql_text = "select order_id from marts.fct_orders"

    wrapped = build_wrapped_query(
        sql_text, ["order_id"], NO_PII, filters=[FilterSpec(column="order_id", operator=">", value="1")]
    )

    text, prefix = _text(wrapped), wrapped.prefix_length
    assert prefix == 16
    assert text[:prefix] == "select * from (\n"
    assert text[prefix : prefix + len(sql_text)] == sql_text


class TestFilterSpec:
    def test_comparison_needs_a_value(self) -> None:
        with pytest.raises(ValidationError, match="operator = needs a value"):
            FilterSpec(column="a", operator="=")

    def test_null_checks_take_no_value(self) -> None:
        with pytest.raises(ValidationError, match="operator is_null takes no value"):
            FilterSpec(column="a", operator="is_null", value="x")

    def test_empty_value_is_a_value(self) -> None:
        spec = FilterSpec(column="a", operator="contains", value="")

        assert spec.value == ""


@pytest.mark.parametrize(
    ("sql_text", "expected"),
    [
        ("select 1;", "select 1"),
        ("select 1 ; ;\n\t", "select 1"),
        ("select 1; -- done", "select 1  -- done"),
        ("select 1; /* done */ ", "select 1  /* done */"),
        ("select 1 -- done\n;", "select 1 -- done"),
        ("select ';'", "select ';'"),
        ("select 'é😀';", "select 'é😀'"),
        ("  select 1", "  select 1"),
        ("", ""),
        ("  \n", ""),
    ],
)
def test_strip_terminators_keeps_every_character_in_place(sql_text: str, expected: str) -> None:
    stripped = strip_terminators(sql_text)

    assert stripped == expected
    # Error positions are counted in the stripped text, so it must be the original with only `;` blanked.
    kept_part = sql_text[: len(stripped)]
    assert all(
        kept == original or (original, kept) == (";", " ") for kept, original in zip(stripped, kept_part, strict=True)
    )


def _text(wrapped: WrappedQuery) -> str:
    return wrapped.query.as_string()
