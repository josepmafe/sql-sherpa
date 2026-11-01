"""The private helper macros of `data-platform/dbt/macros`, rendered through `dbt compile --inline`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

    from dbt.cli.main import dbtRunnerResult

SCRATCH = ("--target-path", "target-scratch")

pytestmark = pytest.mark.integration

# One expression per case, joined by "|" in the inline SQL; the rendered results come back in the same order.
AUDIT_MESSAGE_CASES: tuple[tuple[str, str], ...] = (
    # A constraint violation: the DETAIL line carries the failing row's real values, so only the first line stays.
    (
        '{{ _audit_message(\'Database Error in model fct_leads_unmasked\n  null value in column "lead_id"\n'
        "  DETAIL:  Failing row contains (null, a@example.com).') }}",
        "Database Error in model fct_leads_unmasked",
    ),
    ("{{ _audit_message('x' * 2500) | length }}", "2000"),
    ("{{ _audit_message(none) is none }}", "True"),
    ("{{ _audit_message('') }}", ""),
)


def test_audit_message_keeps_only_the_first_line(dbt: Callable[..., dbtRunnerResult]) -> None:
    inline = "|".join(expression for expression, _ in AUDIT_MESSAGE_CASES)

    result = dbt("compile", "--quiet", *SCRATCH, "--inline", inline)

    rendered = result.result.results[0].node.compiled_code.strip().split("|")
    assert rendered == [expected for _, expected in AUDIT_MESSAGE_CASES]
