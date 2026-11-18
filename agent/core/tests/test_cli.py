from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, Protocol

import pytest
from builders import (
    TRACE_ID,
    make_answered,
    make_clarification,
    make_draft,
    make_failed,
    make_stats,
    make_unanswerable,
)
from mcp.shared.exceptions import MCPError
from mcp.types import CONNECTION_CLOSED, INVALID_PARAMS, REQUEST_TIMEOUT
from pydantic import TypeAdapter
from pydantic_ai.toolsets import FunctionToolset
from sherpa_agent import AgentError, cli
from sherpa_agent.cli import (
    EXIT_ABORTED,
    EXIT_ANSWERED,
    EXIT_CLARIFY,
    EXIT_FAILED,
    EXIT_UNANSWERABLE,
    app,
)
from sherpa_agent.outcomes import AnswerOutcome, Correction, PlanOutcome
from sherpa_agent.settings import AgentSettings
from typer.testing import CliRunner

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from sherpa_agent.outcomes import Draft

runner = CliRunner()


class FakeService:
    """Returns the outcomes it is given, one per call (the last one repeating), and records the calls."""

    def __init__(self, plans: Sequence[PlanOutcome], answers: Sequence[AnswerOutcome] = ()) -> None:
        self._plans = list(plans)
        self._answers = list(answers)
        self.plan_calls: list[tuple[str, tuple[Correction, ...], str | None]] = []
        self.answer_calls: list[tuple[Draft, str | None]] = []

    async def plan(
        self,
        question: str,
        corrections: Sequence[Correction] = (),
        on_progress: Any = None,
        *,
        variant: str | None = None,
    ) -> PlanOutcome:
        self.plan_calls.append((question, tuple(corrections), variant))
        if on_progress is not None:
            await on_progress(make_draft().steps[0])
        return self._plans.pop(0) if len(self._plans) > 1 else self._plans[0]

    async def answer(self, draft: Draft, on_progress: Any = None, *, variant: str | None = None) -> AnswerOutcome:
        self.answer_calls.append((draft, variant))
        return self._answers[0]


class Install(Protocol):
    def __call__(self, service: FakeService, trace_url_template: str | None = None) -> FakeService: ...


@pytest.fixture
def install(monkeypatch: pytest.MonkeyPatch) -> Install:
    """Replaces the service the CLI opens and its settings (no `.env`, so the trace template is the test's)."""

    def _install(service: FakeService, trace_url_template: str | None = None) -> FakeService:
        @asynccontextmanager
        async def open_service(settings: AgentSettings) -> AsyncIterator[FakeService]:
            yield service

        def load_settings() -> AgentSettings:
            return AgentSettings(_env_file=None, trace_url_template=trace_url_template)  # type: ignore[call-arg]

        monkeypatch.setattr(cli, "open_service", open_service)
        monkeypatch.setattr(cli, "load_settings", load_settings)
        return service

    return _install


