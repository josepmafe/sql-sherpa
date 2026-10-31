from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sherpa_ui.lineage.column_lineage import ColumnLineage, ColumnRef, Resolution
from sherpa_ui.lineage.column_lineage_loader import ColumnLineageLoader
from sherpa_ui.lineage.manifest_loader import ManifestLoader
from sherpa_ui.lineage.node_sql import NodeSql

if TYPE_CHECKING:
    from sherpa_ui.lineage.manifest_graph import ManifestGraph
    from sherpa_ui.settings import UiSettings

pytestmark = pytest.mark.warehouse

REPO_ROOT = Path(__file__).resolve().parents[4]
ORIGIN_PREFIXES = ("source.", "seed.", "snapshot.")  # where a trace is read off: the project's raw inputs
SHOP = "source.sherpa.shop"

# Each set was read off the compiled SQL in target-prod/compiled by hand.
TRACES = [
    pytest.param(
        "model.sherpa.fct_orders",
        "net_revenue",
        {
            # gross_revenue - discount_amount - refunded_revenue, zeroed on a cancelled order (status)
            (f"{SHOP}.orders", "status"),
            (f"{SHOP}.order_items", "id"),
            (f"{SHOP}.order_items", "order_id"),
            (f"{SHOP}.order_items", "quantity"),
            (f"{SHOP}.order_items", "unit_price"),
            (f"{SHOP}.order_discounts", "amount"),
            (f"{SHOP}.refunds", "amount"),
            (f"{SHOP}.refunds", "reason"),
        },
        id="net_revenue",
    ),
    pytest.param(
        "model.sherpa.fct_orders",
        "marketing_channel",
        {
            # the purchase session's channel, read from the event context and mapped by the seed; an order without
            # one is 'unknown', and the order's own sales_channel decides which of the two applies
            ("source.sherpa.events.events", "context"),
            (f"{SHOP}.orders", "sales_channel"),
            ("seed.sherpa.channel_mapping", "marketing_channel"),
        },
        id="marketing_channel",
    ),
    pytest.param(
        "model.sherpa.fct_order_items",
        "cogs",
        {
            # case when is_cancelled (fct_orders: from the order's status) then 0 else quantity * unit_cost_at_sale,
            # and the unit cost is the variant's cost_price as the snapshot captured it. The trace passes through the
            # snapshot and ends in the source it copies, so both are in the set.
            (f"{SHOP}.orders", "status"),
            (f"{SHOP}.order_items", "quantity"),
            (f"{SHOP}.product_variants", "cost_price"),
            ("snapshot.sherpa.snap_product_variants", "cost_price"),
        },
        id="cogs",
    ),
    pytest.param(
        "model.sherpa.stg_events__events",
        "occurred_at",
        {("source.sherpa.events.events", "occurred_at")},
        id="occurred_at",
    ),
]


@pytest.fixture(scope="module")
def graph_and_lineage(ui_settings: UiSettings) -> tuple[ManifestGraph, ColumnLineage]:
    target = REPO_ROOT / ui_settings.ui_dbt_target_path
    manifests = ManifestLoader(target)
    return manifests.load(), ColumnLineageLoader(target, manifests, NodeSql(target)).load()


def test_every_model_column_has_a_kind(graph_and_lineage: tuple[ManifestGraph, ColumnLineage]) -> None:
    graph, lineage = graph_and_lineage

    documented = [
        ColumnRef(unique_id=node.unique_id, column=column.name)
        for node in graph.nodes.values()
        if node.resource_type in {"model", "snapshot"}
        for column in node.columns
    ]
    infos = [lineage.info(ref) for ref in documented]

    assert documented
    assert None not in infos
    assert [info.ref for info in infos if info is not None and info.resolution == Resolution.UNRESOLVED] == []
    assert [uid for uid in graph.nodes if lineage.node_note(uid)] == []


@pytest.mark.parametrize(("unique_id", "column", "origins"), TRACES)
def test_traces_match_the_hand_checked_sources(
    graph_and_lineage: tuple[ManifestGraph, ColumnLineage], unique_id: str, column: str, origins: set[tuple[str, str]]
) -> None:
    _, lineage = graph_and_lineage

    focus = lineage.focus(ColumnRef(unique_id=unique_id, column=column))

    assert {
        (ref.unique_id, ref.column) for ref in focus.upstream if ref.unique_id.startswith(ORIGIN_PREFIXES)
    } == origins


def test_a_passthrough_with_a_cast_says_so(graph_and_lineage: tuple[ManifestGraph, ColumnLineage]) -> None:
    _, lineage = graph_and_lineage

    info = lineage.info(ColumnRef(unique_id="model.sherpa.stg_events__events", column="occurred_at"))

    assert info is not None
    assert (info.kind.value, info.notes) == ("passthrough", ("cast to timestamptz",))


def test_a_row_count_names_the_model_it_counts(graph_and_lineage: tuple[ManifestGraph, ColumnLineage]) -> None:
    _, lineage = graph_and_lineage

    info = lineage.info(ColumnRef(unique_id="model.sherpa.dim_customers", column="order_count"))

    assert info is not None
    assert (info.kind.value, info.counts) == ("row_count", "model.sherpa.fct_orders")


def test_a_trace_passes_through_an_ephemeral_model(graph_and_lineage: tuple[ManifestGraph, ColumnLineage]) -> None:
    _, lineage = graph_and_lineage
    ephemeral = "model.sherpa.int_orders__payments_summarized"

    focus = lineage.focus(ColumnRef(unique_id="model.sherpa.fct_orders", column="primary_payment_method"))

    assert ColumnRef(unique_id=ephemeral, column="primary_payment_method") in focus.upstream
    assert {(ref.unique_id, ref.column) for ref in focus.upstream if ref.unique_id.startswith("source.")} == {
        (f"{SHOP}.payments", "method")
    }
