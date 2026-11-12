"""`AgentService` (agent spec §3, §5.1, §5.3, §6): runs a variant for one question with one shared budget, fills in the
`RunStats` of every outcome and maps the failures a run can end in to `Failed`."""

from __future__ import annotations

import logging
import sys
from collections import defaultdict
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Final, cast

import anyio
from botocore.exceptions import BotoCoreError, ClientError
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from mcp.shared.exceptions import MCPError
from pydantic_ai import Agent
from pydantic_ai.exceptions import (
    ModelAPIError,
    ModelHTTPError,
    ModelRequestRejectedError,
    UnexpectedModelBehavior,
    UsageLimitExceeded,
)
from pydantic_ai.mcp import MCPError as PydanticAiMCPError
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.usage import RunUsage
from sherpa_mcp.server import STARTUP_WAIT_SECONDS
from sherpa_mcp.telemetry import configure

from sherpa_agent import AgentError
from sherpa_agent.models import build_model
from sherpa_agent.outcomes import Answered, Draft, Failed, ModelUsage, NeedsClarification, RunStats, Unanswerable
from sherpa_agent.recording import SqlAttemptsExhausted, ToolServerUnavailable, WarehouseContextUnavailable
from sherpa_agent.variants import VARIANTS, VariantContext

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from typing import Any

    from opentelemetry.trace import Span, Tracer
    from pydantic_ai.messages import ModelMessage, ModelResponse
    from pydantic_ai.models import Model, ModelRequestParameters
    from pydantic_ai.settings import ModelSettings
    from pydantic_ai.toolsets import AbstractToolset

    from sherpa_agent.outcomes import AnswerOutcome, Correction, PlanOutcome, Step, UsageSnapshot
    from sherpa_agent.prices import Prices
    from sherpa_agent.recording import RecordingToolset
    from sherpa_agent.settings import AgentSettings
    from sherpa_agent.variants import Variant

type _Outcome = Draft | NeedsClarification | Unanswerable | Failed | Answered

logger = logging.getLogger(__name__)

SERVICE_NAME: Final = "sherpa-agent"
REPO_ROOT: Final = Path(__file__).resolve().parents[4]
MCP_SERVER_COMMAND: Final = ["-c", "from sherpa_mcp.server import main; main()"]
# A call the server has not answered after this long is given up: the server's statement timeout is 5 s, so the rest is
# margin for the round trip and the server's own work.
MCP_READ_TIMEOUT_SECONDS: Final = 30
# The stdio server waits up to STARTUP_WAIT_SECONDS for the warehouse before it listens; the start must outlast that
# and the interpreter's own start-up, or the client reports a connect failure while the server is still waiting.
MCP_INIT_TIMEOUT_SECONDS: Final = STARTUP_WAIT_SECONDS + 15
MCP_MAX_RETRIES: Final = 4  # §5.3: above the SQL-attempt limit, so the framework never ends a run first
PLAN_SPAN: Final = "sherpa.plan"
ANSWER_SPAN: Final = "sherpa.answer"
UNKNOWN_VARIANT: Final = "unknown variant {name!r}; the variants are: {names}"
BUDGET_EXCEEDED: Final = "budget exceeded"
SQL_ATTEMPTS_EXHAUSTED: Final = "sql attempts exhausted"
PLAN_NOT_VALIDATED: Final = "plan could not be validated"
ANSWER_NOT_VERIFIED: Final = "answer could not be verified against results"
# Only a stable code or status goes into a reason: the provider's own text can carry account ids, role ARNs and emails,
# and reports are committed. The full text goes to the log and the span.
PROVIDER_UNAVAILABLE: Final = "model provider unavailable: {code}"
PROVIDER_REJECTED: Final = "model provider rejected the request: {status}"
TOO_MANY_REQUESTS: Final = 429
SERVER_ERROR_FROM: Final = 500
TOOL_CALLS_KEPT_FAILING: Final = "tool calls kept failing"
TOOL_SERVER_UNAVAILABLE: Final = "tool server unavailable"
CONTEXT_UNAVAILABLE: Final = "warehouse context unavailable: {detail}"
# What PydanticAI raises when a validator (or the output schema) sends the model back more often than allowed, and when
# a tool's retries run out; the other UnexpectedModelBehavior causes (a limit on the model's own behaviour) propagate.
OUTPUT_RETRIES_MESSAGE: Final = "Exceeded maximum output retries"
TOOL_RETRIES_MESSAGE: Final = "exceeded max retries count"
# What the MCP client raises when the server's session ends under a running call.
SESSION_CLOSED_MESSAGE: Final = "Server session was closed unexpectedly"