class TestExitCodes:
    @pytest.mark.parametrize(
        ("plan", "answer", "code", "shown"),
        [
            (make_draft(), make_answered(), EXIT_ANSWERED, "Spain made the most."),
            (make_clarification(), None, EXIT_CLARIFY, "Best by what?"),
            (make_unanswerable(), None, EXIT_UNANSWERABLE, "inventory is not in the warehouse"),
            (make_failed(), None, EXIT_FAILED, "budget exceeded"),
            (make_draft(), make_failed(reason="answer could not be verified"), EXIT_FAILED, "could not be verified"),
        ],
    )
    def test_each_outcome_has_its_code_and_sections(
        self, install: Install, plan: PlanOutcome, answer: AnswerOutcome | None, code: int, shown: str
    ) -> None:
        install(FakeService([plan], [answer] if answer else []))

        result = runner.invoke(app, ["ask", "revenue by country"])

        assert result.exit_code == code
        assert shown in result.stdout
        assert "4.2 s" in result.stdout

    def test_exit_codes_are_the_specs(self) -> None:
        assert (EXIT_ANSWERED, EXIT_FAILED, EXIT_CLARIFY, EXIT_UNANSWERABLE, EXIT_ABORTED) == (0, 1, 3, 4, 5)

    def test_a_refusal_never_calls_answer(self, install: Install) -> None:
        service = install(FakeService([make_unanswerable()]))

        runner.invoke(app, ["ask", "inventory?"])

        assert service.answer_calls == []

    def test_progress_is_printed_before_the_answer(self, install: Install) -> None:
        install(FakeService([make_draft()], [make_answered()]))

        out = runner.invoke(app, ["ask", "q"]).stdout

        assert out.index("s1 scope: get_context") < out.index("Spain made the most.")

    def test_the_variant_is_passed_to_plan_and_answer(self, install: Install) -> None:
        service = install(FakeService([make_draft()], [make_answered()]))

        runner.invoke(app, ["ask", "q", "--variant", "v0_baseline"])

        assert service.plan_calls[0][2] == "v0_baseline"
        assert service.answer_calls[0][1] == "v0_baseline"

    def test_the_answer_receives_the_draft(self, install: Install) -> None:
        draft = make_draft(draft_id="d-42")
        service = install(FakeService([draft], [make_answered()]))

        runner.invoke(app, ["ask", "q"])

        assert service.answer_calls[0][0] == draft


class TestFooter:
    def test_the_template_builds_the_trace_link(self, install: Install) -> None:
        install(FakeService([make_unanswerable()]), "http://127.0.0.1:16686/trace/{trace_id}")

        out = runner.invoke(app, ["ask", "q"]).stdout

        assert f"trace: http://127.0.0.1:16686/trace/{TRACE_ID}" in out

    def test_without_a_template_the_bare_trace_id(self, install: Install) -> None:
        install(FakeService([make_unanswerable()]))

        out = runner.invoke(app, ["ask", "q"]).stdout

        assert f"trace: {TRACE_ID}" in out


class TestJson:
    def test_stdout_is_exactly_the_final_outcome(self, install: Install) -> None:
        answered = make_answered(stats=make_stats())
        install(FakeService([make_draft()], [answered]))

        result = runner.invoke(app, ["ask", "q", "--json"])

        assert result.exit_code == EXIT_ANSWERED
        assert result.stdout == answered.model_dump_json() + "\n"

    @pytest.mark.parametrize("plan", [make_clarification(), make_unanswerable(), make_failed()])
    def test_a_plan_refusal_is_the_final_outcome(self, install: Install, plan: PlanOutcome) -> None:
        install(FakeService([plan]))

        result = runner.invoke(app, ["ask", "q", "--json"])

        assert result.stdout == plan.model_dump_json() + "\n"

    def test_progress_goes_to_stderr(self, install: Install) -> None:
        install(FakeService([make_draft()], [make_answered()]))

        result = runner.invoke(app, ["ask", "q", "--json"])

        assert "s1 scope" in result.stderr
        assert "s1 scope" not in result.stdout

    def test_the_output_validates_as_an_outcome(self, install: Install) -> None:
        install(FakeService([make_clarification()]))

        out = runner.invoke(app, ["ask", "q", "--json"]).stdout

        assert TypeAdapter(PlanOutcome).validate_python(json.loads(out)) == make_clarification()


