from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

import pytest
from sherpa_ui.build.build_status import LastFullBuild, NodeResult
from sherpa_ui.lineage.manifest_graph import GraphNode, Layer, ManifestGraph, NodeTest
from sherpa_ui.lineage.node_status import NodeStatus, StatusDeriver, TestOutcome

STARTED_AT = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)
NODE_STATUS_LOGGER = "sherpa_ui.lineage.node_status"
MODEL = "model.p.fct_orders"
TEST = "test.p.not_null_fct_orders_id"
OTHER_TEST = "test.p.unique_fct_orders_id"


def _node(
    unique_id: str, resource_type: str = "model", materialization: str | None = "table", parents: tuple[str, ...] = ()
) -> GraphNode:
    return GraphNode(
        unique_id=unique_id,
        name=unique_id.split(".")[-1],
        resource_type=resource_type,
        layer=Layer.MARTS,
        materialization=materialization,
        domain=None,
        description="",
        file_path="models/x.sql",
        package_name="p",
        raw_sql=None,
        contract_enforced=False,
        columns=(),
        parents=parents,
    )


def _test(
    unique_id: str, counts_against: tuple[str, ...], kind: Literal["generic", "singular", "unit"] = "generic"
) -> NodeTest:
    return NodeTest(
        unique_id=unique_id,
        name=unique_id.split(".")[-1],
        kind=kind,
        column=None,
        severity="error",
        counts_against=counts_against,
    )


def _build(statuses: dict[str, str]) -> LastFullBuild:
    return LastFullBuild(
        invocation_id="inv",
        started_at=STARTED_AT,
        results={
            unique_id: NodeResult(
                unique_id=unique_id,
                resource_type="test" if unique_id.startswith("test.") else "model",
                status=status,
                failures=None,
                execution_time_s=None,
                message=None,
            )
            for unique_id, status in statuses.items()
        },
    )


def _deriver(
    statuses: dict[str, str] | None, *extra_nodes: GraphNode, tests: tuple[NodeTest, ...] | None = None
) -> StatusDeriver:
    nodes = {node.unique_id: node for node in (_node(MODEL), *extra_nodes)}
    default_tests = (_test(TEST, (MODEL,)), _test(OTHER_TEST, (MODEL,)))
    graph = ManifestGraph(nodes, default_tests if tests is None else tests, STARTED_AT)
    return StatusDeriver(graph, None if statuses is None else _build(statuses))


class TestLastFullBuild:
    def test_node_count_is_the_number_of_rows(self) -> None:
        build = _build({MODEL: "success", TEST: "pass"})

        assert build.node_count == 2


