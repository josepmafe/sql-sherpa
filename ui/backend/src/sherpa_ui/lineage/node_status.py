"""A node's status in the last full build, and a test's outcome (UI spec §6.5)."""

from __future__ import annotations

import logging
from enum import StrEnum, auto
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from sherpa_ui.build.build_status import LastFullBuild
    from sherpa_ui.lineage.manifest_graph import ManifestGraph, NodeTest

logger = logging.getLogger(__name__)

NO_STATUS_TYPES: Final = frozenset({"source", "exposure"})
EPHEMERAL: Final = "ephemeral"


class NodeStatus(StrEnum):
    OK = auto()
    WARNED = auto()
    FAILED = auto()
    SKIPPED = auto()
    NOT_IN_BUILD = auto()
    INLINED = auto()
    NO_BUILD = auto()
    NONE = auto()


class TestOutcome(StrEnum):
    __test__ = False  # not a pytest class, despite the name

    PASSED = auto()
    WARNED = auto()
    FAILED = auto()
    SKIPPED = auto()
    NOT_RUN = auto()


_TEST_OUTCOMES: Final = {
    "pass": TestOutcome.PASSED,
    "warn": TestOutcome.WARNED,
    "fail": TestOutcome.FAILED,
    "error": TestOutcome.FAILED,
    "runtime error": TestOutcome.FAILED,
    "skipped": TestOutcome.SKIPPED,
}
_NODE_STATUSES: Final = {
    "success": NodeStatus.OK,
    "no-op": NodeStatus.OK,
    "error": NodeStatus.FAILED,
    "runtime error": NodeStatus.FAILED,
    "partial success": NodeStatus.FAILED,  # dbt treats it as a failure and skips the dependents
    "skipped": NodeStatus.SKIPPED,
}


class StatusDeriver:
    def __init__(self, graph: ManifestGraph, build: LastFullBuild | None) -> None:
        self._graph = graph
        self._build = build

    def node_status(self, unique_id: str) -> NodeStatus:
        node = self._graph.nodes[unique_id]
        if node.resource_type in NO_STATUS_TYPES:
            return NodeStatus.NONE

        if self._build is None:
            return NodeStatus.NO_BUILD

        own = self._own_status(unique_id)
        outcomes = {self.test_outcome(test) for test in self._graph.tests_of(unique_id)}
        if own == NodeStatus.FAILED or TestOutcome.FAILED in outcomes:
            return NodeStatus.FAILED

        if own == NodeStatus.SKIPPED:
            return NodeStatus.SKIPPED

        if TestOutcome.WARNED in outcomes:
            return NodeStatus.WARNED

        if own == NodeStatus.OK:
            return NodeStatus.OK

        return NodeStatus.INLINED if node.materialization == EPHEMERAL else NodeStatus.NOT_IN_BUILD

    def test_outcome(self, test: NodeTest) -> TestOutcome:
        status = self._recorded_status(test.unique_id)
        if status is None:
            return TestOutcome.NOT_RUN

        outcome = _TEST_OUTCOMES.get(status)
        if outcome is None:
            _log_unknown(test.unique_id, status)
            return TestOutcome.NOT_RUN

        return outcome

    def _own_status(self, unique_id: str) -> NodeStatus | None:
        status = self._recorded_status(unique_id)
        if status is None:
            return None

        derived = _NODE_STATUSES.get(status)
        if derived is None:
            _log_unknown(unique_id, status)

        return derived

    def _recorded_status(self, unique_id: str) -> str | None:
        if self._build is None or unique_id not in self._build.results:
            return None

        return self._build.results[unique_id].status


def _log_unknown(unique_id: str, status: str) -> None:
    logger.warning("unknown dbt status %r for %s; treated as no row", status, unique_id)