class TestConfirm:
    def test_run_answers_the_draft(self, install: Install) -> None:
        service = install(FakeService([make_draft()], [make_answered()]))

        result = runner.invoke(app, ["ask", "q", "--confirm"], input="run\n")

        assert result.exit_code == EXIT_ANSWERED
        assert len(service.answer_calls) == 1
        assert "select country" in result.stdout

    def test_correct_replans_with_one_free_text_correction(self, install: Install) -> None:
        service = install(FakeService([make_draft(), make_draft(draft_id="d-2")], [make_answered()]))

        result = runner.invoke(app, ["ask", "q", "--confirm"], input="correct\nuse gross revenue\nrun\n")

        assert result.exit_code == EXIT_ANSWERED
        assert [call[1] for call in service.plan_calls] == [
            (),
            (Correction(action="free_text", original=None, value="use gross revenue"),),
        ]
        assert service.answer_calls[0][0].draft_id == "d-2"

    def test_corrections_accumulate_across_rounds(self, install: Install) -> None:
        service = install(FakeService([make_draft()], [make_answered()]))

        runner.invoke(app, ["ask", "q", "--confirm"], input="correct\nfirst\ncorrect\nsecond\nrun\n")

        assert [c.value for c in service.plan_calls[-1][1]] == ["first", "second"]

    def test_abort_exits_5_and_never_answers(self, install: Install) -> None:
        service = install(FakeService([make_draft()], [make_answered()]))

        result = runner.invoke(app, ["ask", "q", "--confirm"], input="abort\n")

        assert result.exit_code == EXIT_ABORTED
        assert service.answer_calls == []
        assert "Spain made the most." not in result.stdout

    def test_the_draft_is_shown_before_the_prompt(self, install: Install) -> None:
        install(FakeService([make_draft()], [make_answered()]))

        result = runner.invoke(app, ["ask", "q", "--confirm"], input="abort\n")

        assert "select country" in result.stdout
        assert "net_revenue" in result.stdout

    def test_a_plan_refusal_is_not_confirmed(self, install: Install) -> None:
        install(FakeService([make_unanswerable()]))

        result = runner.invoke(app, ["ask", "q", "--confirm"])

        assert result.exit_code == EXIT_UNANSWERABLE

    def test_json_keeps_stdout_clean_and_shows_the_draft_on_stderr(self, install: Install) -> None:
        answered = make_answered()
        install(FakeService([make_draft()], [answered]))

        result = runner.invoke(app, ["ask", "q", "--confirm", "--json"], input="run\n")

        assert result.stdout == answered.model_dump_json() + "\n"
        assert "select country" in result.stderr

    def test_json_abort_prints_the_draft(self, install: Install) -> None:
        draft = make_draft()
        install(FakeService([draft]))

        result = runner.invoke(app, ["ask", "q", "--confirm", "--json"], input="abort\n")

        assert result.exit_code == EXIT_ABORTED
        assert result.stdout == draft.model_dump_json() + "\n"

    def test_an_unknown_choice_is_asked_again(self, install: Install) -> None:
        service = install(FakeService([make_draft()], [make_answered()]))

        result = runner.invoke(app, ["ask", "q", "--confirm"], input="maybe\nrun\n")

        assert result.exit_code == EXIT_ANSWERED
        assert len(service.answer_calls) == 1

    def test_the_end_of_input_aborts(self, install: Install) -> None:
        service = install(FakeService([make_draft()], [make_answered()]))

        result = runner.invoke(app, ["ask", "q", "--confirm"], input="")

        assert result.exit_code == EXIT_ABORTED
        assert service.answer_calls == []

    def test_an_empty_correction_aborts_instead_of_replanning(self, install: Install) -> None:
        service = install(FakeService([make_draft()], [make_answered()]))

        result = runner.invoke(app, ["ask", "q", "--confirm"], input="correct\n\n")

        assert result.exit_code == EXIT_ABORTED
        assert len(service.plan_calls) == 1


