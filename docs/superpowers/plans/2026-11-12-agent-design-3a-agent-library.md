# agent-design-3a — Agent Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The text-to-SQL agent as a library and a CLI: `AgentService` with the two-step `plan`/`answer` contract, the `v0_baseline` variant, the recording toolset, the never-fabricate guards and `sherpa ask`. All of it is tested without a network. OpenTelemetry replaces the Logfire SDK in the MCP server, with Jaeger as the optional local trace backend.

**Architecture:**
- **New package:** `agent/core/src/sherpa_agent/`.
- **Pure core:** the outcome models (`outcomes.py`), the guards (`guards.py`) and the step grouping are pure.
- **I/O:** the variant runs PydanticAI agents over the MCP server's tools, reached through `MCPToolset` over stdio or HTTP. A `RecordingToolset` wraps that toolset, records every call, groups the calls into steps and enforces the SQL-attempt limit. `AgentService` dispatches to the variant, shares one usage budget per question, computes `RunStats` and opens the root spans.
- **Telemetry:** one small `sherpa_mcp.telemetry` module serves both processes.

**Tech Stack:** Python 3.12, PydanticAI 2.x (`pydantic-ai-slim[bedrock,anthropic,mcp]`, 2.68.0 at planning; it resolves with the pinned `mcp==2.3.0`), boto3, OpenTelemetry SDK with the OTLP/HTTP exporter and the psycopg instrumentor, Typer and Rich; pytest with PydanticAI's `TestModel`/`FunctionModel`.

**Spec:** `docs/superpowers/specs/2026-10-22-sql-sherpa-agent-design.md`:
- §2 (tooling and models), §3 (architecture, stdio specifics)
- §5.1 (the contract), §5.2 (never fabricate), §5.3 (limits and failures, defaults), §5.4 (variants), §5.5 (CLI)
- §6 (observability)
- §8 (the agent-design-3a row), §9 (testing), §10 (configuration)

Also the UI spec §3 and §9 (the containers' trace endpoint) and data platform spec §11.

**This plan's commit changes the specs** (owner decisions, 2026-11-04 and 2026-11-12):
- **Telemetry:** plain OpenTelemetry replaces the Logfire SDK, and Jaeger replaces Phoenix as the optional profile. The MCP SDK's own W3C `traceparent` carries the trace across processes. New configuration rows: `OTEL_EXPORTER_OTLP_ENDPOINT`, `SHERPA_CONTAINER_OTLP_ENDPOINT`, `SHERPA_TRACE_URL_TEMPLATE`.
- **PydanticAI:** the spec names PydanticAI 2.x and its `MCPToolset`.
- **Budget:** the `Draft` carries the plan's usage and `answer` continues it, so a question's budget covers both calls; each agent run's tool-call limit is its starting count plus 15.
- **Eval records:** the per-case records move to the gitignored root folder `.sql-sherpa/` (built in 3b).
- **The milestone is two parts.** **3a** (this plan) needs no network. **3b** (the next plan) verifies the output mode and effort on Bedrock, wires the evals and commits the first baseline. ui-5 needs 3b.

**Decisions taken here** (owner asked for recommendations):
- **The Windows event loop (for 3b, recorded in its notes):**
  - **The conflict:** asyncio subprocesses, and so the stdio MCP server, need Windows' default proactor loop. psycopg's async connections need a selector loop.
  - **The choice:** keep stdio for evals as the spec says, with the stdio server given the eval database's environment. Move `EvalDatabase` to sync psycopg in worker threads (`asyncio.to_thread`), so the runner runs on the default loop.
  - **The rejected option:** HTTP MCP for evals. The compose `mcp` service serves the prod warehouse, so it would need a second server just for `sherpa_eval`.
- **The spec edits:** made in this plan's commit, as listed above.
- **The answer model returns no interpretation** (spec §5.2 aligned in this commit): `Answered.interpretation` is the draft's, which the person approved. Asking the model to repeat it would cost tokens and could drift from what was approved; a variant that revises it (agent-design-7's critic) records that in `sql_revision`.

**`CLAUDE.md` is binding.**

