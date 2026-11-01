"""`dbt run --empty` (the CI smoke run) wraps every ref and source in `(select ... where false limit 0) <alias>`."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    from dbt.cli.main import dbtRunnerResult

SCRATCH = ("--target-path", "target-scratch")

pytestmark = pytest.mark.integration

# The wrapper is itself the alias of the subquery, so `ref(...) as orders` is a syntax error (a bare alias is too,
# but is indistinguishable from a keyword here, and the CI `dbt run --empty` catches it).
DOUBLE_ALIAS = re.compile(r"_dbt_limit_subq_\w+\s+as\s+\w+")


def test_no_model_puts_an_alias_after_a_ref_or_source(dbt: Callable[..., dbtRunnerResult]) -> None:
    result = dbt("compile", "--quiet", "--empty", *SCRATCH, "--select", "resource_type:model")

    offenders = {
        node.node.name: match.group(0)
        for node in result.result.results
        if node.node.resource_type == "model" and (match := DOUBLE_ALIAS.search(node.node.compiled_code))
    }
    assert result.result.results
    assert offenders == {}