class TestErrors:
    def test_an_agent_error_is_a_clean_message_and_exit_1(self, install: Install) -> None:
        class Raising(FakeService):
            async def plan(self, *args: Any, **kwargs: Any) -> PlanOutcome:
                raise AgentError("unknown variant 'nope'")

        install(Raising([]))

        result = runner.invoke(app, ["ask", "q", "--variant", "nope"])

        assert result.exit_code == EXIT_FAILED
        assert "unknown variant 'nope'" in result.stderr
        assert "Traceback" not in result.output

    def test_bad_configuration_is_a_clean_message_and_exit_1(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.delenv("SHERPA_DB_HOST")
        monkeypatch.chdir(tmp_path)  # no .env here

        result = runner.invoke(app, ["ask", "q"])

        assert result.exit_code == EXIT_FAILED
        assert "db_host" in result.stderr
        assert "Traceback" not in result.output


class FailsToStart(FunctionToolset[None]):
    async def __aenter__(self) -> FailsToStart:
        raise RuntimeError("Client failed to connect: Connection closed\n" + "traceback line\n" * 5)


class StdioServerExits(FunctionToolset[None]):
    """What the client raises over stdio when the server exits during `initialize` (Postgres down)."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error

    async def __aenter__(self) -> StdioServerExits:
        raise self._error


class TestToolServerStartup:
    @pytest.mark.parametrize("code", [CONNECTION_CLOSED, REQUEST_TIMEOUT], ids=["connection-closed", "timeout"])
    def test_a_stdio_server_that_exits_during_initialize_is_a_clean_message_and_exit_1(
        self, monkeypatch: pytest.MonkeyPatch, code: int
    ) -> None:
        toolset = StdioServerExits(MCPError(code, "Connection closed"))
        monkeypatch.setattr(cli, "load_settings", lambda: AgentSettings(_env_file=None))  # type: ignore[call-arg]
        monkeypatch.setattr(cli, "mcp_toolset", lambda settings: toolset)

        result = runner.invoke(app, ["ask", "q"])

        assert result.exit_code == EXIT_FAILED
        assert "error: tool server unavailable: Connection closed" in result.stderr
        assert "Traceback" not in result.output

    def test_an_mcp_error_that_is_not_the_session_ending_is_not_hidden(self, monkeypatch: pytest.MonkeyPatch) -> None:
        toolset = StdioServerExits(MCPError(INVALID_PARAMS, "bad"))
        monkeypatch.setattr(cli, "load_settings", lambda: AgentSettings(_env_file=None))  # type: ignore[call-arg]
        monkeypatch.setattr(cli, "mcp_toolset", lambda settings: toolset)

        result = runner.invoke(app, ["ask", "q"])

        assert isinstance(result.exception, MCPError)

    def test_a_tool_server_that_does_not_start_is_a_clean_message_and_exit_1(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli, "load_settings", lambda: AgentSettings(_env_file=None))  # type: ignore[call-arg]
        monkeypatch.setattr(cli, "mcp_toolset", lambda settings: FailsToStart())

        result = runner.invoke(app, ["ask", "q"])

        assert result.exit_code == EXIT_FAILED
        assert "error: tool server unavailable: Client failed to connect: Connection closed" in result.stderr
        assert "traceback line" not in result.output
        assert "Traceback" not in result.output

    def test_any_other_runtime_error_is_not_hidden(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Broken(FunctionToolset[None]):
            async def __aenter__(self) -> Broken:
                raise RuntimeError("a bug")

        monkeypatch.setattr(cli, "load_settings", lambda: AgentSettings(_env_file=None))  # type: ignore[call-arg]
        monkeypatch.setattr(cli, "mcp_toolset", lambda settings: Broken())

        result = runner.invoke(app, ["ask", "q"])

        assert isinstance(result.exception, RuntimeError)


class CountingToolset(FunctionToolset[None]):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[str] = []

    async def __aenter__(self) -> CountingToolset:
        self.events.append("enter")
        return self

    async def __aexit__(self, *args: object) -> bool | None:
        self.events.append("exit")
        return None


@pytest.mark.anyio
class TestOpenService:
    async def test_the_toolset_is_open_for_the_whole_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        toolset = CountingToolset()
        monkeypatch.setattr(cli, "mcp_toolset", lambda settings: toolset)

        async with cli.open_service(AgentSettings(_env_file=None)):  # type: ignore[call-arg]
            during = list(toolset.events)

        assert (during, toolset.events) == (["enter"], ["enter", "exit"])
