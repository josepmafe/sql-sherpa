from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
from typing import TYPE_CHECKING, Any
from unittest.mock import call

import pytest
from sherpa_mcp.catalog import MartCatalogLoader
from sherpa_mcp.database import QueryResult, WarehouseToolError
from sherpa_mcp.examples import ExampleIndex
from sherpa_mcp.tools import DOMAIN_SUBJECTS, TableSummary, WarehouseTools

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path
    from unittest.mock import Mock

    import psycopg
    from logfire.testing import CaptureLogfire
    from sherpa_mcp.database import AgentDatabase

METRICS = (
    *("gross_revenue", "discount_amount", "refunded_revenue", "net_revenue"),
    *("cogs", "gross_margin", "aov", "refund_rate", "conversion_rate"),
)
SIMILARITY_FLOOR = 0.2  # pg_trgm.similarity_threshold set by the tool
PII_LITERAL = "leak@example.com"
HOSTILE_SQL = f"select email from marts.dim_customers where email = '{PII_LITERAL}'; drop table marts.fct_orders"
NO_MATCH_HINT = (
    "no stored value has a similarity of 0.2 or more and none contains the term as a word; try a sibling column, "
    "an English or code form of the term, or the distinct_values describe_table lists"
)
AS_OF_QUERY = "select max(order_date) from marts.fct_orders"
FIRST_ORDER_QUERY = "select min(order_date) from marts.fct_orders"
# Agent spec §4.1, get_context: what the warehouse cannot answer, and the facts that change a correct answer.
DOES_NOT_COVER = [
    "inventory and stock levels",
    "product reviews",
    "currencies other than EUR",
    "campaign names, budgets and ad spend (customers and promotions carry only a campaign id; sessions also carry "
    "the utm_campaign slug)",
    "app visits and on-site events below the session, such as which products or pages were viewed or carted "
    "(fct_sessions has one row per web session with funnel flags)",
    "leads that never became customers (customers carry lead_id and lead_source)",
]
SHORT_HISTORY = "comparisons across years, such as year over year: the history is shorter than two years"
NOTES = [
    "as_of_date is the latest order date: resolve relative periods (last month, last 30 days, this year) against "
    "it, never against today",
    "the history runs from first_order_date to as_of_date, so the first and the last month are partial",
    "refunds arrive 6 to 47 days after the order and count on the order's date, so for orders of the last 7 weeks "
    "refunded revenue is understated and net revenue overstated",
    "amounts are EUR; gross and net revenue exclude the 20% tax and the shipping",
    "dates and timestamps are UTC",
    "cancelled orders are kept and flagged: order metrics filter on not is_cancelled",
    "test accounts and their orders are left out of every mart",
    "sessions are web only: app orders have no session, so conversion_rate and session counts leave app orders out",
]


def test_marketing_web_subject_does_not_promise_landing_pages() -> None:
    assert "landing page" not in DOMAIN_SUBJECTS["marketing_web"]


