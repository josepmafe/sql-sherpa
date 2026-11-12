# Extending the agent

How to change `sherpa_agent`: add a variant, change the prompt, add a guard, and change a limit. Each section lists the files to touch, in order, and the tests that prove the change. Read [Asking from the command line](../basic/asking-from-the-cli.md) first. The design is in the [agent spec](../../superpowers/specs/2026-10-22-sql-sherpa-agent-design.md) §3 to §6, and every technique has to improve the eval scores ([Running the evals](../basic/running-the-evals.md)). Commands run from the repository root.

## Module map

Code is in `agent/core/src/sherpa_agent/`; tests in `agent/core/tests/`, mirroring it.

| File | Holds |
|---|---|
| `outcomes.py` | The frozen §5.1 models: `Draft`, `Answered`, `NeedsClarification`, `Unanswerable`, `Failed`, `RunStats`; pure |
| `guards.py` | The never-fabricate checks: execution reference, numbers check, validated draft SQL; pure |
| `recording.py` | `RecordingToolset`: wraps the MCP toolset, records the steps, counts SQL attempts (`MAX_SQL_ATTEMPTS`), hides `run_sql` from the plan |
| `variants/base.py` | `Variant` (the protocol), `VariantContext`, `OUTPUT_MODE` and the run limits |
| `variants/__init__.py` | `VARIANTS`, the registry the CLI and the service look names up in |
| `variants/v0_baseline.py` | The baseline: prompt constants, output types, plan and answer |
| `service.py` | `AgentService` (shared budget, stats, spans, failure mapping), `mcp_toolset` and `MCP_MAX_RETRIES` |
| `models.py` | `build_model`, `model_settings`, `BEDROCK_CONFIG` |
| `settings.py` | `AgentSettings`, the only place that reads configuration |
| `render.py`, `cli.py` | `sherpa ask`: Rich renderables (pure) and the Typer command |
| `prices.py`, `agent/core/prices.yaml` | EUR per million tokens per model ID |

Tests need no network and no database. `tests/fakes.py` has `FakeWarehouse`, a toolset with the eight real tool names and canned results (`warehouse.set(tool, *results)`); `tests/scripts.py` has `Script`, a `FunctionModel` function that plays scripted turns and records what it was sent; `tests/builders.py` has the `make_<outcome>` builders. Run them with `uv run pytest agent/core/tests -q`.

## 1. Add a variant

A variant is one technique added to the baseline (agent spec §5.4). Files, in order:

1. **`variants/<name>.py`:** a class with `name: str`, `async plan(question, corrections, ctx)` and `async answer(draft, ctx)`, matching `Variant`. Build every agent run from the context: `ctx.model_for(...)` for the model, `ctx.toolset` wrapped in a `RecordingToolset` that you append to `ctx.recorders`, and `ctx.usage` with `ctx.usage_limits()` passed to `Agent.run`. That gives the run the shared budget and the stats. Leave `stats` unset on the outcomes you return; `AgentService` fills it in.
2. **`variants/__init__.py`:** add the instance to `VARIANTS`.
3. **Tests, `tests/variants/test_<name>.py`**, copying the fixtures of `test_v0_baseline.py` (`warehouse`, `make_ctx`): the registration (`test_the_baseline_is_registered_under_its_name`), a plan that returns a `Draft`, an answer that returns `Answered`, and the limits (section 4).
4. **Score it:** `uv run --env-file .env python -m sherpa_evals.run --variant <name>` ([Running the evals](../basic/running-the-evals.md)).

Check it from the CLI with `uv run sherpa ask --variant <name> "..."`. A variant that uses the second model reads `model_fast` and `effort_fast` from `AgentSettings`.

## 2. Change the prompt

The baseline's prompt is the constants at the top of `variants/v0_baseline.py` (`ROLE`, `SCOPE_TEMPLATE`, `CORRECTIONS_HEADER`, `DEFAULTS`, `CLARIFY_OR_REFUSE`, `DERIVED_FIGURES`, `CONVENTIONS`, `PLAN_TASK`, `ANSWER_TASK`), joined into `PLAN_INSTRUCTIONS` and `ANSWER_INSTRUCTIONS`. The warehouse scope comes from one `get_context` call, not from the prompt text.

1. Edit the constant. Metric, grain and PII rules belong in the marts and the tool descriptions, not here.
2. Run `uv run pytest agent/core/tests/variants/test_v0_baseline.py`. These tests pin what the prompt must carry, not its words: `TestScopeInPrompt` (the scope is in both prompts, from one `get_context` call), `TestCorrections` (each correction action reaches the prompt as the spec says; no corrections, no block) and `test_the_prompt_hands_over_the_approved_sql_and_interpretation`. Add a test for any new block in the same style: run the variant on a `Script` and read what the model was sent.
3. A wording change that is meant to improve answers is proved by a before/after eval run, not by a unit test.

## 3. Add a guard