class AgentService:
    def __init__(
        self,
        settings: AgentSettings,
        toolset: AbstractToolset[Any],
        *,
        variants: Mapping[str, Variant] = VARIANTS,
        prices: Prices,
        model_for: Callable[[str], Model] | None = None,
    ) -> None:
        self._settings = settings
        self._toolset = toolset
        self._variants = variants
        self._prices = prices
        self._model_for = model_for or (lambda model_string: build_model(model_string, settings))
        provider = configure(SERVICE_NAME, settings.otel_exporter_otlp_endpoint)
        self._tracer: Tracer = provider.get_tracer(__name__)
        Agent.instrument_all(InstrumentationSettings(tracer_provider=provider))

    async def plan(
        self,
        question: str,
        corrections: Sequence[Correction] = (),
        on_progress: Callable[[Step], Awaitable[None]] | None = None,
        *,
        variant: str | None = None,
    ) -> PlanOutcome:
        chosen = self._variant(variant)
        return await self._run(
            PLAN_SPAN,
            chosen.name,
            RunUsage(),
            on_progress,
            PLAN_NOT_VALIDATED,
            lambda ctx: chosen.plan(question, corrections, ctx),
        )

    async def answer(
        self,
        draft: Draft,
        on_progress: Callable[[Step], Awaitable[None]] | None = None,
        *,
        variant: str | None = None,
    ) -> AnswerOutcome:
        chosen = self._variant(variant)
        return await self._run(
            ANSWER_SPAN,
            chosen.name,
            _usage_from(draft.usage),
            on_progress,
            ANSWER_NOT_VERIFIED,
            lambda ctx: chosen.answer(draft, ctx),
            draft_id=draft.draft_id,
        )

    def _variant(self, name: str | None) -> Variant:
        chosen = name or self._settings.default_variant
        if chosen not in self._variants:
            raise AgentError(UNKNOWN_VARIANT.format(name=chosen, names=", ".join(self._variants)))
        return self._variants[chosen]

    async def _run[T: _Outcome](
        self,
        span_name: str,
        variant: str,
        usage: RunUsage,
        on_progress: Callable[[Step], Awaitable[None]] | None,
        unverified_reason: str,
        work: Callable[[VariantContext], Awaitable[T]],
        *,
        draft_id: str | None = None,
    ) -> T | Failed:
        meter = _UsageMeter()
        ctx = VariantContext(
            settings=self._settings,
            toolset=self._toolset,
            model_for=meter.metered(self._model_for),
            usage=usage,
            on_progress=on_progress,
        )
        started = monotonic()
        with self._tracer.start_as_current_span(span_name, attributes={"variant": variant}) as span:
            try:
                outcome: T | Failed = await work(ctx)
            except Exception as error:
                failed = _failure(error, unverified_reason)
                if failed is None:
                    raise
                logger.warning("%s ended in a failure: %s (%s)", span_name, failed.reason, error)
                span.record_exception(error)
                outcome = failed
            stats = self._stats(variant, meter, ctx.recorders, int((monotonic() - started) * 1000), span)
            outcome = cast("T | Failed", outcome.model_copy(update={"stats": stats}))
            _annotate(span, outcome, stats, draft_id or (outcome.draft_id if isinstance(outcome, Draft) else None))
        return outcome

    def _stats(
        self, variant: str, meter: _UsageMeter, recorders: Sequence[RecordingToolset], latency_ms: int, span: Span
    ) -> RunStats:
        usage = meter.by_model()
        return RunStats(
            variant=variant,
            usage=usage,
            latency_ms=latency_ms,
            cost_eur=self._prices.cost_eur(usage),
            tool_calls=sum(len(step.tool_calls) for recorder in recorders for step in recorder.steps),
            sql_attempts=sum(recorder.sql_attempts for recorder in recorders),
            trace_id=format(span.get_span_context().trace_id, "032x"),
        )


def mcp_toolset(settings: AgentSettings) -> MCPToolset[None]:
    """The MCP toolset of the client's transport: a subprocess over stdio, or the running HTTP server."""
    match settings.mcp_transport:
        case "stdio":
            transport = StdioTransport(sys.executable, MCP_SERVER_COMMAND, env=server_env(settings), cwd=str(REPO_ROOT))
            return _toolset(transport)
        case "http":
            return _toolset(StreamableHttpTransport(settings.mcp_http_url))


def server_env(settings: AgentSettings) -> dict[str, str]:
    """The variables the stdio server gets (§3): the ones this client sets, never the admin login. The server reads
    `.env` from the repository root for the rest."""
    env = {
        "MCP_TRANSPORT": "stdio",
        "SHERPA_DB_HOST": settings.db_host,
        "SHERPA_DB_PORT": str(settings.db_port),
        "SHERPA_DB_NAME": settings.db_name,
        "SHERPA_AGENT_USER": settings.agent_user,
        "SHERPA_AGENT_PASSWORD": settings.agent_password.get_secret_value(),
        "SHERPA_DBT_TARGET_PATH": settings.dbt_target_path.as_posix(),
    }
    if settings.otel_exporter_otlp_endpoint:
        env["OTEL_EXPORTER_OTLP_ENDPOINT"] = settings.otel_exporter_otlp_endpoint
    return env


class _UsageMeter:
    """Records the usage of every model response of one run, by the model that answered."""

    def __init__(self) -> None:
        self._tokens: dict[str, list[int]] = defaultdict(lambda: [0, 0])

    def metered(self, model_for: Callable[[str], Model]) -> Callable[[str], Model]:
        return lambda model_string: _MeteredModel(model_for(model_string), self)

    def record(self, response: ModelResponse, fallback_name: str) -> None:
        tokens = self._tokens[response.model_name or fallback_name]
        tokens[0] += response.usage.input_tokens
        tokens[1] += response.usage.output_tokens

    def by_model(self) -> tuple[ModelUsage, ...]:
        return tuple(ModelUsage(model=name, input_tokens=i, output_tokens=o) for name, (i, o) in self._tokens.items())


