from __future__ import annotations

import sys
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import anyio
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from fakes import FakeWarehouse, validation
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from mcp.shared.exceptions import MCPError
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior
from pydantic_ai.messages import ModelRequest, ModelResponse
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage
from scripts import QUESTION, SQL, Script, answer_out, clarify, item, plan_draft, tool
from sherpa_agent import AgentError
from sherpa_agent import service as service_module
from sherpa_agent.outcomes import (
    Answered,
    Draft,
    Failed,
    Interpretation,
    NeedsClarification,
    Unanswerable,
    UsageSnapshot,
)
from sherpa_agent.prices import ModelPrice, Prices
from sherpa_agent.service import MCP_MAX_RETRIES, REPO_ROOT, AgentService, mcp_toolset, server_env
from sherpa_agent.settings import AgentSettings
from sherpa_agent.variants import VARIANTS, VariantContext
from sherpa_mcp.telemetry import configure

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from opentelemetry.sdk.trace import ReadableSpan
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model
    from sherpa_agent.outcomes import AnswerOutcome, Correction, PlanOutcome

pytestmark = pytest.mark.anyio

PRICES = Prices(
    models={"model-a": ModelPrice(input_eur_per_mtok=Decimal(3), output_eur_per_mtok=Decimal(15))},
)
PASSWORD = "s3cret-pw"


class UsageScript(Script):
    """A `Script` whose responses report 10 input and 5 output tokens, from a model called `model-a`."""

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        response = super().__call__(messages, info)
        return ModelResponse(parts=response.parts, usage=RequestUsage(input_tokens=10, output_tokens=5))

    @property
    def model(self) -> FunctionModel:
        return FunctionModel(self, model_name="model-a")


class TwoModels:
    """A variant that asks `model-a` twice and `model-b` once, and returns a refusal."""

    name = "two_models"

    async def plan(self, question: str, corrections: Sequence[Correction], ctx: VariantContext) -> PlanOutcome:
        for name in ("model-a", "model-b", "model-a"):
            await ctx.model_for(name).request([ModelRequest.user_text_prompt(question)], None, ModelRequestParameters())
        return Unanswerable(outcome="unanswerable", reason="x", closest_supported=None)

    async def answer(self, draft: Draft, ctx: VariantContext) -> AnswerOutcome:
        raise NotImplementedError


class Raising:
    """A variant whose plan and answer raise `error`."""

    name = "raising"

    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def plan(self, question: str, corrections: Sequence[Correction], ctx: VariantContext) -> PlanOutcome:
        raise self.error

    async def answer(self, draft: Draft, ctx: VariantContext) -> AnswerOutcome:
        raise self.error


@pytest.fixture
def spans() -> Any:
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    configure("sherpa-agent", None).add_span_processor(processor)
    yield exporter
    exporter.clear()  # the provider is process-wide and keeps its processors


@pytest.fixture
def warehouse() -> FakeWarehouse:
    return FakeWarehouse()


@pytest.fixture
def make_service(settings: AgentSettings, warehouse: FakeWarehouse) -> Callable[..., AgentService]:
    def build(script: Script | None = None, variants: Any = None, model: Model | None = None) -> AgentService:
        chosen = model or (script or Script(clarify([]))).model
        return AgentService(
            settings, warehouse.toolset, variants=variants or VARIANTS, prices=PRICES, model_for=lambda _name: chosen
        )

    return build


def make_draft(**overrides: Any) -> Draft:
    fields: dict[str, Any] = {
        "outcome": "draft",
        "draft_id": "d1",
        "question": QUESTION,
        "corrections": (),
        "interpretation": Interpretation(items=()),
        "sql": SQL,
        "steps": (),
        "usage": UsageSnapshot(requests=3, input_tokens=100, output_tokens=50, tool_calls=4),
    }
    return Draft(**{**fields, **overrides})


def root_span(exporter: InMemorySpanExporter, name: str) -> ReadableSpan:
    (span,) = [s for s in exporter.get_finished_spans() if s.name == name]
    return span