@pytest.mark.warehouse
class TestWarehouseTools:
    def test_get_context_as_of_and_scope(self, tools: WarehouseTools, admin: psycopg.Connection) -> None:
        row = admin.execute("select max(order_date), min(order_date) from marts.fct_orders").fetchone()
        assert row is not None

        context = tools.get_context()

        assert str(context.as_of_date) == str(row[0])
        assert str(context.first_order_date) == str(row[1])
        # The built warehouse holds a year of history (make backfill), shorter than two years.
        assert context.does_not_cover == (*DOES_NOT_COVER, SHORT_HISTORY)
        assert list(context.notes) == NOTES
        assert set(context.domains) == {"customers", "marketing_web", "products", "sales", "shared"}
        assert context.covers[0] == (
            "customers: lifecycle segment, VIP flag, lead source, acquisition channel and lifetime order history "
            "(dim_customers)"
        )
        assert context.covers[3].endswith(
            "(dim_promotions, fct_order_discounts, fct_order_items, fct_orders, fct_refunds, fct_returns)"
        )

    def test_list_tables_are_the_marts(self, tools: WarehouseTools, marts: tuple[str, ...]) -> None:
        names = [table.name for table in tools.list_tables().tables]

        assert sorted(names) == sorted(marts)

    def test_describe_table_samples_are_ordered_by_pk_and_masked(self, tools: WarehouseTools) -> None:
        description = tools.describe_table("dim_customers")

        columns = description.sample_rows.columns
        ids = [row[columns.index("customer_id")] for row in description.sample_rows.rows]
        assert ids == sorted(ids)
        assert len(ids) == 3
        assert {row[columns.index("email")] for row in description.sample_rows.rows} == {"***"}
        assert any(column.name == "email" and column.is_pii for column in description.columns)

    def test_describe_table_lists_the_stored_values_of_small_text_columns(self, tools: WarehouseTools) -> None:
        columns = {column.name: column for column in tools.describe_table("fct_orders").columns}
        customers = {column.name: column for column in tools.describe_table("dim_customers").columns}

        assert columns["sales_channel"].distinct_values == ("app", "web")
        assert set(columns["current_status"].distinct_values or ()) <= set(
            columns["current_status"].accepted_values or ()
        )
        assert columns["order_number"].distinct_values is None  # one per order: more than 30
        assert columns["net_revenue"].distinct_values is None  # not text
        assert "United Kingdom" in (customers["country_name"].distinct_values or ())
        assert customers["email"].distinct_values is None  # PII
        assert columns["net_revenue"].metric_refs == ("net_revenue",)
        assert columns["order_number"].metric_refs == ()

    def test_describe_table_lists_exactly_the_stored_values_of_lead_source(self, tools: WarehouseTools) -> None:
        columns = {column.name: column for column in tools.describe_table("dim_customers").columns}

        values = columns["lead_source"].distinct_values
        assert values is not None
        assert len(values) == 4
        assert "None" not in values  # customers without a lead are NULL and never listed

    def test_search_values_returns_two_matches_in_descending_similarity(self, tools: WarehouseTools) -> None:
        matches = tools.search_values("dim_customers", "acquisition_channel", "paidsearch").matches

        assert [match.value for match in matches] == ["paid_search", "paid_social"]
        assert matches[0].similarity > matches[1].similarity

    def test_search_values_finds_a_value_with_a_similarity_between_a_fifth_and_three_tenths(
        self, tools: WarehouseTools
    ) -> None:
        matches = tools.search_values("dim_customers", "acquisition_channel", "direct mail").matches

        found = {match.value: match.similarity for match in matches}
        assert 0.2 <= found["email"] < 0.3
        assert list(found) == ["direct", "email"]

    def test_describe_table_is_case_insensitive(self, tools: WarehouseTools) -> None:
        assert tools.describe_table("DIM_Customers").name == "dim_customers"

    def test_describe_table_unknown_name_lists_valid_ones(self, tools: WarehouseTools) -> None:
        with pytest.raises(WarehouseToolError, match=r"nope.*dim_customers.*fct_orders"):
            tools.describe_table("nope")

    def test_search_values_finds_brand_by_typo(self, tools: WarehouseTools, agent_database: AgentDatabase) -> None:
        brand = str(agent_database.scalar("select brand from marts.dim_products group by 1 order by count(*) desc, 1"))
        typo = brand[:-1] + ("x" if brand[-1] != "x" else "z")

        matches = tools.search_values("dim_products", "BRAND", typo).matches

        assert matches[0].value == brand
        assert 0 < matches[0].similarity <= 1
        assert matches[0].row_count > 0

    def test_search_values_finds_a_value_containing_the_term_as_a_word(
        self, tools: WarehouseTools, agent_database: AgentDatabase
    ) -> None:
        brand = str(agent_database.scalar("select brand from marts.dim_products group by 1 order by count(*) desc, 1"))

        found = tools.search_values("dim_products", "product_name", brand, limit=50)

        # Product names start with their brand: as a whole they are not similar to it, but it is a word of each.
        assert found.matches
        assert all(match.value.startswith(brand) and match.similarity == 1.0 for match in found.matches)
        assert found.hint is None

    def test_search_values_without_a_match_says_what_to_try(self, tools: WarehouseTools) -> None:
        found = tools.search_values("dim_customers", "country_name", "qqqzzz")

        assert found.matches == ()
        assert found.hint == NO_MATCH_HINT

    @pytest.mark.parametrize("term", ["%", "_"])
    def test_search_values_wildcards_do_not_widen_the_match(
        self, tools: WarehouseTools, agent_database: AgentDatabase, term: str
    ) -> None:
        brands = int(str(agent_database.scalar("select count(distinct brand) from marts.dim_products")))

        matches = tools.search_values("dim_products", "brand", term, limit=50).matches

        # Interpolated into a LIKE, `%` would match every brand and `_` every brand of any length.
        assert brands > 2
        assert len(matches) < brands
        assert all(match.similarity >= SIMILARITY_FLOOR for match in matches)

    def test_search_values_trailing_metacharacter_keeps_the_real_brand_first(
        self, tools: WarehouseTools, agent_database: AgentDatabase
    ) -> None:
        brand = str(agent_database.scalar("select brand from marts.dim_products group by 1 order by count(*) desc, 1"))

        for suffix in ("%", "_", "\\", "'"):
            matches = tools.search_values("dim_products", "brand", brand + suffix, limit=50).matches
            assert matches[0].value == brand, suffix
            assert all(match.similarity >= SIMILARITY_FLOOR for match in matches)

        # A LIKE prefix pattern would return every brand sharing these letters; the trigram bound does not.
        prefix = tools.search_values("dim_products", "brand", brand[:2] + "%", limit=50).matches
        assert all(match.similarity >= SIMILARITY_FLOOR for match in prefix)

    @pytest.mark.parametrize("term", ["o'neil", "\\"])
    def test_search_values_quote_and_backslash_terms_return_without_error(
        self, tools: WarehouseTools, term: str
    ) -> None:
        matches = tools.search_values("dim_products", "brand", term).matches

        assert all(match.similarity >= SIMILARITY_FLOOR for match in matches)

    def test_search_values_unknown_column_lists_valid_ones(self, tools: WarehouseTools) -> None:
        with pytest.raises(WarehouseToolError, match=r"nope.*brand"):
            tools.search_values("dim_products", "nope", "x")

    def test_search_values_refuses_pii_and_non_text(self, tools: WarehouseTools) -> None:
        with pytest.raises(WarehouseToolError, match="PII"):
            tools.search_values("dim_customers", "email", "a")
        with pytest.raises(WarehouseToolError, match="text"):
            tools.search_values("dim_products", "current_list_price", "1")

    @pytest.mark.parametrize("limit", [0, 51])
    def test_search_values_limit_is_bounded(self, tools: WarehouseTools, limit: int) -> None:
        with pytest.raises(WarehouseToolError, match="limit"):
            tools.search_values("dim_products", "brand", "x", limit=limit)

    def test_get_metric_definitions_lists_the_doc_blocks(self, tools: WarehouseTools) -> None:
        names = [metric.name for metric in tools.get_metric_definitions().metrics]

        assert sorted(names) == sorted(METRICS)

    def test_get_examples_returns_k_and_every_example_runs(self, tools: WarehouseTools, examples_path: Path) -> None:
        assert len(tools.get_examples("net revenue by month", k=2).examples) == 2
        for example in ExampleIndex.from_yaml(examples_path).examples:
            result = tools.run_sql(example.sql)
            assert result.returned_rows >= 1, example.question
            assert not result.truncated, example.question

    def test_get_examples_rejects_bad_k(self, tools: WarehouseTools) -> None:
        with pytest.raises(WarehouseToolError, match="k must"):
            tools.get_examples("anything", k=0)

    def test_validate_sql_reports_guard_and_explain_errors(self, tools: WarehouseTools) -> None:
        guard = tools.validate_sql("select * from staging.stg_shop__orders")
        planner = tools.validate_sql("select nope from marts.fct_orders")
        good = tools.validate_sql("select order_id from marts.fct_orders")

        assert not guard.ok
        assert guard.errors
        assert not planner.ok
        assert any("nope" in error for error in planner.errors)
        assert good.ok
        assert good.errors == ()
        assert good.tables == ("marts.fct_orders",)
        assert good.warnings == ()

    def test_validate_sql_warns_when_the_query_reads_the_clock(
        self, tools: WarehouseTools, admin: psycopg.Connection
    ) -> None:
        row = admin.execute("select max(order_date) from marts.fct_orders").fetchone()
        assert row is not None

        validation = tools.validate_sql("select count(*) from marts.fct_orders where order_date >= current_date - 30")

        assert validation.ok
        assert validation.warnings == (
            f"current_date reads the real clock, but the warehouse is as of {row[0]}; "
            "derive periods from (select max(order_date) from marts.fct_orders)",
        )

    def test_run_sql_truncates_masks_and_ids(self, tools: WarehouseTools) -> None:
        big = tools.run_sql("select * from marts.fct_order_items")
        emails = tools.run_sql("select email from marts.dim_customers limit 5")
        plain = tools.run_sql("select order_id from marts.fct_orders limit 2")
        spaced = tools.run_sql("select order_id from marts.fct_orders limit 2 ;  \n")

        assert big.column_types[:2] == ("int8", "int8")  # order_item_id, order_id
        assert len(big.column_types) == len(big.columns)
        assert plain.column_types == ("int8",)
        assert big.returned_rows == 100
        assert big.truncated
        assert len(big.rows) == 100
        assert [row[0] for row in emails.rows] == ["***"] * 5
        assert plain.execution_id == spaced.execution_id
        assert plain.execution_id == hashlib.sha256(b"select order_id from marts.fct_orders limit 2").hexdigest()[:16]

    def test_error_text_never_carries_pii_values(self, tools: WarehouseTools, admin: psycopg.Connection) -> None:
        row = admin.execute("select email from marts.dim_customers where email is not null limit 1").fetchone()
        assert row is not None
        email = str(row[0])

        with pytest.raises(WarehouseToolError) as error:
            tools.run_sql("select cast(email as integer) from marts.dim_customers limit 1")
        validation = tools.validate_sql("select cast(email as integer) from marts.dim_customers limit 1")

        assert email not in str(error.value)
        assert "check the literal format" in str(error.value)
        assert email not in " ".join(validation.errors)

    @pytest.mark.parametrize(
        "query",
        [
            "select c from marts.dim_customers c limit 1",
            "select dim_customers from marts.dim_customers limit 1",
            "select to_json(c) from marts.dim_customers c limit 1",
            "select c.*::text from marts.dim_customers c limit 1",
            "select concat(c.*) from marts.dim_customers c limit 1",
            "select (c.*)::text as x from marts.dim_customers c limit 1",
            "select array_agg(c.*) from marts.dim_customers c",
            # attribute notation: Postgres reads c.f as f(c) when f is not a column of c
            "select c.row_to_json from marts.dim_customers c limit 1",
            "select c.record_out from marts.dim_customers c limit 1",
            "with x as (select * from marts.dim_customers) select x.to_jsonb from x limit 1",
        ],
    )
    def test_run_sql_refuses_whole_row_pii_leaks(self, tools: WarehouseTools, query: str) -> None:
        with pytest.raises(WarehouseToolError, match=r"whole-row reference|is not allowed|is not a column of"):
            tools.run_sql(query)

    def test_run_sql_reads_an_aggregate_column_by_the_name_postgres_gives_it(self, tools: WarehouseTools) -> None:
        result = tools.run_sql(
            "select t.count, t.sum is not null, t.avg is not null, t.min <= t.max from ("
            "select count(*), sum(net_revenue), avg(net_revenue), min(order_date), max(order_date) "
            "from marts.fct_orders) t"
        )

        assert result.columns == ("count", "?column?", "?column?", "?column?")
        assert result.rows[0][0] > 0
        assert result.rows[0][1:] == (True, True, True)

    def test_run_sql_rejects_hostile_sql_with_guard_errors(self, tools: WarehouseTools) -> None:
        with pytest.raises(WarehouseToolError, match="staging"):
            tools.run_sql("select * from staging.stg_shop__orders")
        with pytest.raises(WarehouseToolError):
            tools.run_sql("drop table marts.fct_orders")


