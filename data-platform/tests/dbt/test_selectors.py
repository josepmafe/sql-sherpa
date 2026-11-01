"""The `distribution` tag and the `ci` selector of `data-platform/dbt/selectors.yml` (spec 8)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    from dbt.cli.main import dbtRunnerResult

SCRATCH = ("--target-path", "target-scratch")

pytestmark = pytest.mark.integration

# Spec 8: the distribution checks are the row-count and mean expectations plus the Pareto test.
DISTRIBUTION_PREFIXES = (
    "dbt_expectations_expect_table_row_count_to_be_between",
    "dbt_expectations_expect_column_mean_to_be_between",
    "assert_buyer_revenue_pareto",
)


def test_distribution_tag_is_exactly_the_distribution_checks(dbt: Callable[..., dbtRunnerResult]) -> None:
    all_tests = _ls(dbt, "--resource-type", "test")

    tagged = _ls(dbt, "--select", "tag:distribution")

    expected = {name for name in all_tests if name.startswith(DISTRIBUTION_PREFIXES)}
    assert tagged
    assert set(tagged) == expected


def test_ci_selector_leaves_out_exactly_the_distribution_tests(dbt: Callable[..., dbtRunnerResult]) -> None:
    all_tests = _ls(dbt, "--resource-type", "test")
    tagged = _ls(dbt, "--select", "tag:distribution")

    ci_tests = _ls(dbt, "--resource-type", "test", "--selector", "ci")

    assert tagged
    assert set(ci_tests) == set(all_tests) - set(tagged)


def _ls(dbt: Callable[..., dbtRunnerResult], *args: str) -> list[str]:
    result = dbt("ls", "--output", "name", "--quiet", *SCRATCH, *args)
    return list(result.result)
