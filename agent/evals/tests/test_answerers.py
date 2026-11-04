from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sherpa_evals.answerers import ANSWERERS, OracleAnswerer
from sherpa_evals.cases import GoldSet

if TYPE_CHECKING:
    from collections.abc import Callable

    from sherpa_evals.cases import GoldCase


def gold_of(*cases: GoldCase) -> GoldSet:
    return GoldSet(gold_version=1, dataset_version=1, cases=cases)


@pytest.mark.anyio
class TestOracleAnswerer:
    async def test_answered_case_gets_its_gold_sql(self, make_case: Callable[..., GoldCase]) -> None:
        case = make_case("values-001", gold_sql="select 7 as n from marts.fct_orders")

        attempt = await OracleAnswerer(gold_of(case)).attempt(case.question)

        assert attempt.outcome == "answered"
        assert attempt.sql == "select 7 as n from marts.fct_orders"

    async def test_unanswerable_case_gets_no_sql(self, make_case: Callable[..., GoldCase]) -> None:
        case = make_case(
            "unanswerable-001", expected_outcome="unanswerable", gold_sql=None, tables=(), question="Who is Bob?"
        )

        attempt = await OracleAnswerer(gold_of(case)).attempt("Who is Bob?")

        assert attempt.outcome == "unanswerable"
        assert attempt.sql is None

    async def test_stats_are_zero(self, make_case: Callable[..., GoldCase]) -> None:
        case = make_case("values-001")

        attempt = await OracleAnswerer(gold_of(case)).attempt(case.question)

        assert attempt.stats.latency_ms == 0
        assert attempt.stats.usage == ()
        assert attempt.stats.trace_id is None

    async def test_unknown_question_raises_key_error(self, make_case: Callable[..., GoldCase]) -> None:
        oracle = OracleAnswerer(gold_of(make_case("values-001")))

        with pytest.raises(KeyError):
            await oracle.attempt("A question nobody wrote?")


def test_the_oracle_is_registered() -> None:
    assert set(ANSWERERS) == {"oracle"}