@pytest.mark.warehouse
class TestGuardTracing:
    def test_rejection_is_traced_without_the_sql(self, tools: WarehouseTools, capfire: CaptureLogfire) -> None:
        with pytest.raises(WarehouseToolError):
            tools.run_sql(HOSTILE_SQL)

        spans = capfire.exporter.exported_spans_as_dict()
        guard = _only_span(spans, "sherpa.guard")
        assert guard["attributes"]["tool"] == "run_sql"
        assert guard["attributes"]["ok"] is False
        assert guard["attributes"]["error_count"] == 1
        assert guard["attributes"]["execution_id"] == hashlib.sha256(HOSTILE_SQL.encode()).hexdigest()[:16]
        rejected = _only_span(spans, "guard rejected")
        assert rejected["attributes"]["rule"] == "statement_count"
        assert rejected["attributes"]["error_count"] == 1
        assert PII_LITERAL not in str(spans)
        assert "drop table" not in str(spans)

    def test_rejection_rule_comes_from_the_error_code(self, tools: WarehouseTools, capfire: CaptureLogfire) -> None:
        tools.validate_sql("select c.row_to_json from marts.dim_customers c")

        rejected = _only_span(capfire.exporter.exported_spans_as_dict(), "guard rejected")
        assert rejected["attributes"]["rule"] == "column"

    def test_accepted_query_is_traced_with_its_marts(self, tools: WarehouseTools, capfire: CaptureLogfire) -> None:
        tools.validate_sql("select count(*) from marts.fct_orders where order_id > 41")

        spans = capfire.exporter.exported_spans_as_dict()
        guard = _only_span(spans, "sherpa.guard")
        assert guard["attributes"]["tool"] == "validate_sql"
        assert guard["attributes"]["ok"] is True
        assert guard["attributes"]["error_count"] == 0
        assert json.loads(guard["attributes"]["tables"]) == ["marts.fct_orders"]  # Logfire sends tuples as JSON
        assert "execution_id" not in guard["attributes"]
        assert not [span for span in spans if span["name"] == "guard rejected"]
        assert "41" not in str([span["attributes"] for span in spans if span["name"] == "sherpa.guard"])


