"""`sherpa ask` (agent spec §5.5): plan, optionally confirm, answer, render. Stdout carries the result (or, with
`--json`, only the final outcome); progress, prompts and errors go to stderr."""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Annotated, Final, Protocol

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.text import Text
from sherpa_mcp.telemetry import configure

from sherpa_agent import AgentError
from sherpa_agent.outcomes import Answered, Correction, Draft, Failed, NeedsClarification, Unanswerable
from sherpa_agent.prices import PRICES_PATH, Prices
from sherpa_agent.render import render_draft, render_outcome, render_progress
from sherpa_agent.service import SERVICE_NAME, AgentService, mcp_toolset
from sherpa_agent.settings import AgentSettings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

    from rich.console import RenderableType

    from sherpa_agent.outcomes import AnswerOutcome, PlanOutcome, Step

type Terminal = Answered | NeedsClarification | Unanswerable | Failed

EXIT_ANSWERED: Final = 0
EXIT_FAILED: Final = 1
EXIT_CLARIFY: Final = 3
EXIT_UNANSWERABLE: Final = 4
EXIT_ABORTED: Final = 5
LOG_FORMAT: Final = "%(asctime)s %(levelname)s %(name)s: %(message)s"
CHOICE_RUN: Final = "run"
CHOICE_CORRECT: Final = "correct"
CHOICE_ABORT: Final = "abort"
CHOICES: Final = (CHOICE_RUN, CHOICE_CORRECT, CHOICE_ABORT)
CONFIRM_PROMPT: Final = "Run, correct or abort?"
CORRECTION_PROMPT: Final = "Correction:"
INVALID_CONFIGURATION: Final = "invalid configuration: {details}"

app = typer.Typer(help="SQL Sherpa text-to-SQL agent (agent spec §5.5).", no_args_is_help=True, add_completion=False)


class AgentRun(Protocol):
    """What the CLI needs from `AgentService` (the tests give it a fake)."""

    def plan(
        self,
        question: str,
        corrections: Sequence[Correction] = (),
        on_progress: Callable[[Step], Awaitable[None]] | None = None,
        *,
        variant: str | None = None,
    ) -> Awaitable[PlanOutcome]: ...

    def answer(
        self, draft: Draft, on_progress: Callable[[Step], Awaitable[None]] | None = None, *, variant: str | None = None
    ) -> Awaitable[AnswerOutcome]: ...


@app.callback()
def main() -> None:
    logging.basicConfig(level=logging.WARNING, format=LOG_FORMAT)  # stderr


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="The question, in natural language.")],
    variant: Annotated[
        str | None, typer.Option("--variant", help="The variant to run (default: the configured one).")
    ] = None,
    confirm: Annotated[bool, typer.Option("--confirm", help="Show the draft and ask: run, correct or abort.")] = False,
    json_output: Annotated[bool, typer.Option("--json", help="Print only the final outcome, as JSON.")] = False,
) -> None:
    err = Console(stderr=True, highlight=False)
    try:
        code = asyncio.run(AskCommand(question, variant, confirm=confirm, as_json=json_output).run())
    except AgentError as error:
        err.print(Text(f"error: {error}"), soft_wrap=True)
        raise typer.Exit(EXIT_FAILED) from error
    raise typer.Exit(code)


class AskCommand:
    def __init__(self, question: str, variant: str | None, *, confirm: bool, as_json: bool) -> None:
        self._question = question
        self._variant = variant
        self._confirm = confirm
        self._as_json = as_json
        self._err = Console(stderr=True, highlight=False)
        self._out = self._err if as_json else Console(highlight=False)  # stdout is the JSON outcome alone

    async def run(self) -> int:
        settings = load_settings()
        provider = configure(SERVICE_NAME, settings.otel_exporter_otlp_endpoint)
        try:
            async with open_service(settings) as service:
                return await self._ask(service, settings.trace_url_template)
        finally:
            provider.force_flush()

    async def _ask(self, service: AgentRun, trace_url_template: str | None) -> int:
        try:
            planned = await self._plan_until_run(service)
        except _Aborted as aborted:
            self._err.print(Text("Aborted."))
            if self._as_json:
                typer.echo(aborted.draft.model_dump_json())
            return EXIT_ABORTED
        if not isinstance(planned, Draft):
            return self._finish(planned, trace_url_template)
        return self._finish(
            await service.answer(planned, self._show_progress, variant=self._variant), trace_url_template
        )

    async def _plan_until_run(self, service: AgentRun) -> PlanOutcome:
        """Plans until the person accepts a draft (or `--confirm` is off); each `correct` adds one correction."""
        corrections: list[Correction] = []
        while True:
            planned = await service.plan(self._question, corrections, self._show_progress, variant=self._variant)
            if not (self._confirm and isinstance(planned, Draft)):
                return planned
            self._show(render_draft(planned))
            match await asyncio.to_thread(self._choose):
                case "run":
                    return planned
                case "correct":
                    text = await asyncio.to_thread(self._read_line, CORRECTION_PROMPT)
                    if not text:
                        raise _Aborted(planned)
                    corrections.append(Correction(action="free_text", original=None, value=text))
                case _:
                    raise _Aborted(planned)

    def _choose(self) -> str:
        """The person's choice; an unknown answer is asked again, and the end of input is an abort."""
        while True:
            answer = self._read_line(CONFIRM_PROMPT)
            if answer is None:
                return CHOICE_ABORT
            if answer.lower() in CHOICES:
                return answer.lower()
            self._err.print(Text(f"Please answer one of: {', '.join(CHOICES)}."))

    def _read_line(self, prompt: str) -> str | None:
        """Reads one line of stdin; the prompt goes to stderr, so `--json` keeps stdout clean."""
        self._err.print(Text(f"{prompt} "), end="")
        line = sys.stdin.readline()
        return line.strip() if line else None

    def _finish(self, outcome: Terminal, trace_url_template: str | None) -> int:
        if self._as_json:
            typer.echo(outcome.model_dump_json())
        else:
            self._show(render_outcome(outcome, trace_url_template))
        match outcome:
            case Answered():
                return EXIT_ANSWERED
            case NeedsClarification():
                return EXIT_CLARIFY
            case Unanswerable():
                return EXIT_UNANSWERABLE
            case Failed():
                return EXIT_FAILED

    async def _show_progress(self, step: Step) -> None:
        self._out.print(render_progress(step), soft_wrap=True)

    def _show(self, renderables: Sequence[RenderableType]) -> None:
        for renderable in renderables:
            # Text is left to the terminal to wrap: a SQL line or a trace link stays copyable.
            self._out.print(renderable, soft_wrap=isinstance(renderable, Text))


@asynccontextmanager
async def open_service(settings: AgentSettings) -> AsyncIterator[AgentRun]:
    """The service over one MCP session: the stdio server is a single subprocess for plan and answer alike. The
    variants enter the toolset too; MCPToolset counts its entries, so theirs are nested inside this one."""
    toolset = mcp_toolset(settings)
    async with toolset:
        yield AgentService(settings, toolset, prices=Prices.load(PRICES_PATH))


def load_settings() -> AgentSettings:
    try:
        return AgentSettings()  # type: ignore[call-arg]  # the required fields come from the environment
    except ValidationError as error:
        details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors())
        raise AgentError(INVALID_CONFIGURATION.format(details=details)) from error


class _Aborted(Exception):
    def __init__(self, draft: Draft) -> None:
        super().__init__("aborted at --confirm")
        self.draft = draft
