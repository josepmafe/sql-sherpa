from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sherpa_mcp.catalog import MartCatalogLoader
from sherpa_mcp.database import AgentDatabase, WarehouseToolError
from sherpa_mcp.examples import ExampleIndex
from sherpa_mcp.tools import WarehouseTools

if TYPE_CHECKING:
    import psycopg
    from sherpa_mcp.settings import McpSettings

REPO_ROOT = Path(__file__).resolve().parents[3]
EXAMPLES_PATH = REPO_ROOT / "agent" / "mcp-server" / "src" / "sherpa_mcp" / "examples.yaml"
MARTS_SPEC = REPO_ROOT / "data-platform" / "tests" / "dbt" / "test_agent_access.py"
METRICS = (
    *("gross_revenue", "discount_amount", "refunded_revenue", "net_revenue"),
    *("cogs", "gross_margin", "aov", "refund_rate"),
)
SIMILARITY_FLOOR = 0.2  # pg_trgm.similarity_threshold set by the tool


def _expected_marts() -> tuple[str, ...]:
    """The data platform's own list of the nine marts, loaded from its test module."""
    spec = importlib.util.spec_from_file_location("data_platform_agent_access", MARTS_SPEC)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    marts: tuple[str, ...] = module.MARTS
    return marts


@pytest.fixture(scope="module")
def prod_loader(warehouse_settings: McpSettings) -> MartCatalogLoader:
    return MartCatalogLoader(REPO_ROOT / warehouse_settings.dbt_target_path)


@pytest.fixture(scope="module")
def tools(prod_loader: MartCatalogLoader, agent_database: AgentDatabase) -> WarehouseTools:
    return WarehouseTools(prod_loader, agent_database, ExampleIndex.from_yaml(EXAMPLES_PATH))


@pytest.mark.warehouse
class TestWarehouseTools:
    def test_get_context_as_of_and_scope(self, tools: WarehouseTools, admin: psycopg.Connection) -> None:
        row = admin.execute("select max(order_date), min(order_date) from marts.fct_orders").fetchone()
        assert row is not None

        context = tools.get_context()

        assert str(context.as_of_date) == str(row[0])
        assert str(context.first_order_date) == str(row[1])
        assert any("inventory" in item for item in context.does_not_cover)
        assert set(context.domains) == {"customers", "products", "sales", "shared"}
        assert any("dim_customers" in item for item in context.covers)

    def test_list_tables_are_the_nine_marts(self, tools: WarehouseTools) -> None:
        names = [table.name for table in tools.list_tables().tables]

        assert sorted(names) == sorted(_expected_marts())

    def test_describe_table_samples_are_ordered_by_pk_and_masked(self, tools: WarehouseTools) -> None:
        description = tools.describe_table("dim_customers")

        columns = description.sample_rows.columns
        ids = [row[columns.index("customer_id")] for row in description.sample_rows.rows]
        assert ids == sorted(ids)
        assert len(ids) == 3
        assert {row[columns.index("email")] for row in description.sample_rows.rows} == {"***"}
        assert any(column.name == "email" and column.is_pii for column in description.columns)

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

    def test_get_examples_returns_k_and_every_example_runs(self, tools: WarehouseTools) -> None:
        assert len(tools.get_examples("net revenue by month", k=2).examples) == 2
        for example in ExampleIndex.from_yaml(EXAMPLES_PATH).examples:
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

    def test_run_sql_truncates_masks_and_ids(self, tools: WarehouseTools) -> None:
        big = tools.run_sql("select * from marts.fct_order_items")
        emails = tools.run_sql("select email from marts.dim_customers limit 5")
        plain = tools.run_sql("select order_id from marts.fct_orders limit 2")
        spaced = tools.run_sql("select order_id from marts.fct_orders limit 2 ;  \n")

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
        assert "explicit, safe casts" in str(error.value)
        assert email not in " ".join(validation.errors)

    @pytest.mark.parametrize(
        "query",
        [
            "select c from marts.dim_customers c limit 1",
            "select dim_customers from marts.dim_customers limit 1",
            "select to_json(c) from marts.dim_customers c limit 1",
        ],
    )
    def test_run_sql_refuses_whole_row_pii_leaks(self, tools: WarehouseTools, query: str) -> None:
        with pytest.raises(WarehouseToolError):
            tools.run_sql(query)

    def test_run_sql_rejects_hostile_sql_with_guard_errors(self, tools: WarehouseTools) -> None:
        with pytest.raises(WarehouseToolError, match="staging"):
            tools.run_sql("select * from staging.stg_shop__orders")
        with pytest.raises(WarehouseToolError):
            tools.run_sql("drop table marts.fct_orders")


class TestToolsWithoutArtifacts:
    def test_tools_report_missing_artifacts(self, tmp_path: Path) -> None:
        class NoDatabase:
            """Any use of the database would be a bug: the catalog is read first."""

        tools = WarehouseTools(MartCatalogLoader(tmp_path), NoDatabase(), ExampleIndex([]))  # type: ignore[arg-type]
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

        for call in calls:
            with pytest.raises(WarehouseToolError, match="make build-prod"):
                call()

    def test_get_examples_is_static_and_needs_no_artifacts(self, tmp_path: Path) -> None:
        # get_examples reads examples.yaml only, so it is intentionally independent of the dbt catalog.
        tools = WarehouseTools(
            MartCatalogLoader(tmp_path),
            None,  # type: ignore[arg-type]
            ExampleIndex.from_yaml(EXAMPLES_PATH),
        )

        assert len(tools.get_examples("net revenue by month", k=2).examples) == 2