class TestGetContext:
    def test_scope_and_lists_are_the_specs(self, fake_tools: WarehouseTools, fake_database: Mock) -> None:
        _set_dates(fake_database, first=dt.date(2025, 1, 1), latest=dt.date(2025, 12, 31))

        context = fake_tools.get_context()

        assert context.as_of_date == dt.date(2025, 12, 31)
        assert context.first_order_date == dt.date(2025, 1, 1)
        assert context.domains == ("customers", "products", "sales")
        assert context.covers == (
            "customers: lifecycle segment, VIP flag, lead source, acquisition channel and lifetime order history "
            "(dim_customers)",
            "products and their variants: brand, three-level category, current list and cost prices (dim_products)",
            "orders, order lines, discounts and promotions, returns and refunds: revenue, cost, margin, delivery "
            "(fct_orders)",
        )
        assert list(context.does_not_cover) == [*DOES_NOT_COVER, SHORT_HISTORY]
        assert list(context.notes) == NOTES

    @pytest.mark.parametrize(
        ("days", "has_note"),
        [(364, True), (365, True), (729, True), (730, False), (731, False)],
    )
    def test_short_history_note_stops_at_two_years(
        self, fake_tools: WarehouseTools, fake_database: Mock, days: int, has_note: bool
    ) -> None:
        first = dt.date(2024, 1, 1)
        _set_dates(fake_database, first=first, latest=first + dt.timedelta(days=days))

        context = fake_tools.get_context()

        assert (SHORT_HISTORY in context.does_not_cover) is has_note
        assert list(context.does_not_cover[: len(DOES_NOT_COVER)]) == DOES_NOT_COVER

    def test_the_dates_come_from_the_documented_queries(self, fake_tools: WarehouseTools, fake_database: Mock) -> None:
        _set_dates(fake_database, first=dt.date(2025, 1, 1), latest=dt.date(2025, 12, 31))

        fake_tools.get_context()

        assert fake_database.scalar.call_args_list == [call(AS_OF_QUERY), call(FIRST_ORDER_QUERY)]

    @pytest.mark.parametrize("empty", ["latest", "first", "both"])
    def test_empty_fct_orders_is_a_tool_error(
        self, fake_tools: WarehouseTools, fake_database: Mock, empty: str
    ) -> None:
        dates: dict[str, dt.date | None] = {AS_OF_QUERY: dt.date(2025, 12, 31), FIRST_ORDER_QUERY: dt.date(2025, 1, 1)}
        if empty in ("latest", "both"):
            dates[AS_OF_QUERY] = None  # max over no rows
        if empty in ("first", "both"):
            dates[FIRST_ORDER_QUERY] = None
        fake_database.scalar.side_effect = lambda query, params=(): dates[query]

        with pytest.raises(WarehouseToolError, match=r"^fct_orders has no rows; run make build-prod$"):
            fake_tools.get_context()