class _MeteredModel(WrapperModel):
    def __init__(self, wrapped: Model, meter: _UsageMeter) -> None:
        super().__init__(wrapped)
        self._meter = meter

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        response = await super().request(messages, model_settings, model_request_parameters)
        self._meter.record(response, self.wrapped.model_name)
        return response


def _usage_from(snapshot: UsageSnapshot) -> RunUsage:
    """The answer continues the plan's budget: its request, token and tool-call limits count from here (§5.3)."""
    return RunUsage(
        requests=snapshot.requests,
        input_tokens=snapshot.input_tokens,
        output_tokens=snapshot.output_tokens,
        tool_calls=snapshot.tool_calls,
    )


def _toolset(transport: StdioTransport | StreamableHttpTransport) -> MCPToolset[None]:
    return MCPToolset(
        transport,
        max_retries=MCP_MAX_RETRIES,
        init_timeout=MCP_INIT_TIMEOUT_SECONDS,
        read_timeout=MCP_READ_TIMEOUT_SECONDS,
    )


def _failure(error: BaseException, unverified_reason: str) -> Failed | None:
    """The `Failed` outcome of an error a run can end in, or None when the error is not one of them."""
    match error:
        case UsageLimitExceeded():
            return _failed(BUDGET_EXCEEDED)
        case SqlAttemptsExhausted():
            return _failed(SQL_ATTEMPTS_EXHAUSTED, last_sql=error.last_sql, last_error=error.last_error)
        case UnexpectedModelBehavior() if error.message.startswith(OUTPUT_RETRIES_MESSAGE):
            return _failed(unverified_reason)
        case UnexpectedModelBehavior() if TOOL_RETRIES_MESSAGE in error.message:
            return _failed(TOOL_CALLS_KEPT_FAILING)
        case ModelAPIError() | BotoCoreError() | ClientError():
            return _failed(_provider_reason(error))
        case WarehouseContextUnavailable():
            return _failed(CONTEXT_UNAVAILABLE.format(detail=_first_line(str(error))))
        case (
            ToolServerUnavailable()
            | MCPError()
            | PydanticAiMCPError()
            | ConnectionError()
            | anyio.ClosedResourceError()
        ):
            return _failed(TOOL_SERVER_UNAVAILABLE)
        case RuntimeError() if SESSION_CLOSED_MESSAGE in str(error):
            return _failed(TOOL_SERVER_UNAVAILABLE)
        case BaseExceptionGroup():
            # anyio task groups wrap what a call raised; the group is a failure only when every member is one.
            failures = [_failure(member, unverified_reason) for member in error.exceptions]
            return failures[0] if failures and all(failures) else None
        case _:
            return None


def _provider_reason(error: ModelAPIError | BotoCoreError | ClientError) -> str:
    """A 4xx other than 429 is the request's fault, not an outage; everything else is the provider being unreachable."""
    match error:
        case ModelHTTPError(status_code=status) if _rejects(status):
            return PROVIDER_REJECTED.format(status=status)
        case ModelHTTPError():
            return PROVIDER_UNAVAILABLE.format(code=error.provider_error_code or error.status_code)
        case ModelRequestRejectedError():
            return PROVIDER_REJECTED.format(status=type(error).__name__)
        case ClientError():
            status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if isinstance(status, int) and _rejects(status):
                return PROVIDER_REJECTED.format(status=status)
            return PROVIDER_UNAVAILABLE.format(code=error.response.get("Error", {}).get("Code") or type(error).__name__)
        case _:
            return PROVIDER_UNAVAILABLE.format(code=type(error).__name__)


def _rejects(status: int) -> bool:
    return status < SERVER_ERROR_FROM and status != TOO_MANY_REQUESTS


def _failed(reason: str, *, last_sql: str | None = None, last_error: str | None = None) -> Failed:
    return Failed(outcome="failed", reason=reason, last_sql=last_sql, last_error=last_error)


def _annotate(span: Span, outcome: _Outcome, stats: RunStats, draft_id: str | None) -> None:
    """Run stats as span attributes. No SQL, no row values and nothing from the environment."""
    attributes: dict[str, str | int] = {
        "outcome": outcome.outcome,
        "latency_ms": stats.latency_ms,
        "tool_calls": stats.tool_calls,
        "sql_attempts": stats.sql_attempts,
        "input_tokens": sum(u.input_tokens for u in stats.usage),
        "output_tokens": sum(u.output_tokens for u in stats.usage),
    }
    if stats.cost_eur is not None:
        attributes["cost_eur"] = str(stats.cost_eur)
    if draft_id is not None:
        attributes["draft_id"] = draft_id
    span.set_attributes(attributes)


def _first_line(text: str) -> str:
    return (text.splitlines() or [""])[0]