class TestStatusDeriver:
    @pytest.mark.parametrize(
        ("status", "outcome"),
        [
            ("pass", TestOutcome.PASSED),
            ("warn", TestOutcome.WARNED),
            ("fail", TestOutcome.FAILED),
            ("error", TestOutcome.FAILED),
            ("runtime error", TestOutcome.FAILED),
            ("skipped", TestOutcome.SKIPPED),
        ],
    )
    def test_a_test_row_maps_to_its_outcome(self, status: str, outcome: TestOutcome) -> None:
        deriver = _deriver({TEST: status})

        assert deriver.test_outcome(_test(TEST, (MODEL,))) == outcome

    def test_a_test_without_a_row_was_not_run(self) -> None:
        deriver = _deriver({})

        assert deriver.test_outcome(_test(TEST, (MODEL,))) == TestOutcome.NOT_RUN

    @pytest.mark.parametrize("status", ["error", "runtime error"])
    def test_own_error_row_fails_the_node(self, status: str) -> None:
        assert _deriver({MODEL: status}).node_status(MODEL) == NodeStatus.FAILED

    def test_a_failed_counted_against_test_fails_the_node(self) -> None:
        assert _deriver({MODEL: "success", TEST: "fail"}).node_status(MODEL) == NodeStatus.FAILED

    def test_own_skipped_row_skips_the_node(self) -> None:
        assert _deriver({MODEL: "skipped"}).node_status(MODEL) == NodeStatus.SKIPPED

    def test_a_warned_test_warns_the_node(self) -> None:
        assert _deriver({MODEL: "success", TEST: "warn"}).node_status(MODEL) == NodeStatus.WARNED

    @pytest.mark.parametrize("status", ["success", "partial success", "no-op"])
    def test_own_success_row_is_ok(self, status: str) -> None:
        assert _deriver({MODEL: status, TEST: "pass"}).node_status(MODEL) == NodeStatus.OK

    def test_no_row_is_not_in_build(self) -> None:
        assert _deriver({}).node_status(MODEL) == NodeStatus.NOT_IN_BUILD

    def test_no_row_for_an_ephemeral_model_is_inlined(self) -> None:
        ephemeral = _node("model.p.eph", materialization="ephemeral")

        assert _deriver({}, ephemeral).node_status("model.p.eph") == NodeStatus.INLINED

    def test_error_wins_over_skipped_and_warned(self) -> None:
        assert _deriver({MODEL: "skipped", TEST: "warn", OTHER_TEST: "fail"}).node_status(MODEL) == NodeStatus.FAILED

    def test_skipped_wins_over_warned(self) -> None:
        assert _deriver({MODEL: "skipped", TEST: "warn"}).node_status(MODEL) == NodeStatus.SKIPPED

    def test_warned_wins_over_success(self) -> None:
        assert _deriver({MODEL: "success", TEST: "warn", OTHER_TEST: "pass"}).node_status(MODEL) == NodeStatus.WARNED

    def test_a_seed_and_a_snapshot_follow_the_model_rules(self) -> None:
        seed = _node("seed.p.countries", "seed")
        snapshot = _node("snapshot.p.snap", "snapshot")
        deriver = _deriver({seed.unique_id: "success", snapshot.unique_id: "error"}, seed, snapshot)

        assert deriver.node_status(seed.unique_id) == NodeStatus.OK
        assert deriver.node_status(snapshot.unique_id) == NodeStatus.FAILED

    def test_sources_and_exposures_are_always_none(self) -> None:
        source = _node("source.p.shop.orders", "source", None)
        exposure = _node("exposure.p.dash", "exposure", None)

        with_build = _deriver({}, source, exposure)
        without_build = _deriver(None, source, exposure)

        assert with_build.node_status(source.unique_id) == NodeStatus.NONE
        assert with_build.node_status(exposure.unique_id) == NodeStatus.NONE
        assert without_build.node_status(source.unique_id) == NodeStatus.NONE
        assert without_build.node_status(exposure.unique_id) == NodeStatus.NONE

    def test_a_failing_singular_test_fails_every_model_it_depends_on(self) -> None:
        other = _node("model.p.dim_customers")
        singular = _test("test.p.assert_match", (MODEL, other.unique_id), kind="singular")
        deriver = _deriver(
            {MODEL: "success", other.unique_id: "success", singular.unique_id: "fail"}, other, tests=(singular,)
        )

        assert deriver.node_status(MODEL) == NodeStatus.FAILED
        assert deriver.node_status(other.unique_id) == NodeStatus.FAILED

    def test_a_warning_is_not_a_failure(self) -> None:
        status = _deriver({MODEL: "success", TEST: "warn"}).node_status(MODEL)

        assert status == NodeStatus.WARNED
        assert status != NodeStatus.FAILED

    def test_a_failed_model_marks_downstream_skipped_not_failed(self) -> None:
        child = _node("model.p.dim_customers", parents=(MODEL,))
        deriver = _deriver({MODEL: "error", child.unique_id: "skipped"}, child, tests=())

        assert deriver.node_status(MODEL) == NodeStatus.FAILED
        assert deriver.node_status(child.unique_id) == NodeStatus.SKIPPED

    def test_unknown_status_is_logged_and_treated_as_no_row(self, caplog: pytest.LogCaptureFixture) -> None:
        deriver = _deriver({MODEL: "exploded", TEST: "weird"})

        with caplog.at_level(logging.WARNING, logger=NODE_STATUS_LOGGER):
            status = deriver.node_status(MODEL)
            outcome = deriver.test_outcome(_test(TEST, (MODEL,)))

        assert status == NodeStatus.NOT_IN_BUILD
        assert outcome == TestOutcome.NOT_RUN
        assert "exploded" in caplog.text
        assert "weird" in caplog.text

    def test_no_build_gives_no_status_not_not_in_build(self) -> None:
        deriver = _deriver(None)

        assert deriver.node_status(MODEL) == NodeStatus.NO_BUILD
        assert deriver.test_outcome(_test(TEST, (MODEL,))) == TestOutcome.NOT_RUN