class TestListTables:
    def test_every_field_of_every_mart(self, fake_tools: WarehouseTools) -> None:
        tables = fake_tools.list_tables().tables

        assert tables == (
            TableSummary(
                name="dim_customers",
                description="Customer dimension, one row per customer.",
                domain="customers",
                grain="one row per customer",
                primary_key=("customer_id",),
            ),
            TableSummary(
                name="dim_products",
                description="Product dimension, one row per product.",
                domain="products",
                grain="one row per product",
                primary_key=("product_id",),
            ),
            TableSummary(
                name="fct_orders",
                description="Order fact, one row per order.",
                domain="sales",
                grain="one row per order",
                primary_key=("order_id",),
            ),
        )


class TestDescribeTable:
    def test_sample_rows_are_ordered_by_the_primary_key_not_the_first_column(
        self, fake_tools: WarehouseTools, fake_database: Mock
    ) -> None:
        # dim_products stores `brand` first and `product_id` second.
        fake_database.fetch_for_model.return_value = _result(("brand", "product_id", "supplier_email"), [])

        fake_tools.describe_table("dim_products")

        (query,) = fake_database.fetch_for_model.call_args.args
        assert query.as_string() == 'select * from "marts"."dim_products" order by "product_id" limit 3'
        assert fake_database.fetch_for_model.call_args.kwargs == {"max_rows": 3}

    def test_sample_rows_mask_the_pii_of_every_mart(self, fake_tools: WarehouseTools, fake_database: Mock) -> None:
        fake_database.fetch_for_model.return_value = _result(
            ("brand", "product_id", "supplier_email"), [("acme", 1, "a@b.com"), ("zed", 2, None)]
        )

        description = fake_tools.describe_table("dim_products")

        assert description.sample_rows.columns == ("brand", "product_id", "supplier_email")
        assert description.sample_rows.rows == (("acme", 1, "***"), ("zed", 2, None))  # a NULL stays NULL

    def test_columns_carry_their_synonyms_types_and_metric_refs(
        self, fake_tools: WarehouseTools, fake_database: Mock
    ) -> None:
        _stored_values(fake_database, {"current_status": ["cancelled", "placed", "shipped"]})

        description = fake_tools.describe_table("FCT_Orders")

        columns = {column.name: column for column in description.columns}
        assert description.name == "fct_orders"
        assert description.grain == "one row per order"
        assert description.primary_key == ("order_id",)
        assert list(columns) == ["order_id", "customer_id", "order_date", "net_revenue", "current_status"]
        assert columns["net_revenue"].synonyms == ("revenue", "net sales")
        assert columns["net_revenue"].data_type == "numeric(12,2)"
        assert columns["net_revenue"].metric_refs == ("net_revenue",)
        assert columns["order_id"].synonyms == ()
        assert columns["current_status"].accepted_values == ("placed", "shipped", "cancelled")
        assert columns["current_status"].distinct_values == ("cancelled", "placed", "shipped")

    def test_a_pii_text_column_has_no_distinct_values_and_is_never_queried(
        self, fake_tools: WarehouseTools, fake_database: Mock
    ) -> None:
        # The PII columns have two values each: listed, they would pass for a short, harmless column.
        _stored_values(
            fake_database,
            {
                "email": ["a@b.com", "c@d.com"],
                "supplier_email": ["x@y.com", "z@y.com"],
                "customer_segment": ["vip"],
                "brand": ["acme"],
            },
        )

        customers = fake_tools.describe_table("dim_customers")
        products = fake_tools.describe_table("dim_products")

        by_name = {column.name: column for column in (*customers.columns, *products.columns)}
        assert by_name["email"].is_pii
        assert by_name["email"].distinct_values is None
        assert by_name["supplier_email"].is_pii
        assert by_name["supplier_email"].distinct_values is None
        assert by_name["customer_segment"].distinct_values == ("vip",)
        assert [column for column, _ in _value_queries(fake_database)] == ["customer_segment", "brand"]

    def test_only_text_columns_are_queried_for_stored_values(
        self, fake_tools: WarehouseTools, fake_database: Mock
    ) -> None:
        _stored_values(fake_database, {"current_status": ["placed"]})

        description = fake_tools.describe_table("fct_orders")

        assert [column for column, _ in _value_queries(fake_database)] == ["current_status"]
        assert {column.name for column in description.columns if column.distinct_values is not None} == {
            "current_status"
        }

    @pytest.mark.parametrize(("count", "listed"), [(1, True), (29, True), (30, True), (31, False), (45, False)])
    def test_stored_values_are_listed_up_to_exactly_thirty(
        self, fake_tools: WarehouseTools, fake_database: Mock, count: int, listed: bool
    ) -> None:
        values = [f"segment {number:02d}" for number in range(count)]
        _stored_values(fake_database, {"customer_segment": values})

        description = fake_tools.describe_table("dim_customers")

        segment = next(column for column in description.columns if column.name == "customer_segment")
        assert segment.distinct_values == (tuple(values) if listed else None)
        assert fake_database.fetch.call_args.kwargs == {"max_rows": 30}

    def test_stored_values_query_leaves_out_nulls_and_sorts(
        self, fake_tools: WarehouseTools, fake_database: Mock
    ) -> None:
        _stored_values(fake_database, {"customer_segment": ["vip"]})

        fake_tools.describe_table("dim_customers")

        assert _value_queries(fake_database) == [
            (
                "customer_segment",
                'select "customer_segment" from "marts"."dim_customers" where "customer_segment" is not null '
                "group by 1 order by 1",
            )
        ]

    def test_stored_values_are_read_once_per_catalog(self, fake_tools: WarehouseTools, fake_database: Mock) -> None:
        _stored_values(fake_database, {"current_status": ["placed"]})

        fake_tools.describe_table("fct_orders")
        fake_tools.describe_table("FCT_ORDERS")

        assert len(_value_queries(fake_database)) == 1

    def test_unknown_table_lists_the_valid_ones(self, fake_tools: WarehouseTools, fake_database: Mock) -> None:
        message = r"^unknown table 'nope'; valid tables: dim_customers, dim_products, fct_orders$"

        with pytest.raises(WarehouseToolError, match=message):
            fake_tools.describe_table("nope")

        fake_database.fetch_for_model.assert_not_called()


