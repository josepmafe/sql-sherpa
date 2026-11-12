from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest
from sherpa_agent.guards import check_draft_sql, check_execution_reference, numbers_message, unverified_numbers
from sherpa_agent.recording import Execution

if TYPE_CHECKING:
    from pydantic import JsonValue

AS_OF = date(2025, 6, 30)
HASH_CELL = "9be4a3f9c1d7e2203021"
QUESTION = "How did net revenue develop over the top 10 months?"
TIME_RANGE = ("2025-03-01", "2025-04-30")


def make_execution(
    execution_id: str = "ex-1",
    *,
    columns: tuple[str, ...] = ("month", "net_revenue", "orders", "conversion", "customer_hash"),
    column_types: tuple[str, ...] = ("date", "numeric", "int8", "numeric", "text"),
    rows: tuple[tuple[JsonValue, ...], ...] = (
        ("2025-03-01", "1180000.00", 5230, "0.242", HASH_CELL),
        ("2025-04-01", "950000.50", 4101, "0.198", "c71e5f00aa11"),
    ),
) -> Execution:
    return Execution(
        execution_id=execution_id,
        sql="select 1",
        columns=columns,
        column_types=column_types,
        rows=rows,
        returned_rows=len(rows),
        truncated=False,
    )


def single_value(value: str, column_type: str = "numeric") -> Execution:
    return make_execution(columns=("v",), column_types=(column_type,), rows=((value,),))


def unverified(text: str, execution: Execution | None = None, *, question: str = QUESTION) -> list[str]:
    return unverified_numbers(
        text, execution or make_execution(), question=question, time_range=TIME_RANGE, as_of_date=AS_OF
    )


class TestExecutionReference:
    def test_last_execution_passes(self) -> None:
        executions = [make_execution("a"), make_execution("b")]

        assert check_execution_reference("b", executions) is None

    def test_stale_id_names_the_last_one(self) -> None:
        executions = [make_execution("a"), make_execution("b")]

        assert check_execution_reference("a", executions) == (
            "a is not the last query you ran; describe the result of b"
        )

    def test_unknown_id_is_refused(self) -> None:
        assert check_execution_reference("zzz", [make_execution("a")]) == "no successful run_sql has execution_id zzz"

    def test_no_successful_run_at_all(self) -> None:
        assert check_execution_reference("a", []) == "no successful run_sql has execution_id a"

    def test_id_must_match_exactly(self) -> None:
        assert check_execution_reference("A", [make_execution("a")]) is not None
        assert check_execution_reference("a ", [make_execution("a")]) is not None


class TestDraftSql:
    @pytest.mark.parametrize(
        "sql",
        ["select a from t", "select a from t;", "  select   a\n from\tt ;  ", "select a from t ;\n"],
    )
    def test_trailing_semicolon_and_whitespace_are_ignored(self, sql: str) -> None:
        assert check_draft_sql(sql, "select a from t") is None

    def test_validated_sql_is_normalised_too(self) -> None:
        assert check_draft_sql("select a from t", "select   a from t;") is None

    @pytest.mark.parametrize(
        "sql",
        [
            "select b from t",
            "select a from t limit 5",
            "SELECT a FROM t",
            "select a from t;;",
            "select a from t; select 1",
            "",
        ],
    )
    def test_any_other_change_is_refused(self, sql: str) -> None:
        assert check_draft_sql(sql, "select a from t") == "return exactly the SQL that last passed validate_sql"

    def test_nothing_validated_yet(self) -> None:
        assert check_draft_sql("select 1", None) == "validate the SQL you return with validate_sql"

    def test_whitespace_inside_a_string_literal_is_significant(self) -> None:
        assert check_draft_sql("select 1 where n = 'a  b'", "select 1 where n = 'a b'") is not None
        assert check_draft_sql("select 1  where n = 'a  b'", "select 1 where n = 'a  b'") is None

    def test_whitespace_inside_a_quoted_identifier_is_significant(self) -> None:
        assert check_draft_sql('select "a  b" from t', 'select "a b" from t') is not None

    def test_escaped_quote_does_not_end_the_literal_early(self) -> None:
        assert check_draft_sql("select 'it''s  x'", "select 'it''s x'") is not None