A guard turns a model mistake into a retry before it becomes an outcome (agent spec §5.2). Guards are pure functions in `guards.py`; a variant calls them from an output validator and raises `ModelRetry` with the message.

1. **`guards.py`:** the check and its message constant, with the spec section in a comment. Return what is wrong; never raise.
2. **`tests/test_guards.py`:** a class per guard, with the allowed forms, the refused forms and the edge cases (empty input, boundary values, hostile input) as parametrized cases. Assert the spec's literal values, not the module constants.
3. **Wire it** into the variant's validator, and add a variant test in `TestPlan` or `TestAnswer` that shows the retry and then the end of the run after `OUTPUT_RETRIES` retries (`test_fabricated_number_retries_then_fails` is the model).
4. **`service.py`:** only if the guard adds a new way for a run to end, add its reason to `_failure`, with a test in `test_service.py`.

## 4. Change a limit

| Limit | Value | Set in | Pinned by |
|---|---|---|---|
| Tool calls per agent run, model requests per question, total tokens per question | 15, 40, 400,000 | `TOOL_CALLS_PER_AGENT`, `REQUEST_LIMIT`, `TOTAL_TOKENS_LIMIT` in `variants/base.py` (`VariantContext.usage_limits`) | `TestLimits` in `test_v0_baseline.py`; `test_budget_exceeded_is_failed` in `test_service.py` |
| Validator retries | 2 | `OUTPUT_RETRIES` in `variants/base.py` | `test_plan_retries_a_bad_output_twice` |
| Tool retries of an agent | 4 | `TOOL_RETRIES` in `variants/v0_baseline.py` | `test_calls_to_an_unknown_tool_do_not_end_the_run_within_the_spec_retries` |
| MCP toolset retries | 4 | `MCP_MAX_RETRIES` in `service.py` | `test_the_framework_retries_never_end_a_run_before_the_sql_attempt_limit` |
| SQL attempts | 3 (the 4th ends the run) | `MAX_SQL_ATTEMPTS` in `recording.py` | the `test_a_fourth_*` tests in `test_recording.py` |
| MCP call and start timeouts | 30 s per call; start-up 30 s (the server's 15 s warehouse wait plus 15 s) | `MCP_READ_TIMEOUT_SECONDS`, `MCP_INIT_TIMEOUT_SECONDS` in `service.py` | `test_a_call_is_given_up_after_30_seconds_and_the_start_outlasts_the_servers_warehouse_wait` |
| Bedrock retries and timeouts | `max_attempts` 5 (botocore counts 5 retries: 6 attempts), connect 5 s, read 300 s | `BEDROCK_CONFIG` in `models.py` | `test_bedrock_prefix_builds_a_converse_model_with_the_retry_and_timeout_config` |

Both tool-retry values stay above `MAX_SQL_ATTEMPTS`, so the framework never ends a run before the SQL-attempt rule does. A limit test asserts what we pass (`UsageLimits`, the botocore config), not that PydanticAI enforces it. Change the spec (§5.3) in the same commit. A `Failed.reason` carries a stable code, status or class name and never the provider's or the database's text (`_provider_reason` in `service.py`; `test_a_bedrock_http_error_reports_a_code_or_a_status_and_never_the_providers_text`): reports are committed, and the full error goes to the log and the span.

Two switches change how the model is called: `OUTPUT_MODE` in `variants/base.py` (how structured output is requested; `NativeOutput`) and `model_settings` in `models.py` (the effort field and the token ceiling; never temperature, top_p or tool_choice, which `test_no_setting_forces_sampling_or_tool_choice` pins).

## Traces

`AgentService` opens a `sherpa.plan` or `sherpa.answer` span per call and sets run statistics as attributes (`_annotate`: counts, latency, tokens, cost; no SQL or rows). PydanticAI's own model and tool spans nest under it and record prompts, tool arguments and tool results, so the SQL, the result rows and the database's error text are in the trace; the MCP server's spans keep query text out. A new attribute of ours follows the `_annotate` rule: nothing from the database or the environment. The MCP server's spans join when the stdio server gets `OTEL_EXPORTER_OTLP_ENDPOINT` (`server_env`).

## When something goes wrong

| Symptom | Cause and fix |
|---|---|
| `Failed: tool server unavailable` in a test | The test used the real `mcp_toolset`; give the service the `FakeWarehouse` toolset |
| A test that passes alone fails in the suite | The OpenTelemetry provider is global (`configure` installs it once); read spans from the service's tracer, not from a new provider |
| A variant's budget runs out early in `answer` | `ctx.usage` must already include `draft.usage`; `AgentService.answer` does that, so call the variant through the service |
| A new guard's retry loops until `Failed` | The retry message must say what to change; compare `UNVERIFIED_NUMBERS` in `guards.py` |
| `mypy` or `ruff` fails on a new module | Follow the stepdown order and keep typing-only imports under `TYPE_CHECKING` ([CLAUDE.md](../../../CLAUDE.md)) |