class TestSearchValues:
    def test_the_query_ranks_by_similarity_then_value_and_sets_the_thresholds(
        self, fake_tools: WarehouseTools, fake_database: Mock
    ) -> None:
        fake_database.fetch.return_value = _result(("value", "similarity", "row_count"), [])

        fake_tools.search_values("dim_products", "BRAND", "gucci", limit=7)

        query, params = fake_database.fetch.call_args.args
        assert query.as_string() == (
            'select "brand" as value, greatest(similarity("brand", %s), word_similarity(%s, "brand")) as similarity, '
            'count(*) as row_count from "marts"."dim_products" where "brand" %% %s or %s <%% "brand" '
            "group by 1 order by 2 desc, 1 limit %s"
        )
        assert params == ("gucci", "gucci", "gucci", "gucci", 7)
        # The 0.2 and 0.6 it asks for are set by AgentDatabase (test_database.py, on the warehouse).
        assert fake_database.fetch.call_args.kwargs == {"max_rows": 7, "set_similarity_threshold": True}

    def test_matches_keep_the_database_order_and_carry_their_counts(
        self, fake_tools: WarehouseTools, fake_database: Mock
    ) -> None:
        fake_database.fetch.return_value = _result(
            ("value", "similarity", "row_count"), [("Gucci", 1.0, 12), ("Gucchi", 0.5, 3)]
        )

        found = fake_tools.search_values("dim_products", "brand", "gucci")

        assert [(match.value, match.similarity, match.row_count) for match in found.matches] == [
            ("Gucci", 1.0, 12),
            ("Gucchi", 0.5, 3),
        ]
        assert found.hint is None

    def test_no_match_carries_the_hint(self, fake_tools: WarehouseTools, fake_database: Mock) -> None:
        fake_database.fetch.return_value = _result(("value", "similarity", "row_count"), [])

        found = fake_tools.search_values("dim_products", "brand", "qqqzzz")

        assert found.matches == ()
        assert found.hint == NO_MATCH_HINT

    @pytest.mark.parametrize("limit", [1, 50])
    def test_limit_bounds_are_inclusive(self, fake_tools: WarehouseTools, fake_database: Mock, limit: int) -> None:
        fake_tools.search_values("dim_products", "brand", "x", limit=limit)

        assert fake_database.fetch.call_args.kwargs["max_rows"] == limit

    @pytest.mark.parametrize("limit", [0, 51, -1])
    def test_limit_outside_one_to_fifty_is_refused_before_any_query(
        self, fake_tools: WarehouseTools, fake_database: Mock, limit: int
    ) -> None:
        with pytest.raises(WarehouseToolError, match=rf"^limit must be between 1 and 50, got {limit}$"):
            fake_tools.search_values("dim_products", "brand", "x", limit=limit)

        fake_database.fetch.assert_not_called()

    @pytest.mark.parametrize(
        ("table", "column", "message"),
        [
            ("dim_products", "supplier_email", r"^dim_products\.supplier_email holds PII and cannot be searched$"),
            ("dim_customers", "EMAIL", r"^dim_customers\.email holds PII and cannot be searched$"),
            (
                "dim_products",
                "product_id",
                r"^dim_products\.product_id is bigint, not text; only text columns can be searched$",
            ),
            (
                "dim_products",
                "nope",
                r"^unknown column 'nope' in dim_products; valid columns: brand, product_id, supplier_email$",
            ),
        ],
    )
    def test_pii_non_text_and_unknown_columns_are_refused_before_any_query(
        self, fake_tools: WarehouseTools, fake_database: Mock, table: str, column: str, message: str
    ) -> None:
        with pytest.raises(WarehouseToolError, match=message):
            fake_tools.search_values(table, column, "x")

        fake_database.fetch.assert_not_called()