**Base:** `main` at `5a8d9ca` (#24, agent-design-2).

**Model selection:** every task names its implementer and reviewer models. Dispatch exactly those. Fix rounds 4–5 go one tier up. The final whole-branch review is Task 9's panel.

## Global Constraints

- **No network in any test:**
  - PydanticAI `TestModel`/`FunctionModel`, a fake toolset (`FunctionToolset`) with the eight tool names, and `AsyncMock` for async collaborators (`CLAUDE.md`)
  - no Bedrock call, no Anthropic API call
  - the only live checks are 3b's
- **Models (spec §2):**
  - generation `bedrock:eu.anthropic.claude-sonnet-5-5`, cheap steps `bedrock:eu.anthropic.claude-haiku-5-5`
  - effort `medium` by default (`SHERPA_EFFORT_DEFAULT`, `SHERPA_EFFORT_FAST`)
  - never `temperature`/`top_p`, never forced tool choice
  - structured output without forcing `tool_choice`; 3a uses `NativeOutput`, kept behind one module constant so 3b can switch it
- **Limits (spec §5.2, §5.3), as named constants:**
  - `OUTPUT_RETRIES = 2`
  - `MAX_SQL_ATTEMPTS = 3` (the 4th ends the run)
  - `MCP_MAX_RETRIES = 4` (set explicitly on the toolset)
  - `TOOL_CALLS_PER_AGENT = 15`, `REQUEST_LIMIT = 40`, `TOTAL_TOKENS_LIMIT = 400_000`
  - botocore `retries={"mode": "adaptive", "max_attempts": 5}` and `connect_timeout=5` (UI spec §3: fail fast offline)
- **The MCP server (spec §3):**
  - **stdio** is started as the same interpreter (`sys.executable -c "from sherpa_mcp.server import main; main()"`) in the repository root, with an explicit `env`: `MCP_TRANSPORT=stdio`, the `SHERPA_DB_*`, `SHERPA_AGENT_*`, `SHERPA_MCP_*` and `SHERPA_DBT_TARGET_PATH` variables the client sets, and `OTEL_EXPORTER_OTLP_ENDPOINT` when set. Never the admin login. The server reads `.env` for the rest.
  - **HTTP** uses `MCP_HTTP_URL`.
  - The server never writes to stdout.
- **Telemetry:**
  - service names `sherpa-mcp` and `sherpa-agent`
  - a tracer provider is installed in every process (so every run has a trace id); the OTLP/HTTP exporter is added only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set
  - no console exporter
  - query text and stored values stay out of the server's spans as today (spec §6)
- **Package rules (`CLAUDE.md`):**
  - `sherpa_agent/settings.py` (`AgentSettings`, frozen, prefix `SHERPA_`, with `validation_alias` for `AWS_PROFILE`, `AWS_REGION`, `MCP_TRANSPORT`, `MCP_HTTP_URL`, `OTEL_EXPORTER_OTLP_ENDPOINT`) is the only environment reader
  - frozen Pydantic models across modules
  - `match` for dispatch, narrow exceptions re-raised with `from`
  - no blocking call in async code
  - prompts as module constants (they may pass 120 characters)
- **Packaging:**
  - a new dependency group `agent` (`pydantic-ai-slim[bedrock,anthropic,mcp]`, `boto3`, `rich`; `typer` is already there), added to `default-groups`
  - `agent/core/src/sherpa_agent` joins the wheel's packages, `testpaths` and mypy's `files`
  - the console script `sherpa = "sherpa_agent.cli:app"`
  - in the `mcp` group, `logfire[psycopg]` is replaced by `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http` and `opentelemetry-instrumentation-psycopg`, all already in `uv.lock`; `[tool.logfire]` goes
- **Every commit** leaves `make lint` and `make test` green. Commit as `Josep Martinez <57620224+josepmafe@users.noreply.github.com>`.

## Review Focus

1. **A number in the answer that no query produced** (the spec's worst outcome). Examples: "€1.2M" when the result says 1,180,000 (allowed, within 5%), "€1.3M" (not allowed), a growth rate computed in the prose, a year that appears only in the answer, digits inside a quoted email hash. Expected: only the forms §5.2 lists pass, and the run ends `Failed` after 2 retries. Tests: `TestNumbersCheck` (Task 4) and `test_fabricated_number_retries_then_fails` (Task 5).
2. **The plan step runs data.** Expected: `plan` cannot call `run_sql` at all (its toolset doesn't offer it), and its draft SQL is the SQL of its last successful `validate_sql`. Tests: `test_plan_never_calls_run_sql` and `test_draft_sql_is_the_last_validated_sql` (Task 5).
3. **The SQL-attempt and budget limits end the run in our words, not the framework's.** The 4th SQL attempt, the 16th tool call of one agent, the 41st model request, the token limit, and the framework's own MCP retry limit, which must not end the run first. Expected: `Failed` with the spec's reason, `last_sql` and `last_error`. Tests: Task 3 (attempts) and Task 6 (budget, framework retries).
4. **A correction that is ignored.** Expected: a `replace` correction's value appears in the re-planned SQL and in an interpretation item with `grounded_by = "correction"`; a `remove` correction's original value appears in neither. Test: `test_replace_correction_reaches_the_sql` (Task 5).
5. **The tool server or the model goes away mid-run.** The stdio server exits, Bedrock throttles until the retries run out, or a connection is refused. Expected: `Failed(reason="tool server unavailable")` / `Failed(reason="model provider unavailable: …")`, the CLI exits 1, and nothing hangs. Tests: Task 6 (service mapping) and Task 7 (exit codes).

---

## Environment (before Task 1)

The worktree has none of the gitignored files (`CLAUDE.md`, Environment notes):
1. Copy `.env` from the main checkout.
2. Run `UV_LINK_MODE=copy uv sync`.

No warehouse is needed: the agent's tests use fakes. The MCP server's existing `warehouse` tests need `make build-prod`.

## File Structure

```
agent/mcp-server/src/sherpa_mcp/
  telemetry.py          # new: configure(service_name), shared by the server and the agent
  server.py, tools.py   # Logfire calls become OpenTelemetry spans and stdlib logging
agent/core/
  src/sherpa_agent/
    __init__.py
    settings.py         # AgentSettings
    outcomes.py         # §5.1 models (pure)
    prices.py           # Prices (moved from sherpa_evals), cost_eur (pure but for the file read)
    models.py           # build_model(model_string, settings): Bedrock with our botocore config, or Anthropic
    recording.py        # RecordingToolset: records, steps, SQL attempts
    guards.py           # execution reference, numbers check, validated draft SQL (pure)
    variants/__init__.py      # Variant protocol, VariantContext, VARIANTS
    variants/v0_baseline.py   # prompts, output types, plan and answer
    service.py          # AgentService
    render.py, cli.py   # sherpa ask
  prices.yaml           # moved from agent/evals/
  tests/                # mirrors the package
agent/evals/src/sherpa_evals/  # imports Prices from sherpa_agent; PRICES_PATH follows the file
docker-compose.yml      # + jaeger profile; mcp gets SHERPA_CONTAINER_OTLP_ENDPOINT
.env.example, pyproject.toml
```

---

### Task 1: OpenTelemetry in the MCP server, and the Jaeger profile

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:**
- Create: `agent/mcp-server/src/sherpa_mcp/telemetry.py`, `agent/mcp-server/tests/test_telemetry.py`
- Modify: `server.py:14,195,198,269`, `tools.py:10,376,383`, `tests/test_server.py:23`, `tests/test_tools.py:22`, the MCP tests' `conftest.py`, `pyproject.toml`, `docker-compose.yml:51`, `.env.example:25-26`

**Interfaces — Produces:**
- `configure(service_name: str, otlp_endpoint: str | None) -> TracerProvider`:
  - sets a `TracerProvider(resource=Resource({"service.name": service_name}))` as the global provider
  - adds `BatchSpanProcessor(OTLPSpanExporter())` only when `otlp_endpoint` is set (the exporter reads the endpoint from the standard variable)
  - returns the provider
  - calling it twice in one process does nothing the second time
- `McpSettings` gains `otel_exporter_otlp_endpoint: str | None` (`validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT"`).

- [ ] **Step 1: Tests first.**
  - **`test_telemetry.py`:**
    - without an endpoint there is a provider and no span processor exports
    - with one, exactly one `BatchSpanProcessor` wraps an `OTLPSpanExporter`
    - the resource's `service.name` is the argument
    - a second call keeps the first provider
  - **The ported tests:** `test_server.py` and `test_tools.py` swap `CaptureLogfire` for a session-scoped `InMemorySpanExporter` fixture in `conftest.py` (one `TracerProvider` with a `SimpleSpanProcessor`, cleared before each test), keeping today's assertions:
    - the `sherpa.guard` span and its attributes
    - an unexpected tool exception is logged on stderr and recorded on the span (`span.record_exception`), with no stdout output
    - no query text on the server-side cursor's spans

  Run them. Expected: FAIL.
- [ ] **Step 2: Implement.**
  - **Calls:** `logfire.configure` becomes `telemetry.configure("sherpa-mcp", settings.otel_exporter_otlp_endpoint)`; `logfire.instrument_psycopg("psycopg")` becomes `PsycopgInstrumentor().instrument()`.
  - **Spans and logs:** `logfire.span` becomes `trace.get_tracer(__name__).start_as_current_span`; `logfire.info` becomes `logger.info` plus span attributes; `logfire.exception` becomes `logger.exception` plus `span.record_exception`.
  - **Dependencies:** swap them as in Global Constraints and run `uv lock`.
  - **Compose:**
    - a `jaeger` service under `profiles: [jaeger]`, a pinned `jaegertracing/jaeger:2.x` tag (the newest at implementation, written down), in-memory storage, ports `127.0.0.1:4318:4318` and `127.0.0.1:16686:16686`
    - the `mcp` service's `LOGFIRE_TOKEN` becomes `OTEL_EXPORTER_OTLP_ENDPOINT: ${SHERPA_CONTAINER_OTLP_ENDPOINT:-}`
  - **`.env.example`:** `OTEL_EXPORTER_OTLP_ENDPOINT` (`http://127.0.0.1:4318`), `SHERPA_CONTAINER_OTLP_ENDPOINT` (`http://jaeger:4318`) and `SHERPA_TRACE_URL_TEMPLATE` (`http://127.0.0.1:16686/trace/{trace_id}`), commented out with one line each.
- [ ] **Step 3: Verify.**
  - `make lint`, `make test`, and `uv run --env-file .env pytest -m warehouse agent/mcp-server`.
  - By hand: `docker compose --profile jaeger up -d jaeger` and `make mcp-dev` with `OTEL_EXPORTER_OTLP_ENDPOINT` set; one `describe_table` call from MCP Inspector shows a `sherpa-mcp` trace in Jaeger's UI. Record it in the task report.
  - Commit `feat: OpenTelemetry in the MCP server, Jaeger as the optional trace backend`.

---

### Task 2: The `sherpa_agent` package: settings, outcomes, prices and models

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:**
- Create: `agent/core/src/sherpa_agent/{__init__,settings,outcomes,prices,models}.py`, `agent/core/tests/{conftest,test_outcomes,test_prices,test_models}.py`
- Move: `agent/evals/prices.yaml` → `agent/core/prices.yaml`
- Modify: `agent/evals/src/sherpa_evals/report.py:35` (`Prices` moves out; `report.py` imports it), `agent/evals/src/sherpa_evals/settings.py:10` (`PRICES_PATH` points at the new file), `agent/evals/tests/test_report.py`, `pyproject.toml`

**Interfaces — Produces:**
- **`outcomes.py`** (spec §5.1, frozen models):
  - **Kinds:** `ItemKind = Literal["metric", "filter", "time_range", "grain", "table", "assumption"]`; `GroundedBy = Literal["search_values", "accepted_values", "literal", "default", "correction"]`.
  - **Interpretation:**
    - `Alternative(value: str, label: str, score: float | None)`
    - `InterpretationItem(kind, value: str, step_id: str, grounded_by, alternatives: tuple[Alternative, ...] = ())`
    - `Interpretation(items: tuple[InterpretationItem, ...])`
  - **Corrections:** `CorrectionTarget(kind: ItemKind, value: str)`; `Correction(action: Literal["replace", "remove", "free_text"], original: CorrectionTarget | None, value: str | None)`. A validator enforces `original is None` exactly for `free_text`, and `value is None` exactly for `remove`.
  - **Steps:** `ToolCall(tool: str, arguments: dict[str, JsonValue], summary: str)`; `Step(step_id: str, name: Literal["scope", "schema", "values", "sql", "answer"], tool_calls: tuple[ToolCall, ...])`.
  - **Usage and stats:**
    - `ModelUsage(model: str, input_tokens: int, output_tokens: int)`
    - `UsageSnapshot(requests: int, input_tokens: int, output_tokens: int, tool_calls: int)` (what `Draft` carries forward)
    - `RunStats(variant: str, usage: tuple[ModelUsage, ...], latency_ms: int, cost_eur: Decimal | None, tool_calls: int, sql_attempts: int, trace_id: str | None)`
    - every outcome below has `stats: RunStats | None = None`: a variant returns it unset and `AgentService` sets it, so an outcome the service returns always has stats (Task 6 tests that)
  - **Plan outcomes:**
    - `Draft(outcome: Literal["draft"], draft_id: str, question, corrections, interpretation, sql: str, steps, usage: UsageSnapshot, stats: RunStats)`
    - `NeedsClarification(outcome: Literal["needs_clarification"], question_to_user, candidate_interpretations: tuple[str, ...], partial: Interpretation | None, stats)`
    - `Unanswerable(outcome: Literal["unanswerable"], reason, closest_supported: str | None, stats)`
    - `Failed(outcome: Literal["failed"], reason, last_sql: str | None, last_error: str | None, stats)`
  - **Answer outcomes:**
    - `SqlRevision(previous_sql: str, reason: str)`
    - `Answered(outcome: Literal["answered"], answer_text, interpretation, execution_id, sql, columns: tuple[str, ...], rows: tuple[tuple[JsonValue, ...], ...], returned_rows: int, truncated: bool, caveats: tuple[str, ...], sql_revision: SqlRevision | None, stats)`
  - **Unions:** `PlanOutcome = Annotated[Draft | NeedsClarification | Unanswerable | Failed, Field(discriminator="outcome")]`; `AnswerOutcome = Annotated[Answered | Failed, …]`.
- **`prices.py`:** `Prices.load(path) -> Prices` and `.cost_eur(usage: Sequence[ModelUsage]) -> Decimal | None`, with the eval package's current behaviour (`None` when a model has no price). `PRICES_PATH` points at `agent/core/prices.yaml`; `sherpa_evals` imports both.
- **`models.py`:** `build_model(model_string: str, settings: AgentSettings) -> Model`, with a `match` on the prefix:
  - `bedrock:<id>` gives `BedrockConverseModel(<id>, provider=BedrockProvider(bedrock_client=boto3.Session(profile_name=settings.aws_profile).client("bedrock-runtime", region_name=settings.aws_region, config=BEDROCK_CONFIG)))`, where `BEDROCK_CONFIG = Config(retries={"mode": "adaptive", "max_attempts": 5}, connect_timeout=5)`
  - `anthropic:<id>` gives PydanticAI's Anthropic model, which reads `ANTHROPIC_API_KEY` itself
  - anything else raises `AgentError` with the two accepted prefixes
- **`model_settings(model_string: str, effort: Effort) -> ModelSettings`:** for Bedrock, `BedrockModelSettings(bedrock_additional_model_requests_fields={"output_config": {"effort": effort}}, max_tokens=MAX_TOKENS)`, `MAX_TOKENS = 16_000`; for Anthropic, the same effort in its own settings. The exact Bedrock field is verified live in 3b; this function is the only place that knows it.
- **`AgentSettings`:**
  - `model_default`, `model_fast`, `effort_default`, `effort_fast`, `default_variant`, `aws_profile`, `aws_region`, `mcp_transport`, `mcp_http_url`, `otel_exporter_otlp_endpoint`, `trace_url_template`
  - the server variables the stdio client passes: `db_host`, `db_port`, `db_name`, `agent_user`, `agent_password`, `dbt_target_path`
  - defaults from spec §10
- **`AgentError(Exception)`:** the package's error type.

- [ ] **Step 1: Tests first.**
  - **`test_outcomes.py`:**
    - the `Correction` rules
    - a `PlanOutcome` round-trips through JSON by its discriminator
    - `Draft` keeps its `UsageSnapshot`
  - **`test_prices.py`:** the moved tests from `sherpa_evals` pass against the new path; the eval suite still passes.
  - **`test_models.py`:**
    - `build_model("bedrock:eu.anthropic.claude-sonnet-5-5", settings)` is a `BedrockConverseModel` whose client carries the adaptive retry mode, 5 attempts and a 5 s connect timeout (read from the client's `meta.config`), built from a `botocore` stub session with no network
    - `anthropic:` builds the Anthropic model
    - `openai:x` raises `AgentError`
    - `model_settings` puts the effort where the docstring says
  - **The guard against forced settings:** no settings object sets `temperature`, `top_p` or `tool_choice`.

  Run them. Expected: FAIL.
- [ ] **Step 2: Implement** (move `prices.yaml` with `git mv`). `uv sync`, `make lint`, `make test`. Commit `feat: sherpa_agent package with the outcome models, prices and model factory`.

---

### Task 3: `RecordingToolset`: records, steps and the SQL-attempt limit

**Model:** implementer `sonnet` · reviewer **`opus`** (the record every guard trusts)

**Files:** `agent/core/src/sherpa_agent/recording.py`, `agent/core/tests/test_recording.py`, `agent/core/tests/fakes.py` (a `FunctionToolset` with the eight MCP tool names, canned results per test).

**Interfaces — Produces:**
- `Execution` (frozen): `execution_id`, `sql`, `columns`, `column_types`, `rows`, `returned_rows`, `truncated`.
- `SqlAttemptsExhausted(AgentError)`, carrying `last_sql` and `last_error`.
- `RecordingToolset(WrapperToolset)`: `__init__(wrapped, *, on_progress: Callable[[Step], Awaitable[None]] | None)`.
  - **`call_tool(name, tool_args, ctx, tool)`:**
    1. A call that would be the 4th SQL attempt raises `SqlAttemptsExhausted` before it runs. An attempt is a `run_sql` call, or a `validate_sql` whose result has `ok: false`.
    2. Otherwise it delegates.
    3. It records a `ToolCall` with a one-line `summary`: "ok, 12 rows" or "refused: <first error line>"; a tool error is recorded as failed and re-raised unchanged.
    4. It appends the call to the current step.
  - **Steps:** a step's name is its tool's purpose, `STEP_OF_TOOL`:
    - `get_context`, `list_tables`, `get_metric_definitions` and `get_examples` → `scope`
    - `describe_table` → `schema`
    - `search_values` → `values`
    - `validate_sql` and `run_sql` → `sql`

    A call whose purpose differs from the current step's completes that step: `on_progress(step)`. Step ids are `s1`, `s2`, … in order.
  - **Properties:** `.steps` (completed and open), `.executions` (successful `run_sql` results, in order), `.last_validated_sql` (the SQL of the last `validate_sql` with `ok: true`), `.sql_attempts`, `.last_error`.
  - `.finish()` completes the open step and returns `.steps`.
- `plan_tools(toolset) -> AbstractToolset`: `toolset.filtered(lambda ctx, tool_def: tool_def.name != "run_sql")`.

- [ ] **Step 1: Tests first** (driving `call_tool` directly over the fake toolset):
  - **Steps:** the calls `get_context, describe_table, describe_table, search_values, validate_sql` give steps `s1 scope`, `s2 schema` (2 calls), `s3 values`, `s4 sql`. `on_progress` is awaited three times during the calls and once by `finish()`.
  - **The attempt limit:**
    - three failing `validate_sql`s are allowed
    - a 4th attempt (any `run_sql`) raises `SqlAttemptsExhausted` with the last SQL and the last error, and the fake records no 4th call
    - a passing `validate_sql` does not count
  - **Executions:** a successful `run_sql` is kept with its columns and types; a `run_sql` tool error is not an execution, counts as an attempt and is re-raised.
  - **Validated SQL:** `last_validated_sql` follows the last passing validation only.
  - **The plan toolset:** `plan_tools` hides `run_sql`, and the other seven stay.

  Run them. Expected: FAIL.
- [ ] **Step 2: Implement.** `make test`, `make lint`. Commit `feat: recording toolset with steps and the SQL-attempt limit`.

---

### Task 4: The guards

**Model:** implementer `sonnet` · reviewer **`opus`** (spec §5.2 is the agent's main promise)

**Files:** `agent/core/src/sherpa_agent/guards.py`, `agent/core/tests/test_guards.py`.

**Interfaces — Produces** (pure; each returns `None` when the check passes, otherwise the retry message for the model):
- `check_execution_reference(execution_id: str, executions: Sequence[Execution]) -> str | None`: the id must be the **last** successful execution's. The messages are "no successful run_sql has execution_id X" and "X is not the last query you ran; describe the result of <last id>".
- `check_draft_sql(sql: str, last_validated_sql: str | None) -> str | None`: equal after stripping a trailing `;` and collapsing whitespace. The messages are "validate the SQL you return with validate_sql" and "return exactly the SQL that last passed validate_sql".
- `unverified_numbers(answer_text: str, execution: Execution, *, question: str, time_range: Sequence[str], as_of_date: date) -> list[str]`: the numbers in `answer_text` that are none of the §5.2 forms. Rules:
  - **Extraction:** remove every text cell's value from the answer first, so the digits of a quoted hash are no number. Then find numbers with optional sign, thousands separators (`,`), decimals, `%` and a magnitude suffix (`k`, `K`, `M`, `B`, and the words `thousand`, `million`, `billion`).
  - **Result values:** the cells of numeric columns only (`column_types` starting `int`, `numeric`, `float`, plus `float4`/`float8`), parsed as `Decimal`.
  - **Within rounding:** within ±0.5 in the answer's last shown digit; percentages are also tried ×100.
  - **Magnitude-rounded:** within a relative 5%: `MAGNITUDE_TOLERANCE = Decimal("0.05")`.
  - **Counts:** `returned_rows`, or the count of distinct values of any result column.
  - **Dates:** the year, month, day and quarter number of any date in the result cells, the question, `time_range` or `as_of_date`.
  - **The question:** any number it contains.
- The validator turns a non-empty list into "these numbers are not in the result: …; compute derived figures in SQL and run it again".

- [ ] **Step 1: Tests first** (`TestExecutionReference`, `TestDraftSql`, `TestNumbersCheck`, parametrized over a fixed execution: `month` date, `net_revenue` numeric `1180000.00` and `950000.50`, `orders` int8 `5230`, a text column holding a hash `"a3f9…"` with digits):
  - **Pass:**
    - `1,180,000`, `1.18 million` and `€1.2M` (within 5%)
    - `950,000.5`
    - `5,230`
    - `2` (`returned_rows`)
    - a year and a month from the dates
    - `2025` from `as_of_date`
    - a number from the question
    - `24.2%` when a cell is `0.242`
  - **Fail:**
    - `€1.3M`
    - `1,234`
    - `+12%` (growth computed in prose)
    - `2019`
    - digits taken from the hash cell, quoted as-is: no number is found, so it passes; taken out of context ("the hash ends in 3021"): `3021` fails
  - **Boundaries:**
    - `1.2M` against 1,142,857 (exactly 5%) passes; against 1,142,000 it fails
    - `950,000` against `950,000.50` passes (±0.5 in the last digit), and `949,999` fails
  - **Other checks:** a stale id fails, a non-last id fails, the last id passes; the draft SQL check ignores a trailing `;` and whitespace and refuses any other change.

  Run them. Expected: FAIL.
- [ ] **Step 2: Implement.** `make test`, `make lint`. Commit `feat: never-fabricate guards (execution reference, numbers check, validated draft SQL)`.

---

### Task 5: `v0_baseline`

**Model:** implementer `sonnet` · reviewer **`opus`** (the prompt and the output contracts every later technique builds on)

**Files:** `agent/core/src/sherpa_agent/variants/{__init__,v0_baseline}.py`, `agent/core/tests/variants/test_v0_baseline.py`.

**Interfaces:**
- **Consumes:** Tasks 2–4.
- **Produces:**
  - **`VariantContext`** (frozen, `arbitrary_types_allowed`):
    - `settings`
    - `toolset: AbstractToolset` (the MCP toolset, unwrapped)
    - `model_for: Callable[[str], Model]`
    - `usage: RunUsage`
    - `on_progress`
  - **`Variant`** (Protocol): `name`; `async plan(question: str, corrections: Sequence[Correction], ctx: VariantContext) -> PlanOutcome`; `async answer(draft: Draft, ctx: VariantContext) -> AnswerOutcome` (spec §5.4). A variant leaves `stats` unset; the service fills it in (Task 6).
  - `VARIANTS: dict[str, Variant] = {"v0_baseline": V0Baseline()}`.
  - `OUTPUT_MODE: Final = NativeOutput`: the one switch 3b may change.

**`v0_baseline`:**
- **`plan`:** one PydanticAI `Agent` on `settings.model_default`, with `output_type=OUTPUT_MODE([PlanDraftOut, ClarifyOut, UnanswerableOut])` and tools `plan_tools(RecordingToolset(ctx.toolset, on_progress=…))`.
  - The output models are those §5.1 lists, minus the pipeline's fields:
    - `PlanDraftOut(sql, items: list[ItemOut])`, where `ItemOut` has `kind`, `value`, `grounded_by`, `alternatives`; no `step_id`, no ids
    - `ClarifyOut(question_to_user, candidate_interpretations, partial_items)`
    - `UnanswerableOut(reason, closest_supported)`
  - An output validator applies `check_draft_sql` to `PlanDraftOut` and raises `ModelRetry` with its message.
  - **`step_id`** is set by the pipeline: the last completed step whose purpose matches the item's `grounded_by` (`search_values` → `values`; `accepted_values` → `schema`; `literal` → `sql`; `default` and `correction` → `scope`), else the last step.
  - **Limits:** `UsageLimits(request_limit=40, total_tokens_limit=400_000, tool_calls_limit=ctx.usage.tool_calls + 15)`, `output_retries=2`, and the run passes `usage=ctx.usage`.
- **`answer`:** a fresh `Agent`, with `output_type=OUTPUT_MODE(AnswerOut)` and tools `RecordingToolset(ctx.toolset)`; `AnswerOut` holds `answer_text`, `caveats` and `execution_id`.
  - **Validators,** in order: `check_execution_reference`, then `unverified_numbers` against the referenced execution. Its `time_range` is the values of the draft's `time_range` items, and `as_of_date` is read once from `get_context` (a direct toolset call before the run, not one of the model's).
  - **The result** gets `sql`, `columns`, `rows`, `returned_rows` and `truncated` from that `Execution`, never from the model. The interpretation is the draft's, and `sql_revision` is set when the executed SQL differs from the draft's.
  - **Usage:** `ctx.usage` starts from `draft.usage`.
- **The system prompt** (`PLAN_INSTRUCTIONS`, `ANSWER_INSTRUCTIONS`, module constants), in this order:
  1. the role and the read-only scope (from `get_context`)
  2. the corrections block, ahead of the question when there are any, one line per correction: "replace <kind> '<original>' with '<value>' (record it with grounded_by correction)", "do not apply <kind> '<original>'", or the free text as given
  3. the defaults of spec §5.3: "revenue" = `net_revenue`, "sales" = `gross_revenue`, "customers" = non-deleted customers with at least one non-cancelled order, relative periods against `as_of_date` with calendar periods
  4. when to clarify (no documented default and materially different queries) and when to refuse (outside `get_context`'s coverage, or a write)
  5. derived figures in SQL; the final answer uses only numbers from the result
  6. the agent-design-2 conventions:
     - do not round in SQL (round in the prose)
     - a period is named by its first date
     - durations as numbers
     - one row per group
     - `as_of_date` is "today"
     - match names case-insensitively with `lower(column) = 'literal'`
  7. `validate_sql` before returning a draft; describe the last query you ran

- [ ] **Step 1: Tests first** (`FunctionModel` scripts that call the fake tools and then return outputs; `TestModel` where the content doesn't matter):
  - **`test_plan_never_calls_run_sql`:** a `FunctionModel` that tries `run_sql` finds no such tool, and the fake records no `run_sql`.
  - **`test_draft_sql_is_the_last_validated_sql`:** a model returning SQL that differs from what it validated gets the retry message, then returns the validated SQL, which the draft carries.
  - **Step attribution:** an item grounded by `search_values` gets the `values` step's id.
  - **`test_replace_correction_reaches_the_sql`:**
    - the instructions the model receives contain the correction line ahead of the question
    - a model that follows it produces a draft whose SQL contains the new value and an item with `grounded_by = "correction"`
    - a `remove` correction's line says "do not apply"
  - **Plan outcomes:** `ClarifyOut` and `UnanswerableOut` map to `NeedsClarification` and `Unanswerable`.
  - **`test_fabricated_number_retries_then_fails`:** an answer quoting `€1.3M` three times ends in `UnexpectedModelBehavior`, which the service maps (Task 6). An answer quoting `€1.2M` passes.
  - **A stale execution id:** it retries, and a correct one on the second try passes.
  - **`Answered`:** it carries the execution's rows, not anything the model wrote, and `sql_revision` is set exactly when the executed SQL differs.
  - **Limits:** the `UsageLimits` passed to `Agent.run` equal `request_limit=40`, `total_tokens_limit=400_000`, `tool_calls_limit=<start>+15` (spy on the run's arguments: our use, not PydanticAI's enforcement).
  - **No forced settings:** neither agent sets `tool_choice`, `temperature` or `top_p`.

  Run them. Expected: FAIL.
- [ ] **Step 2: Implement.** `make test`, `make lint`. Commit `feat: v0_baseline variant (plan and answer, validators, corrections)`.

---

### Task 6: `AgentService`

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `agent/core/src/sherpa_agent/service.py`, `agent/core/tests/test_service.py`.

**Interfaces:**
- **Consumes:** Tasks 2–5, `sherpa_mcp.telemetry.configure`.
- **Produces:**
  - `AgentService(settings: AgentSettings, toolset: AbstractToolset, *, variants: Mapping[str, Variant] = VARIANTS, prices: Prices, model_for: Callable[[str], Model] | None = None)`.
  - `async plan(question, corrections=(), on_progress=None, *, variant: str | None = None) -> PlanOutcome` and `async answer(draft, on_progress=None, *, variant: str | None = None) -> AnswerOutcome`.
  - `mcp_toolset(settings) -> MCPToolset`, with `match settings.mcp_transport`:
    - `stdio` gives `StdioTransport(sys.executable, ["-c", "from sherpa_mcp.server import main; main()"], env=server_env(settings), cwd=REPO_ROOT)`
    - `http` gives `StreamableHttpTransport(settings.mcp_http_url)`
    - `max_retries=MCP_MAX_RETRIES`
  - `server_env(settings) -> dict[str, str]`: exactly the variables of Global Constraints, so `test_server_env_never_carries_the_admin_login` can assert it.

**What the service does:**
- **Variant:** it resolves the variant (default `settings.default_variant`); an unknown name raises `AgentError` listing the names.
- **Spans:** a root span `sherpa.plan` or `sherpa.answer` carries the `variant`, the outcome type and the `draft_id`. Its trace id (32 hex) becomes `RunStats.trace_id`, and `RunStats` is attached to the span as attributes. `Agent.instrument_all(InstrumentationSettings(tracer_provider=…))` runs once.
- **Usage:**
  - `plan` starts a fresh `RunUsage`; `answer` starts from the draft's `UsageSnapshot`
  - `RunStats.usage` sums `usage` by `model_name` over the run's `ModelResponse`s
  - `cost_eur` comes from `Prices`
  - `tool_calls` and `sql_attempts` come from the recorder
  - `latency_ms` comes from a monotonic clock
- **Failure mapping** (`match` on the exception):
  - `UsageLimitExceeded` → `Failed("budget exceeded")`
  - `SqlAttemptsExhausted` → `Failed("sql attempts exhausted", last_sql, last_error)`
  - an `UnexpectedModelBehavior` from the output retries → `Failed("answer could not be verified against results")` in `answer`, `Failed("plan could not be validated")` in `plan`
  - `ModelHTTPError`, botocore's `ClientError` and `EndpointConnectionError` after retries → `Failed("model provider unavailable: <first line>")`
  - the MCP connection or process ending (`MCPError`, `ConnectionError`, `anyio.ClosedResourceError`, or the `ExceptionGroup` wrapping them) → `Failed("tool server unavailable")`
  - anything else propagates

  Every `Failed` carries stats.

- [ ] **Step 1: Tests first** (`test_service.py`, with the fake toolset, `FunctionModel`/`TestModel`, and an in-memory span exporter):
  - **Flow:** plan then answer gives `Answered`, whose `stats.usage` lists the test model's tokens and whose `trace_id` equals the root span's.
  - **Shared budget:** the answer's `UsageLimits` start from the draft's usage, so a plan that used 10 tool calls leaves `answer` a limit of `<its start> + 15`, and a plan that used 39 requests leaves `answer` one.
  - **Mappings, each to the literal reason:**
    - `test_budget_exceeded_is_failed`
    - `test_fourth_sql_attempt_is_failed_with_last_sql`
    - `test_throttling_after_retries_is_model_provider_unavailable`: a `FunctionModel` raising `ModelHTTPError(429)`
    - `test_tool_server_gone_is_failed`: the fake toolset raising the connection error mid-run
  - **The framework's retries never end a run first:** the MCP toolset's `max_retries` is 4 (`mcp_toolset(settings)`'s attribute).
  - **Server environment:** `server_env` carries no `SHERPA_ADMIN_*` and passes `OTEL_EXPORTER_OTLP_ENDPOINT` only when set.
  - **Variants:** an unknown variant name raises `AgentError`.

  Run them. Expected: FAIL.
- [ ] **Step 2: Implement.** `make test`, `make lint`. Commit `feat: AgentService with shared budget, run stats, spans and failure mapping`.

---

### Task 7: The CLI

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `agent/core/src/sherpa_agent/{cli,render}.py`, `agent/core/tests/{test_cli,test_render}.py`, `pyproject.toml` (`[project.scripts]`).

**Interfaces:**
- **Consumes:** `AgentService`, `mcp_toolset`, `build_model`, `Prices`.
- **Produces:** `sherpa ask QUESTION [--variant NAME] [--confirm] [--json]` (spec §5.5).
  - **Exit codes:** `EXIT_ANSWERED = 0`, `EXIT_FAILED = 1`, `EXIT_CLARIFY = 3`, `EXIT_UNANSWERABLE = 4`, `EXIT_ABORTED = 5`.
  - **Telemetry:** the CLI calls `telemetry.configure("sherpa-agent", settings.otel_exporter_otlp_endpoint)` and flushes the provider before exiting.
  - **The toolset's lifetime:** the stdio server is one subprocess, opened with `async with` around the whole command.
  - `render.py` holds pure functions from outcomes to Rich renderables: the progress line per step, the answer, the result table (first 20 rows, with "… n more"), the caveats, the interpretation grouped by step, the SQL and the footer.
  - **The footer:** latency, tokens per model, cost (or "cost unknown"), and the trace link from `SHERPA_TRACE_URL_TEMPLATE`, or the trace id.

- [ ] **Step 1: Tests first** (Typer's `CliRunner`, with the service replaced by a fake that returns given outcomes):
  - **Exit codes:** each outcome gives its code and its rendered sections.
  - **`--json`:** prints exactly `outcome.model_dump_json()` of the final outcome, with no other output on stdout.
  - **`--confirm`:**
    - `run` answers the draft
    - `correct` with "use gross revenue" re-plans with one `free_text` correction, and the fake receives it
    - `abort` exits 5 and never calls `answer`
  - **The footer:** it uses the template when set, and prints the bare trace id when not.
  - **The result table:** it shows 20 rows and "… 5 more" for 25.

  Run them. Expected: FAIL.
- [ ] **Step 2: Implement.** `make test`, `make lint`. By hand, with the owner's OK and a Bedrock profile: one `sherpa ask "How many orders were placed in March 2025?"` against prod, to see the flow end to end. A failure here is information for 3b, not a blocker. Commit `feat: sherpa ask CLI`.

---

### Task 8: Documentation

**Model:** docs implementer `sonnet` · reviewer `haiku`

- [ ] **Step 1: `CLAUDE.md`.**
  - **Source of truth:**
    - the milestone list says agent-design-3 has two parts, 3a done, current plan 3b
    - sequencing: ui-5 after agent-design-3b
  - **Layout:** `agent/core/` (`sherpa_agent`) exists, and `prices.yaml` lives there.
  - **The pure/I/O list:** the agent's `outcomes`, `guards` and `render` are pure; `recording`, `service`, `models` and `cli` do the I/O.
  - **Commands:**
    - `sherpa ask` as a documented plain command
    - `docker compose --profile jaeger up -d jaeger` for traces
  - **The MCP server line:** logs go to stderr, and telemetry has no console exporter.
  - **Tests:**
    - an agent test runs on `FunctionModel`/`TestModel` and the fake toolset in `agent/core/tests/fakes.py`
    - a limit test asserts what we pass (`UsageLimits`, botocore config), not PydanticAI's enforcement
- [ ] **Step 2: `README.md`.**
  - the status line
  - the Stack row: PydanticAI and Claude on Bedrock are no longer *planned*, and "Pydantic Logfire" becomes "OpenTelemetry (Jaeger locally)"
  - a short "Ask from the command line" line linking the new guide
- [ ] **Step 3: Guides.**
  - **New `basic/asking-from-the-cli.md`:**
    - `sherpa ask` with each flag
    - reading the progress, the answer, the interpretation and the footer
    - `--confirm` and corrections
    - the exit codes
    - traces in Jaeger
    - symptoms: "model provider unavailable" (AWS profile, region); "tool server unavailable"; "budget exceeded"; "answer could not be verified against results"
  - **New `advanced/extending-the-agent.md`:**
    - adding a variant (the `Variant` protocol, `VARIANTS`, the tests on the fake toolset)
    - changing the prompt and which tests pin it
    - a new guard
    - the limits and where they are set
  - **`using-the-mcp-server.md` and `extending-the-mcp-server.md`:** their tracing sections become OpenTelemetry and Jaeger.
  - **README:** link the two new guides.
- [ ] **Step 4:** `make lint`. Commit `docs: the agent library, sherpa ask and traces in Jaeger`.

---

### Task 9: Panel review

**Model:** every reviewer and the devil's advocate on `opus`, dispatched by the controller (`CLAUDE.md`). Fixes go through the usual loop.

- [ ] **Step 1: Reviewers**, in parallel. Each gets `git diff main...HEAD`, this plan, its focus and the spec sections named:
  - **AI engineer** (agent spec §5, §6):
    - the prompts and the output contracts
    - the guards against §5.2, and what a model would do to pass them wrongly
    - step attribution and corrections
    - the budget's shape across plan and answer
    - whether 3b can verify and switch the output mode with only `OUTPUT_MODE` and `model_settings`
  - **Software engineer** (`CLAUDE.md`):
    - the package layout and pure/I/O split
    - async correctness and subprocess lifetime
    - the failure mapping's exception coverage
    - the telemetry setup in two processes
    - the tests' honesty against the fakes, and the dependency changes
  - **Security engineer:**
    - what the stdio subprocess receives (`server_env`)
    - what the traces carry now that PydanticAI records prompts, completions and tool results (marts values, hashes)
    - the Jaeger ports on `127.0.0.1`
    - the MCP server's spans keeping query text out as before
- [ ] **Step 2: Devil's advocate,** with the diff and every finding. It asks which fabricated answer would still pass, which failure becomes a hang rather than `Failed`, and which tests only prove the fakes.
- [ ] **Step 3: Triage and fix:** `make lint`, `make test`, the MCP `warehouse` tests, and CI green. The triage table goes in the PR description.

---

## Notes for agent-design-3b (owner notes, not tasks)

- **First, verify on Bedrock** (live, small, the owner's OK). Record each outcome in spec §2.
  - the output mode that works on `eu.anthropic.claude-sonnet-5-5` (`NativeOutput`, else `PromptedOutput`)
  - where effort goes in the Converse request
  - that adaptive thinking runs with tools
- **Eval wiring:**
  - the `v0_baseline` answerer in `ANSWERERS`, chaining `plan` and `answer` and mapping the outcomes and `RunStats` (tokens per model) to `Attempt`
  - span tags `eval_run_id` and `case_id`; the case id is not passed to the answerer
  - an answerer exception or a hung call (a per-case timeout) fails that case, not the run
  - `previous_run` skips partial runs and prints the baseline's age
  - the unmapped-column count per case
  - the admin settings optional, needed only by `--check-gold`
- **The Windows event loop** (decided above): `EvalDatabase` on sync psycopg in worker threads; drop the selector loop; the stdio server gets `SHERPA_DB_NAME=sherpa_eval` and `SHERPA_DBT_TARGET_PATH=data-platform/dbt/target-eval`.
- **The root folder `.sql-sherpa/`** (owner, 2026-11-12):
  - `cases.jsonl` moves to `.sql-sherpa/evals/runs/<run>/`; `report.md` and `meta.json` stay committed
  - `.gitignore`, the two eval guides and the tests follow
- **The first baseline:** the report committed, `FINDINGS.md` created, the budget re-set to 1.5× the baseline's p99, and one trace across the agent and the MCP server checked in Jaeger.
- **`FINDINGS.md`** notes that several gold cases are one substitution away from a few-shot example's SQL, so they partly measure example retrieval.