class TestNumbersCheck:
    @pytest.mark.parametrize(
        "text",
        [
            "Net revenue was 1,180,000 in March.",
            "Net revenue was EUR 1,180,000.00 in March.",
            "That is 1.18 million.",
            "That is about 1.2M.",
            "That is about €1.2 million.",
            "That is about €1.18m.",
            "April brought 950,000.5.",
            "April brought 950,000.",
            "Orders: 5,230.",
            "Orders reached 5230 and then 4101.",
            "Two months are shown.",
            "The 2 rows cover 2025.",
            "March and April 2025 (months 3 and 4, day 1, quarters 1 and 2).",
            "Conversion was 24.2%.",
            "Conversion was 24%.",
            "Conversion was 0.242.",
            "Showing the top 10.",
            "About 5,200 orders in March.",
            "About 5.2k orders in March.",
            "No figures here at all.",
        ],
    )
    def test_allowed_forms_pass(self, text: str) -> None:
        assert unverified(text) == []

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("That is €1.3M.", ["1.3M"]),
            ("Orders were 1,234.", ["1,234"]),
            ("Revenue grew +12% over the period.", ["+12%"]),
            ("This is the worst since 2019.", ["2019"]),
            ("The hash ends in 3021.", ["3021"]),
            ("Revenue was 1,180,000 but 7 customers left.", ["7"]),
            ("Orders were 5,230 and 4,700.", ["4,700"]),
        ],
    )
    def test_unverified_numbers_are_returned_as_written(self, text: str, expected: list[str]) -> None:
        assert unverified(text) == expected

    def test_each_unverified_number_is_listed_once_in_order(self) -> None:
        assert unverified("Up 77 then 88 then 77 again.") == ["77", "88"]

    def test_a_text_cell_quoted_as_is_has_no_numbers(self) -> None:
        assert unverified(f"The top customer is {HASH_CELL}.") == []
        assert unverified(f"The top customer is {HASH_CELL}, who ordered 5,230.") == []

    def test_digits_taken_from_a_text_cell_out_of_context_fail(self) -> None:
        assert unverified("Customer 9be4a3f9c1d7e2203021 and then 3021.") == ["3021"]

    def test_text_cell_is_not_a_result_value(self) -> None:
        execution = make_execution(columns=("code",), column_types=("text",), rows=(("88123",), ("1",)))

        assert unverified("The code is 88123.", execution) == []
        assert unverified("The code is 88124.", execution) == ["88124"]

    def test_short_text_cell_does_not_eat_digits_of_other_numbers(self) -> None:
        execution = make_execution(columns=("name",), column_types=("text",), rows=(("1",), ("x",)))

        assert unverified("It was 1.9 and 1,500.", execution) == ["1.9", "1,500"]

    def test_text_cell_is_removed_only_when_it_stands_alone(self) -> None:
        execution = make_execution(columns=("code",), column_types=("text",), rows=(("12345",), ("x",)))

        assert unverified("Code 12345 and 123456 and 3.12345.", execution) == ["123456", "3.12345"]

    def test_short_numeric_text_cells_do_not_launder_figures(self) -> None:
        execution = make_execution(columns=("code",), column_types=("text",), rows=(("1",), ("42",), ("2024",)))

        assert unverified("Revenue 42 thousand.", execution, question="q") == ["42 thousand"]
        assert unverified("Growth was 42%.", execution, question="q") == ["42%"]
        assert unverified("Change of -42.", execution, question="q") == ["-42"]
        assert unverified("Down 42k.", execution, question="q") == ["42k"]
        assert unverified("It happened in 2024.", execution, question="q") == ["2024"]

    def test_a_long_numeric_text_cell_is_not_a_scaled_or_signed_figure_either(self) -> None:
        execution = make_execution(columns=("code",), column_types=("text",), rows=(("88123",), ("x",)))

        assert unverified("Code 88123.", execution) == []
        assert unverified("Revenue 88123 thousand and -88123 and 88123%.", execution) == [
            "88123 thousand",
            "-88123",
            "88123%",
        ]

    def test_an_abbreviated_hash_is_still_inside_the_cell(self) -> None:
        assert unverified("Customer 9be4a3f9… spent 1,500.") == ["1,500"]
        assert unverified("Customer 9be4a3f9c1d7 spent 5,230.") == []
        assert unverified("Customer 9BE4A3F9... spent 5,230.") == []
        assert unverified("The hash ends in 3021.") == ["3021"]
        assert unverified("Customer 9be4a3f0… spent 5,230.") != []

    @pytest.mark.parametrize(
        ("text", "passes"),
        [
            ("About 1.2M.", True),
            ("About 1.1M.", True),
            ("About 1.3M.", False),
            ("About 1.0M.", False),
        ],
    )
    def test_magnitude_tolerance_is_relative_to_the_result_value(self, text: str, passes: bool) -> None:
        execution = single_value("1142857.15")

        assert (unverified(text, execution) == []) is passes

    def test_five_percent_boundary(self) -> None:
        # 1.2M is exactly 5% above 1,142,857.142857...; the boundary is inclusive and relative to the result value.
        assert unverified("About 1.2M.", single_value("1142857.15")) == []
        assert unverified("About 1.2M.", single_value("1142857.14")) == ["1.2M"]
        assert unverified("About 1.2M.", single_value("1142857")) == ["1.2M"]
        assert unverified("About 1.2M.", single_value("1142000")) == ["1.2M"]

    def test_rounding_boundary_is_half_a_unit_of_the_last_digit(self) -> None:
        assert unverified("April: 950,000.") == []
        assert unverified("April: 949,999.") == ["949,999"]
        assert unverified("April: 950,001.") == []
        assert unverified("April: 950,002.") == ["950,002"]
        assert unverified("April: 950,000.6.") == ["950,000.6"]

    @pytest.mark.parametrize("marker", ["about", "About", "around", "roughly", "approximately", "nearly", "almost"])
    def test_a_hedged_plain_integer_gets_the_five_percent(self, marker: str) -> None:
        assert unverified(f"{marker} 5,200 orders.", question="q") == []
        assert unverified(f"{marker} 4,100 orders.", question="q") == []
        assert unverified(f"{marker} 5,600 orders.", question="q") == ["5,600"]

    @pytest.mark.parametrize("marker", ["~", "≈", "~ ", "about €", "about EUR "])
    def test_other_hedges(self, marker: str) -> None:
        assert unverified(f"{marker}4,100 orders.", question="q") == []

    @pytest.mark.parametrize(
        ("text", "flagged"),
        [
            ("exactly 5,000 orders.", "5,000"),
            ("5,400 orders.", "5,400"),
            ("over 5,400 orders.", "5,400"),
            ("about, 5,400 orders.", "5,400"),
        ],
    )
    def test_an_unhedged_plain_integer_gets_only_the_rounding(self, text: str, flagged: str) -> None:
        assert unverified(text, question="q") == [flagged]

    def test_unhedged_four_thousand_against_4101_is_flagged(self) -> None:
        assert unverified("Orders were 4,000.", question="q") == ["4,000"]

    def test_a_hedge_must_come_right_before_the_figure(self) -> None:
        assert unverified("About two things: 5,400 orders.", question="q") == ["5,400"]

    def test_a_hedge_does_not_widen_a_decimal_or_a_percent(self) -> None:
        assert unverified("About 5,230.4 orders.", question="q") == ["5,230.4"]
        assert unverified("About 25% conversion.", question="q") == ["25%"]

    def test_percentages_use_the_ratio_times_100_within_rounding_only(self) -> None:
        assert unverified("Conversion was 24.2%.") == []
        assert unverified("Conversion was 24.3%.") == ["24.3%"]
        assert unverified("Conversion was 25%.") == ["25%"]
        assert unverified("Conversion was 19.8 percent.") == []

    def test_a_percent_sign_does_not_make_a_count_or_date_match(self) -> None:
        assert unverified("2% of orders.") == ["2%"]
        assert unverified("2025% growth.") == ["2025%"]

    def test_a_plain_number_is_not_tried_times_100(self) -> None:
        assert unverified("Conversion was 24.2.") == ["24.2"]

    def test_negative_values_match_only_with_their_sign(self) -> None:
        execution = single_value("-250.00")

        assert unverified("A change of -250.", execution) == []
        assert unverified("A change of \u2212250.", execution) == []
        assert unverified("A change of 250.", execution) == ["250"]
        assert unverified("A change of +250.", execution) == ["+250"]

    def test_a_dash_between_numbers_is_not_a_sign(self) -> None:
        assert unverified("Between 5230-4101 orders.") == []
        assert unverified("Between 5230 - 4101 orders.") == []
        assert unverified("Between 5230\u20134101 orders.") == []

    def test_a_sign_cannot_hide_a_wrong_value_behind_a_range(self) -> None:
        assert unverified("From 5230 to -4101.") == ["-4101"]

    @pytest.mark.parametrize("text", ["It was 1.2e6.", "It was 1.18E+6."])
    def test_scientific_notation_is_refused_whatever_the_value(self, text: str) -> None:
        assert len(unverified(text)) == 1

    @pytest.mark.parametrize(
        "token",
        ["1.180.000", "1 180 000", "1,18", "1,1800,00", "5,23", "1,180,00"],
    )
    def test_other_thousands_separators_are_refused(self, token: str) -> None:
        # Only "," in groups of three is accepted; "." groups and spaces split into separate figures.
        assert unverified(f"Revenue was {token}.", single_value("1180000")) != []

    def test_a_number_that_equals_the_row_count_only_by_coincidence_is_still_a_count(self) -> None:
        execution = single_value("3", "int8")
        execution = execution.model_copy(update={"returned_rows": 7})

        assert unverified("There are 7 rows.", execution) == []
        assert unverified("There are 8 rows.", execution) == ["8"]

    def test_count_of_distinct_values_of_any_column(self) -> None:
        execution = make_execution(
            columns=("country", "orders"),
            column_types=("text", "int8"),
            rows=(("ES", 100), ("FR", 200), ("DE", 300), ("ES", 400), ("IT", 500)),
        )

        assert unverified("5 rows across 4 countries.", execution) == []
        assert unverified("Across 7 countries.", execution) == ["7"]

    def test_a_count_must_be_exact_not_within_five_percent(self) -> None:
        assert unverified("Over 2.05 rows.") == ["2.05"]

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("In Q1 2025.", []),
            ("In Q2.", []),
            ("In 2025-03.", []),
            ("On 2025-04-01.", []),
            ("On 2025-04-09.", ["09"]),
            ("On 2025-13-01.", ["13"]),
            ("On 01/04/2025.", []),
        ],
    )
    def test_date_parts_of_the_result(self, text: str, expected: list[str]) -> None:
        assert unverified(text) == expected

    def test_quarter_number_is_derived_from_the_month(self) -> None:
        execution = make_execution(columns=("d",), column_types=("date",), rows=(("2025-08-17",),))

        def check(text: str) -> list[str]:
            return unverified_numbers(text, execution, question="when?", time_range=(), as_of_date=date(2025, 6, 30))

        assert check("Q3 had it.") == []
        assert check("Q4 had it.") == ["4"]

    def test_date_parts_come_from_the_question_the_time_range_and_the_as_of_date(self) -> None:
        execution = single_value("1", "int8")

        assert unverified("Since 2024-11-15.", execution, question="What happened on 2024-11-15?") == []
        assert unverified("As of 2025-06-30, Q2.", execution) == []
        assert unverified("In March 2025.", execution) == []
        assert unverified("In 2026.", execution) == ["2026"]

    def test_a_timestamp_cell_contributes_its_date_parts(self) -> None:
        execution = make_execution(
            columns=("at",), column_types=("timestamptz",), rows=(("2023-08-17T10:15:00+00:00",),)
        )

        assert unverified("On 2023-08-17 (Q3).", execution, question="when?") == []
        assert unverified("In 2023, day 17, month 8.", execution, question="when?") == []

    def test_dates_of_text_columns_are_not_dates(self) -> None:
        execution = make_execution(columns=("note",), column_types=("text",), rows=(("2019-02-03 shipped",),))

        assert unverified("In 2019.", execution, question="when?") == ["2019"]

    def test_numbers_in_the_question_are_allowed(self) -> None:
        question = "What were the top 3 products in the last 90 days, above 1,500 EUR?"

        assert unverified("The top 3 over 90 days above 1,500.", question=question) == []
        assert unverified("The top 8 products.", question=question) == ["8"]

    def test_a_question_number_is_matched_exactly(self) -> None:
        assert unverified("The top 11.", question="the top 10?") == ["11"]

    def test_question_percent_matches_only_as_a_percent(self) -> None:
        question = "Which products have a margin above 40%?"

        assert unverified("Margin above 40%.", question=question) == []
        assert unverified("Margin above 40.", question=question) == ["40"]

    @pytest.mark.parametrize("cell", [None, True, "not a number", "NaN", "Infinity"])
    def test_non_numeric_cells_in_a_numeric_column_are_no_result_value(self, cell: JsonValue) -> None:
        execution = make_execution(columns=("v",), column_types=("numeric",), rows=((cell,), ("42",)))

        assert unverified("The value is 42.", execution) == []
        assert unverified("The value is 5.", execution) == ["5"]
        assert unverified("The value is 43.", execution) == ["43"]

    @pytest.mark.parametrize("column_type", ["int2", "int4", "int8", "integer", "numeric", "float4", "float8", "float"])
    def test_numeric_column_types(self, column_type: str) -> None:
        assert unverified("It is 4821.", single_value("4821", column_type), question="q") == []

    @pytest.mark.parametrize("column_type", ["text", "varchar", "bool", "date", "uuid", "jsonb"])
    def test_other_column_types_are_no_result_values(self, column_type: str) -> None:
        assert unverified("It is 4821.0.", single_value("4821", column_type), question="q") == ["4821.0"]

    def test_float_cells_are_compared_as_decimals(self) -> None:
        execution = make_execution(columns=("v",), column_types=("float8",), rows=((0.1 + 0.2,),))

        assert unverified("It is 0.3.", execution, question="q") == []

    def test_truncation_does_not_widen_what_is_allowed(self) -> None:
        execution = make_execution().model_copy(update={"truncated": True, "returned_rows": 2})

        assert unverified("Total is 99,999.", execution) == ["99,999"]

    def test_a_truncated_result_does_not_make_its_row_count_a_total(self) -> None:
        rows = tuple((f"p{n}",) for n in range(100))
        truncated = Execution(
            execution_id="ex-1",
            sql="select 1",
            columns=("product",),
            column_types=("text",),
            rows=rows,
            returned_rows=100,
            truncated=True,
        )
        complete = truncated.model_copy(update={"truncated": False})

        assert unverified("100 products have never been ordered.", truncated, question="q") == ["100"]
        assert unverified("100 products have never been ordered.", complete, question="q") == []

    def test_a_truncated_result_does_not_make_its_distinct_counts_totals(self) -> None:
        rows = (("ES", 10), ("ES", 10), ("FR", 20), ("DE", 20), ("IT", 30), ("NL", 30), ("ES", 40))
        truncated = make_execution(columns=("country", "orders"), column_types=("text", "int8"), rows=rows)
        complete = truncated.model_copy(update={"truncated": False})
        truncated = truncated.model_copy(update={"truncated": True})

        assert unverified("Sales cover 5 countries.", truncated, question="q") == ["5"]
        assert unverified("Sales cover 5 countries.", complete, question="q") == []
        assert unverified("Spain has 10 orders.", truncated, question="q") == []

    def test_empty_result_allows_only_zero_rows_and_question_and_dates(self) -> None:
        execution = make_execution(rows=())

        assert unverified("The query returned 0 rows.", execution) == []
        assert unverified("The query returned 8 rows.", execution) == ["8"]

    def test_empty_answer_has_no_numbers(self) -> None:
        assert unverified("") == []

    def test_numbers_glued_to_words_are_still_checked(self) -> None:
        assert unverified("Segment A9 grew.") == ["9"]
        assert unverified("Segment 9A grew.") == ["9"]

    def test_retry_message_lists_the_numbers_and_names_the_accepted_forms(self) -> None:
        message = numbers_message(["1.3M", "+12%"])

        assert message.startswith("these numbers are not in the result: 1.3M, +12%.")
        assert "as it appears in the result, with its sign" in message
        assert "minus sign" in message
        assert 'round only with a hedge such as "about"' in message
        assert "compute any derived figure (share, difference, growth) in SQL" in message

    def test_bn_mn_and_a_spaced_percent(self) -> None:
        assert unverified("About 1.18bn and 1.18mn.", single_value("1180000000"), question="q") == ["1.18mn"]
        assert unverified("About 1.18mn.", single_value("1180000"), question="q") == []
        assert unverified("Conversion was 24.2 %.") == []
        assert unverified("Conversion was 25 %.") == ["25 %"]
        assert unverified("It was 1.18 bn.", single_value("1180000000"), question="q") == ["1.18"]

    def test_magnitude_words_and_letters(self) -> None:
        execution = single_value("2500000")

        assert unverified("2.5 million, 2.5M, 2.5m, 2500k, 2.5 Million.", execution) == []
        assert unverified("2.5 billion.", execution) == ["2.5 billion"]
        assert unverified("2.5 months.", execution) == ["2.5"]
        assert unverified("2.5 mph.", execution) == ["2.5"]