class TestValidateSql:
    def test_a_guard_refusal_never_reaches_explain(self, fake_tools: WarehouseTools, fake_database: Mock) -> None:
        validation = fake_tools.validate_sql("select c from marts.dim_customers c")

        assert not validation.ok
        assert len(validation.errors) == 1
        assert validation.errors[0].startswith("[guard:whole_row]")
        fake_database.explain.assert_not_called()

    def test_an_accepted_query_is_explained_as_executed(self, fake_tools: WarehouseTools, fake_database: Mock) -> None:
        validation = fake_tools.validate_sql("select order_id from marts.fct_orders ;; \n")

        assert validation.ok
        assert validation.errors == ()
        assert validation.tables == ("marts.fct_orders",)
        assert validation.warnings == ()
        fake_database.explain.assert_called_once_with("select order_id from marts.fct_orders")
        fake_database.scalar.assert_not_called()  # no clock call, so no as-of lookup

    def test_a_planner_error_is_returned_not_raised(self, fake_tools: WarehouseTools, fake_database: Mock) -> None:
        fake_database.explain.side_effect = WarehouseToolError('[sql] column "nope" does not exist (at character 8)')

        validation = fake_tools.validate_sql("select nope from marts.fct_orders")

        assert validation.ok is False
        assert validation.errors == ('[sql] column "nope" does not exist (at character 8)',)
        assert validation.tables == ("marts.fct_orders",)

    def test_a_query_reading_the_clock_warns_with_the_as_of_date(
        self, fake_tools: WarehouseTools, fake_database: Mock
    ) -> None:
        fake_database.scalar.return_value = dt.date(2025, 12, 31)

        validation = fake_tools.validate_sql(
            "select count(*) from marts.fct_orders where order_date >= current_date - 30"
        )

        assert validation.ok
        assert validation.warnings == (
            "current_date reads the real clock, but the warehouse is as of 2025-12-31; "
            "derive periods from (select max(order_date) from marts.fct_orders)",
        )
        fake_database.scalar.assert_called_once_with(AS_OF_QUERY)

    def test_allowed_functions_pass_in_upper_case(self, fake_tools: WarehouseTools, fake_database: Mock) -> None:
        fake_database.scalar.return_value = dt.date(2025, 12, 31)

        validation = fake_tools.validate_sql(
            "SELECT UPPER(current_status), COUNT(*), SUM(net_revenue), COALESCE(MAX(order_date), CURRENT_DATE) "
            "FROM marts.fct_orders GROUP BY 1"
        )

        assert validation.errors == ()
        assert validation.ok