class TestFlow:
    async def test_plan_then_answer_gives_an_answered_outcome_with_stats(
        self, make_service: Callable[..., AgentService], spans: InMemorySpanExporter
    ) -> None:
        plan_script = UsageScript(
            tool("validate_sql", sql=SQL), plan_draft(SQL, item("metric", "net_revenue", "default"))
        )
        draft = await make_service(plan_script).plan(QUESTION)
        answer_script = UsageScript(tool("run_sql", sql=SQL), answer_out("The result is 1."))

        answered = await make_service(answer_script).answer(draft)  # type: ignore[arg-type]

        assert isinstance(draft, Draft)
        assert draft.stats is not None
        assert draft.stats.variant == "v0_baseline"
        assert [(u.model, u.input_tokens, u.output_tokens) for u in draft.stats.usage] == [("model-a", 20, 10)]
        assert (draft.stats.tool_calls, draft.stats.sql_attempts) == (1, 0)
        assert isinstance(answered, Answered)
        assert answered.stats is not None
        assert (answered.stats.tool_calls, answered.stats.sql_attempts) == (1, 1)
        assert answered.stats.trace_id == format(root_span(spans, "sherpa.answer").context.trace_id, "032x")
        assert draft.stats.trace_id == format(root_span(spans, "sherpa.plan").context.trace_id, "032x")
        assert draft.stats.trace_id != answered.stats.trace_id

    async def test_usage_is_summed_by_the_model_that_answered_and_an_unpriced_model_makes_the_cost_unknown(
        self, settings: AgentSettings, warehouse: FakeWarehouse
    ) -> None:
        service = AgentService(
            settings,
            warehouse.toolset,
            variants={"two_models": TwoModels()},
            prices=PRICES,
            model_for=lambda name: FunctionModel(UsageScript(clarify([])), model_name=name),
        )

        outcome = await service.plan(QUESTION, variant="two_models")

        assert outcome.stats is not None
        assert [(u.model, u.input_tokens, u.output_tokens) for u in outcome.stats.usage] == [
            ("model-a", 20, 10),
            ("model-b", 10, 5),
        ]
        assert outcome.stats.cost_eur is None  # model-b has no price: unknown is not zero

    async def test_cost_comes_from_the_prices_when_every_model_is_priced(
        self, make_service: Callable[..., AgentService]
    ) -> None:
        outcome = await make_service(UsageScript(clarify([]))).plan(QUESTION)

        assert isinstance(outcome, NeedsClarification)
        assert outcome.stats is not None
        assert [(u.model, u.input_tokens, u.output_tokens) for u in outcome.stats.usage] == [("model-a", 10, 5)]
        assert outcome.stats.cost_eur == Decimal("0.000105")  # 10 * 3 + 5 * 15 EUR per million tokens

    async def test_latency_comes_from_the_monotonic_clock(
        self, make_service: Callable[..., AgentService], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ticks = iter([10.0, 10.25])
        monkeypatch.setattr(service_module, "monotonic", lambda: next(ticks))

        outcome = await make_service(Script(clarify([]))).plan(QUESTION)

        assert outcome.stats is not None
        assert outcome.stats.latency_ms == 250

    async def test_root_span_carries_variant_outcome_draft_id_and_stats(
        self, make_service: Callable[..., AgentService], spans: InMemorySpanExporter
    ) -> None:
        script = UsageScript(tool("validate_sql", sql=SQL), plan_draft(SQL, item("metric", "net_revenue", "default")))

        draft = await make_service(script).plan(QUESTION)

        assert isinstance(draft, Draft)
        attributes = root_span(spans, "sherpa.plan").attributes or {}
        assert attributes["variant"] == "v0_baseline"
        assert attributes["outcome"] == "draft"
        assert attributes["draft_id"] == draft.draft_id
        assert (attributes["input_tokens"], attributes["output_tokens"]) == (20, 10)
        assert attributes["tool_calls"] == 1

    async def test_agent_spans_are_children_of_the_root_span(
        self, make_service: Callable[..., AgentService], spans: InMemorySpanExporter
    ) -> None:
        await make_service(Script(clarify([]))).plan(QUESTION)

        root = root_span(spans, "sherpa.plan")
        children = [s for s in spans.get_finished_spans() if s.parent is not None and s is not root]
        assert children
        assert {s.context.trace_id for s in children} == {root.context.trace_id}

    async def test_answer_span_carries_the_drafts_id(
        self, make_service: Callable[..., AgentService], spans: InMemorySpanExporter
    ) -> None:
        script = Script(tool("run_sql", sql=SQL), answer_out("The result is 1."))

        await make_service(script).answer(make_draft(draft_id="d-42"))

        assert (root_span(spans, "sherpa.answer").attributes or {})["draft_id"] == "d-42"

    async def test_spans_and_stats_never_carry_the_agent_password(
        self,
        make_service: Callable[..., AgentService],
        spans: InMemorySpanExporter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SHERPA_AGENT_PASSWORD", PASSWORD)
        service = make_service(Script(clarify([])))

        outcome = await service.plan(QUESTION)

        assert PASSWORD not in outcome.model_dump_json()
        assert all(PASSWORD not in str(s.attributes) for s in spans.get_finished_spans())


class TestSharedBudget:
    @pytest.fixture
    def run_calls(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        """What every `Agent.run` receives."""
        calls: list[dict[str, Any]] = []
        original = Agent.run

        async def spy(self: Agent[Any, Any], *args: Any, **kwargs: Any) -> Any:
            calls.append(
                {
                    "usage": kwargs["usage"].tool_calls,
                    "requests": kwargs["usage"].requests,
                    "limits": kwargs["usage_limits"],
                }
            )
            return await original(self, *args, **kwargs)

        monkeypatch.setattr(Agent, "run", spy)
        return calls

    async def test_a_plan_that_used_ten_tool_calls_leaves_the_answer_fifteen_more(
        self, make_service: Callable[..., AgentService], run_calls: list[dict[str, Any]]
    ) -> None:
        script = Script(tool("run_sql", sql=SQL), answer_out("The result is 1."))
        draft = make_draft(usage=UsageSnapshot(requests=3, input_tokens=100, output_tokens=50, tool_calls=10))

        await make_service(script).answer(draft)

        assert run_calls[0]["usage"] == 10
        assert run_calls[0]["limits"].tool_calls_limit == 25

    async def test_a_plan_that_used_39_requests_leaves_the_answer_one(
        self, make_service: Callable[..., AgentService], run_calls: list[dict[str, Any]]
    ) -> None:
        script = Script(tool("run_sql", sql=SQL), answer_out("The result is 1."))
        draft = make_draft(usage=UsageSnapshot(requests=39, input_tokens=100, output_tokens=50, tool_calls=4))

        outcome = await make_service(script).answer(draft)

        assert run_calls[0]["requests"] == 39
        assert run_calls[0]["limits"].request_limit == 40
        assert isinstance(outcome, Failed)
        assert outcome.reason == "budget exceeded"

    async def test_the_answer_continues_the_drafts_tokens(self, make_service: Callable[..., AgentService]) -> None:
        draft = make_draft(usage=UsageSnapshot(requests=3, input_tokens=399_990, output_tokens=5, tool_calls=4))

        outcome = await make_service(Script(tool("run_sql", sql=SQL), answer_out("1"))).answer(draft)

        assert isinstance(outcome, Failed)
        assert outcome.reason == "budget exceeded"

    async def test_a_plan_starts_from_zero(
        self, make_service: Callable[..., AgentService], run_calls: list[dict[str, Any]]
    ) -> None:
        await make_service(Script(clarify([]))).plan(QUESTION)

        assert (run_calls[0]["usage"], run_calls[0]["requests"]) == (0, 0)
        assert run_calls[0]["limits"].tool_calls_limit == 15


class TestFailureMapping:
    async def assert_failed(self, outcome: Any, reason: str) -> Failed:
        assert isinstance(outcome, Failed)
        assert outcome.reason == reason
        assert outcome.stats is not None
        assert outcome.stats.variant == "v0_baseline"
        assert len(outcome.stats.trace_id or "") == 32
        return outcome

    async def test_budget_exceeded_is_failed(self, make_service: Callable[..., AgentService]) -> None:
        script = Script(tool("list_tables"))  # forever: the 16th tool call is over the limit

        outcome = await make_service(script).plan(QUESTION)

        await self.assert_failed(outcome, "budget exceeded")

    async def test_fourth_sql_attempt_is_failed_with_last_sql(
        self, make_service: Callable[..., AgentService], warehouse: FakeWarehouse
    ) -> None:
        warehouse.set("validate_sql", validation("syntax error at or near 'selct'"))
        script = Script(*[tool("validate_sql", sql=f"selct {n}") for n in range(1, 6)])

        outcome = await make_service(script).plan(QUESTION)

        failed = await self.assert_failed(outcome, "sql attempts exhausted")
        assert failed.last_sql == "selct 4"
        assert failed.last_error == "syntax error at or near 'selct'"
        assert failed.stats is not None
        assert failed.stats.sql_attempts == 4

    async def test_plan_that_never_validates_is_failed(self, make_service: Callable[..., AgentService]) -> None:
        script = Script(plan_draft(SQL, item("metric", "net_revenue", "default")))  # never validated, forever

        outcome = await make_service(script).plan(QUESTION)

        await self.assert_failed(outcome, "plan could not be validated")

    async def test_answer_that_never_matches_a_result_is_failed(
        self, make_service: Callable[..., AgentService]
    ) -> None:
        script = Script(tool("run_sql", sql=SQL), answer_out("It is 999.", "ex-1"))  # 999 is not in the result

        outcome = await make_service(script).answer(make_draft())

        await self.assert_failed(outcome, "answer could not be verified against results")

    async def test_unexpected_model_behaviour_of_another_cause_propagates(
        self, make_service: Callable[..., AgentService]
    ) -> None:
        error = UnexpectedModelBehavior("Tool 'run_sql' exceeded max retries count of 4.")
        service = make_service(variants={"raising": Raising(error)})

        with pytest.raises(UnexpectedModelBehavior):
            await service.plan(QUESTION, variant="raising")

    async def test_throttling_after_retries_is_model_provider_unavailable(
        self, make_service: Callable[..., AgentService]
    ) -> None:
        error = ModelHTTPError(429, "model-a", "Too many requests")

        def throttled(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise error

        outcome = await make_service(model=FunctionModel(throttled)).plan(QUESTION)

        failed = await self.assert_failed(outcome, f"model provider unavailable: {str(error).splitlines()[0]}")
        assert "429" in failed.reason
        assert failed.last_sql is None

    @pytest.mark.parametrize(
        "error",
        [
            ClientError({"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}}, "Converse"),
            EndpointConnectionError(endpoint_url="https://bedrock-runtime.eu-central-1.amazonaws.com"),
        ],
        ids=["botocore-client-error", "botocore-endpoint-connection"],
    )
    async def test_botocore_errors_after_retries_are_model_provider_unavailable(
        self, make_service: Callable[..., AgentService], error: Exception
    ) -> None:
        service = make_service(variants={"raising": Raising(error)})

        outcome = await service.answer(make_draft(), variant="raising")

        failed = await self.assert_failed_variant(outcome, "raising")
        assert failed.reason == f"model provider unavailable: {str(error).splitlines()[0]}"

    async def assert_failed_variant(self, outcome: Any, variant: str) -> Failed:
        assert isinstance(outcome, Failed)
        assert outcome.stats is not None
        assert outcome.stats.variant == variant
        return outcome

    async def test_only_the_first_line_of_a_provider_error_is_reported(
        self, make_service: Callable[..., AgentService]
    ) -> None:
        error = ClientError({"Error": {"Code": "Throttling", "Message": "Rate exceeded\nrequest id: abc"}}, "Converse")
        service = make_service(variants={"raising": Raising(error)})

        outcome = await service.plan(QUESTION, variant="raising")

        assert isinstance(outcome, Failed)
        assert "\n" not in outcome.reason
        assert "request id" not in outcome.reason

    async def test_tool_server_gone_is_failed(
        self, make_service: Callable[..., AgentService], warehouse: FakeWarehouse
    ) -> None:
        warehouse.set("validate_sql", ConnectionError("stdio server exited"))
        script = Script(tool("validate_sql", sql=SQL))

        outcome = await make_service(script).plan(QUESTION)

        await self.assert_failed(outcome, "tool server unavailable")

    @pytest.mark.parametrize(
        "error",
        [
            MCPError(-32000, "connection closed"),
            ConnectionRefusedError("refused"),
            anyio.ClosedResourceError(),
            RuntimeError("Server session was closed unexpectedly"),
            ExceptionGroup("task group", [ConnectionError("pipe closed")]),
            ExceptionGroup("outer", [ExceptionGroup("inner", [MCPError(-1, "gone"), anyio.ClosedResourceError()])]),
        ],
        ids=["mcp-error", "connection-refused", "closed-resource", "session-closed", "group", "nested-group"],
    )
    async def test_every_way_the_tool_server_goes_away_is_tool_server_unavailable(
        self, make_service: Callable[..., AgentService], error: BaseException
    ) -> None:
        service = make_service(variants={"raising": Raising(error)})

        outcome = await service.plan(QUESTION, variant="raising")

        failed = await self.assert_failed_variant(outcome, "raising")
        assert failed.reason == "tool server unavailable"

    @pytest.mark.parametrize(
        "error",
        [
            ValueError("a bug"),
            RuntimeError("something else"),
            ExceptionGroup("mixed", [ConnectionError("pipe closed"), ValueError("a bug")]),
        ],
        ids=["value-error", "other-runtime-error", "group-with-a-bug"],
    )
    async def test_anything_else_propagates(self, make_service: Callable[..., AgentService], error: Exception) -> None:
        service = make_service(variants={"raising": Raising(error)})

        with pytest.raises(type(error)):
            await service.plan(QUESTION, variant="raising")

    async def test_a_failed_run_still_ends_its_root_span(
        self, make_service: Callable[..., AgentService], spans: InMemorySpanExporter
    ) -> None:
        service = make_service(variants={"raising": Raising(ConnectionError("gone"))})

        await service.plan(QUESTION, variant="raising")

        assert (root_span(spans, "sherpa.plan").attributes or {})["outcome"] == "failed"


class TestVariantChoice:
    async def test_unknown_variant_raises_agent_error_listing_the_names(
        self, make_service: Callable[..., AgentService]
    ) -> None:
        with pytest.raises(AgentError, match=r"v9.*v0_baseline"):
            await make_service(Script(clarify([]))).plan(QUESTION, variant="v9")

    async def test_the_default_variant_comes_from_the_settings(
        self, make_service: Callable[..., AgentService], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SHERPA_DEFAULT_VARIANT", "raising")
        service = AgentService(
            AgentSettings(_env_file=None),  # type: ignore[call-arg]
            FakeWarehouse().toolset,
            variants={"raising": Raising(ConnectionError("gone"))},
            prices=PRICES,
        )

        outcome = await service.plan(QUESTION)

        assert isinstance(outcome, Failed)
        assert outcome.stats is not None
        assert outcome.stats.variant == "raising"


class TestMcpToolset:
    def test_the_framework_retries_never_end_a_run_before_the_sql_attempt_limit(self, settings: AgentSettings) -> None:
        assert MCP_MAX_RETRIES == 4
        assert mcp_toolset(settings).max_retries == 4

    def test_stdio_starts_the_same_interpreter_in_the_repository_root(self, settings: AgentSettings) -> None:
        transport = mcp_toolset(settings).client.transport

        assert isinstance(transport, StdioTransport)
        assert transport.command == sys.executable
        assert transport.args == ["-c", "from sherpa_mcp.server import main; main()"]
        assert transport.cwd == str(REPO_ROOT)
        assert transport.env == server_env(settings)
        assert (REPO_ROOT / "pyproject.toml").is_file()

    def test_http_connects_to_the_configured_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_TRANSPORT", "http")
        monkeypatch.setenv("MCP_HTTP_URL", "http://127.0.0.1:9999/mcp")

        transport = mcp_toolset(AgentSettings(_env_file=None)).client.transport  # type: ignore[call-arg]

        assert isinstance(transport, StreamableHttpTransport)
        assert transport.url == "http://127.0.0.1:9999/mcp"


class TestServerEnv:
    def test_server_env_carries_exactly_the_client_variables(self, settings: AgentSettings) -> None:
        env = server_env(settings)

        assert env == {
            "MCP_TRANSPORT": "stdio",
            "SHERPA_DB_HOST": "127.0.0.1",
            "SHERPA_DB_PORT": "5432",
            "SHERPA_DB_NAME": "sherpa",
            "SHERPA_AGENT_USER": "sherpa_agent",
            "SHERPA_AGENT_PASSWORD": "pw",
            "SHERPA_DBT_TARGET_PATH": "data-platform/dbt/target-prod",
        }

    def test_server_env_never_carries_the_admin_login(
        self, monkeypatch: pytest.MonkeyPatch, settings: AgentSettings
    ) -> None:
        monkeypatch.setenv("SHERPA_ADMIN_USER", "postgres")
        monkeypatch.setenv("SHERPA_ADMIN_PASSWORD", "admin-pw")

        env = server_env(AgentSettings(_env_file=None))  # type: ignore[call-arg]

        assert not [key for key in env if key.startswith("SHERPA_ADMIN")]
        assert "admin-pw" not in env.values()

    def test_the_otlp_endpoint_is_passed_only_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")

        env = server_env(AgentSettings(_env_file=None))  # type: ignore[call-arg]

        assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://127.0.0.1:4318"

    def test_the_password_is_the_real_secret_not_its_masked_form(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SHERPA_AGENT_PASSWORD", PASSWORD)

        assert server_env(AgentSettings(_env_file=None))["SHERPA_AGENT_PASSWORD"] == PASSWORD  # type: ignore[call-arg]