class TestRunSql:
    @pytest.mark.parametrize(
        "text",
        [
            "select order_id from marts.fct_orders limit 2;;",
            "select order_id from marts.fct_orders limit 2 ; ;  \n",
            "select order_id from marts.fct_orders limit 2;",
            "select order_id from marts.fct_orders limit 2",
        ],
    )
    def test_every_trailing_semicolon_is_dropped_before_execution(
        self, fake_tools: WarehouseTools, fake_database: Mock, text: str
    ) -> None:
        executed = "select order_id from marts.fct_orders limit 2"

        result = fake_tools.run_sql(text)

        fake_database.fetch_for_model.assert_called_once_with(executed)
        assert result.execution_id == hashlib.sha256(executed.encode()).hexdigest()[:16]

    def test_a_trailing_comment_is_executed_unchanged(self, fake_tools: WarehouseTools, fake_database: Mock) -> None:
        text = "select order_id from marts.fct_orders limit 2; -- why"

        fake_tools.run_sql(text)

        fake_database.fetch_for_model.assert_called_once_with(text)

    def test_a_refused_query_never_reaches_the_database(self, fake_tools: WarehouseTools, fake_database: Mock) -> None:
        with pytest.raises(WarehouseToolError, match=r"^\[guard:whole_row\]"):
            fake_tools.run_sql("select c from marts.dim_customers c")

        fake_database.fetch_for_model.assert_not_called()

    def test_rows_are_masked_by_the_pii_columns_of_every_mart(
        self, fake_tools: WarehouseTools, fake_database: Mock
    ) -> None:
        fake_database.fetch_for_model.return_value = _result(
            ("brand", "supplier_email"), [("acme", "a@b.com")], column_types=("text", "text"), truncated=True
        )

        result = fake_tools.run_sql("select brand, supplier_email from marts.dim_products")

        assert result.columns == ("brand", "supplier_email")
        assert result.column_types == ("text", "text")
        assert result.rows == (("acme", "***"),)
        assert result.returned_rows == 1
        assert result.truncated

    def test_a_database_error_reaches_the_caller_unchanged(
        self, fake_tools: WarehouseTools, fake_database: Mock
    ) -> None:
        fake_database.fetch_for_model.side_effect = WarehouseToolError("[timeout] the query took longer than 5 s")

        with pytest.raises(WarehouseToolError, match=r"^\[timeout\] the query took longer than 5 s$"):
            fake_tools.run_sql("select order_id from marts.fct_orders")


def _result(
    columns: tuple[str, ...],
    rows: Sequence[tuple[object, ...]],
    *,
    column_types: tuple[str, ...] = (),
    truncated: bool = False,
) -> QueryResult:
    return QueryResult(
        columns=columns,
        rows=tuple(rows),
        returned_rows=len(rows),
        truncated=truncated,
        elapsed_ms=0,
        column_types=column_types,
    )


def _set_dates(database: Mock, *, first: dt.date, latest: dt.date) -> None:
    dates = {AS_OF_QUERY: latest, FIRST_ORDER_QUERY: first}
    database.scalar.side_effect = lambda query, params=(): dates[query]


def _stored_values(database: Mock, stored: dict[str, list[str]]) -> None:
    """Answer the stored-values queries like the database: at most `max_rows` of a column's values, `truncated` when
    there are more."""

    def fetch(query: Any, params: Sequence[object] = (), max_rows: int = 100, **_: object) -> QueryResult:
        values = stored[_value_query_column(query.as_string())]
        return _result(("v",), [(value,) for value in values[:max_rows]], truncated=len(values) > max_rows)

    database.fetch.side_effect = fetch


def _value_queries(database: Mock) -> list[tuple[str, str]]:
    """(column, query text) of every stored-values query so far."""
    texts = [item.args[0].as_string() for item in database.fetch.call_args_list]
    return [(_value_query_column(text), text) for text in texts]


def _value_query_column(text: str) -> str:
    found = re.match(r'select "(\w+)" from "marts"', text)
    assert found, text
    return found.group(1)


class TestToolsWithoutArtifacts:
    def test_tools_report_missing_artifacts(self, tmp_path: Path, fake_database: Mock) -> None:
        tools = WarehouseTools(MartCatalogLoader(tmp_path), fake_database, ExampleIndex([]))
        # Every tool that reads the catalog; get_examples is covered separately below.
        calls = (
            tools.get_context,
            tools.list_tables,
            lambda: tools.describe_table("fct_orders"),
            lambda: tools.search_values("dim_products", "brand", "x"),
            tools.get_metric_definitions,
            lambda: tools.validate_sql("select 1"),
            lambda: tools.run_sql("select 1"),
        )

        for call_tool in calls:
            with pytest.raises(WarehouseToolError, match=r"^\[unavailable\] .*make build-prod"):
                call_tool()

        # The catalog is read first: the database is never touched.
        assert fake_database.method_calls == []

    def test_get_examples_is_static_and_needs_no_artifacts(
        self, tmp_path: Path, fake_database: Mock, examples_path: Path
    ) -> None:
        # get_examples reads examples.yaml only, so it is intentionally independent of the dbt catalog.
        tools = WarehouseTools(MartCatalogLoader(tmp_path), fake_database, ExampleIndex.from_yaml(examples_path))

        assert len(tools.get_examples("net revenue by month", k=2).examples) == 2


def _only_span(spans: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matching = [span for span in spans if span["name"] == name]
    assert len(matching) == 1, [span["name"] for span in spans]
    return matching[0]
