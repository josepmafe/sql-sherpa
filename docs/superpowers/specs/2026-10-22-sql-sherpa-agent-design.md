# SQL Sherpa — Agent Spec: Text-to-SQL Agent

**Status:** Approved · **Date:** 2026-10-22 · **Depends on:** Data Platform Spec (`2026-10-22-sql-sherpa-data-platform-design.md`), interface in its §11

---

## 1. Intent

SQL Sherpa is the agentic layer that helps people get from a business question to correct SQL over the warehouse's consumption layer (the data platform marts). It is the second half of a learning project: the owner knows agentic systems but has never built a text-to-SQL agent, so this spec puts its detail where text-to-SQL is specific — grounding, schema linking, execution-based evaluation — and keeps generic agent plumbing plain.

The output must be production-like: not necessarily complex, but well-built, with typed contracts, explicit failure handling, observability from the first line, and evals before pipeline complexity.

### Personas
- **Business stakeholder** — complements dashboards with ad-hoc questions. Usually does not read SQL. Needs a correct answer and enough explanation to trust it. A confidently wrong number is the worst outcome.
- **Data analyst** — prototypes queries for dashboards and speeds up writing complex SQL. The SQL is the primary deliverable; the result is a sanity check.

### Goals
- A CLI that answers standalone questions over the marts with a typed outcome: answer, interpretation, SQL, result, caveats.
- An MCP server exposing the marts read-only, plus dbt metadata, with layered SQL safety.
- An execution-accuracy eval harness and a gold set of 50 questions, in place before any pipeline complexity.
- An incremental, measured build: a single-agent baseline, then one text-to-SQL technique at a time, each kept as the default only if the evals justify it.

### Non-goals (v1)
- Multi-turn conversations / follow-up questions (each question is standalone).
- A web UI.
- Exposing SQL Sherpa itself as an MCP tool for Claude Desktop/Code (later idea, §13).
- Writing to the database, or querying anything outside `marts`.
- Fine-tuning or training models.

### Anti-goals
- Answers containing numbers that did not come from an executed query.
- Pipeline stages added because the literature has them, not because the evals improved.
- An eval framework that becomes a project of its own. The runner stays small and boring.

---

## 2. Constraints & Tooling

| Concern | Decision |
|---|---|
| Language / packaging | Python 3.12, uv, same root `pyproject.toml` as the data platform spec; new dependency groups `mcp`, `agent`, `evals` |
| Agent framework | **PydanticAI** (latest stable), MCP client toolsets |
| MCP server | **FastMCP**: the standalone `fastmcp` package (latest stable major at agent-design-1) *or* the `mcp` SDK's bundled `FastMCP`. Pick one deliberately in agent-design-1 and pin it; they have diverged, so don't mix them |
| LLM provider | Claude on **Amazon Bedrock** via PydanticAI's Bedrock model. Configured entirely from `.env`: `AWS_PROFILE` (local default `ClaudeCode`), `AWS_REGION` (`eu-central-1`), model IDs per step. Anthropic API supported by switching the model string, with no code change. |
| Default models | SQL generation and the single-agent baseline: `eu.anthropic.claude-sonnet-5-5`. Cheap steps (grounding, linking, critic): `eu.anthropic.claude-haiku-5-5`. Gold-set drafting (offline): `eu.anthropic.claude-opus-5-5`. All overridable per step. |
| Model parameters | These models run adaptive thinking and **reject non-default sampling parameters** (`temperature`, `top_p`) and **forced tool choice**. The spec therefore never relies on either. Thinking **effort** is configurable per step (`SHERPA_EFFORT_DEFAULT`, `SHERPA_EFFORT_FAST`; default `medium`) and is one of the things evals measure, since it drives latency and cost. Structured output uses an output mode that doesn't force `tool_choice` (PydanticAI `NativeOutput` or `PromptedOutput`, or verified-safe `ToolOutput` with text output allowed). The mode is chosen and verified against Bedrock in agent-design-3. |
| SQL parsing | `sqlglot` (Postgres dialect) |
| Observability | **Logfire SDK** (OpenTelemetry). Logfire cloud by default; `LOGFIRE_SEND_TO_LOGFIRE=false` plus an OTLP endpoint sends to the optional Phoenix compose profile instead |
| CLI / rendering | Typer + Rich |
| Tests | pytest; PydanticAI `TestModel` / `FunctionModel` for every agent test. No live Bedrock calls in the unit suite |

### Repository layout (additions)

```
mcp_server/src/sherpa_mcp/
  server.py        # FastMCP app, tool registration, transport selection
  manifest.py      # loads target-prod manifest/catalog → Catalog model
  sql_guard.py     # sqlglot static checks
  masking.py       # PII masking
  db.py            # psycopg pool as sherpa_agent
  examples.yaml    # few-shot examples (disjoint from the eval gold set)
mcp_server/tests/
agent/src/sherpa_agent/
  config.py        # settings: models per step, limits, provider
  outcomes.py      # Outcome union, Interpretation, RunStats
  recording.py     # records every MCP tool call/result for a run
  guards.py        # output validators (execution reference, numbers check)
  variants/        # one module per variant + registry
  render.py        # persona-aware Rich rendering
  cli.py           # `sherpa ask`
agent/tests/
evals/
  cases.yaml       # gold set
  run.py           # runner (CLI)
  compare.py       # result-set comparison
  report.py        # markdown + jsonl report
  prices.yaml      # per-model token prices (maintained by hand)
  FINDINGS.md      # what each technique did to the scores
  runs/            # one directory per run (jsonl gitignored, report.md committed)
```

---

## 3. Architecture

```
sherpa ask "…" ──► Variant pipeline (PydanticAI agents) ──MCP──► sherpa_mcp ──► Postgres marts (sherpa_agent role)
                         │                                         │
                         └── RecordingToolset (all tool calls)     └── target-prod manifest/catalog (read-only)
                         └── Logfire spans ───────────────────────────────────────────────► Logfire / Phoenix
```

- **Transport:** the server supports `MCP_TRANSPORT=stdio|http`. **stdio is the default**: the CLI and the eval runner each start one server subprocess, shared by every agent in that process, with concurrent requests allowed. **HTTP** (Streamable HTTP) runs as an optional `mcp` service in docker-compose, bound to `127.0.0.1:8765`, with `dbt/target-prod` mounted read-only. It exists for multiple simultaneous client processes and to be production-like; stdio stays the fast development loop.
- **Metadata freshness:** the server loads the manifest/catalog at startup and reloads when `manifest.json`'s mtime changes (checked on each `list_tables` / `describe_table` call).
- **stdio specifics:**
  - The MCP SDK's stdio client passes only a minimal environment to the subprocess, so the client passes the server's variables **explicitly** (`env=` with `SHERPA_*`, `LOGFIRE_*`, `AWS_*`). The server also loads `.env` itself as a fallback.
  - The server must **never write to stdout**, which carries the JSON-RPC stream. Use `logfire.configure(console=False)` and send all logging to stderr.
- **Which marts are visible.** "Mart" means a dbt model node whose relation schema is `marts` (prod target). From data-platform-4 onwards, the node must also have `access: public`. Of versioned models, only the latest version is included, under its unversioned name.

---

## 4. MCP Server (`sherpa_mcp`)

### 4.1 Tools

All table/column arguments are validated against the manifest's mart models (case-insensitive, exact name) before any SQL is built. Unknown names return a tool error listing valid names.

| Tool | Input | Output |
|---|---|---|
| `get_context()` | — | `as_of_date` (`select max(order_date) from marts.fct_orders`, matching the data platform spec §6.5), the data's first order date, the domains, and the warehouse's scope (what it covers and what it doesn't: no inventory, no reviews, EUR only). Also injected into every variant's system prompt at the start of a run. |
| `list_tables()` | — | For every visible mart (§3): `name`, `description`, `domain`, `grain`, `primary_key` |
| `describe_table(table)` | mart name | `name`, `description`, `grain`, and per column: `name`, `data_type`, `description`, `is_pii`, `accepted_values` (from `accepted_values` tests, if any), `synonyms` (from `meta.synonyms`, if any); plus `sample_rows`: 3 rows, `ORDER BY` primary key, PII masked |
| `search_values(table, column, term, limit=10)` | text column only | Up to `limit` distinct stored values matching `column % term` (with `SET LOCAL pg_trgm.similarity_threshold = 0.2`, so the GIN trigram index is used where it exists), ranked by `similarity`, each with its similarity and row count. Columns without a trigram index are scanned sequentially, which is fine at this data size. PII columns are refused. |
| `get_metric_definitions()` | — | Every doc block named `metric_<name>` from the manifest's `docs` nodes (data platform spec §6.6): name and definition text. Ratio metrics are included as text; no column mapping. |
| `get_examples(question, k=3)` | NL question | The `k` most similar examples from `examples.yaml` (similarity = `pg_trgm`-free, pure-Python token Jaccard; good enough for ~15 examples) as `{question, sql, notes}` |
| `validate_sql(sql)` | SQL | `{ok, errors[], tables[]}`: runs `sql_guard`, then `EXPLAIN (FORMAT JSON)`; never executes |
| `run_sql(sql)` | SQL | `{execution_id, columns[], rows[], returned_rows, truncated, elapsed_ms}`: `sql_guard` → executes the SQL **exactly as written** (trailing `;` and whitespace stripped) on a **server-side cursor** and fetches at most 101 rows. No wrapping, so `ORDER BY` and trailing comments are unaffected. Returns ≤ 100 rows; `truncated = fetched 101`; `returned_rows` = rows returned (never a true total). PII columns are masked. `execution_id = sha256(executed_sql)[:16]`, where `executed_sql` is the stripped text. |

### 4.2 SQL safety (layers, all required)
The role's settings are defaults that a session could override. **`sql_guard` is the enforcement layer**, and the others are defence in depth.

1. **`sql_guard` (static, sqlglot, Postgres dialect).** A query is rejected unless all of these hold:
   - It parses to exactly **one statement**. Anything sqlglot falls back to `exp.Command` for is rejected.
   - The top level is `SELECT` or `WITH … SELECT`, with no `SELECT … INTO`, no data-modifying CTEs, and no `SET`/`COPY`/DDL/DML anywhere.
   - Every function call is on an **allow-list**: standard aggregates, window functions, math, string, date/time functions (`date_trunc`, `extract`, `to_char`, `age`, `now`/`current_date`, …), `coalesce`/`nullif`/`greatest`/`least`, casts, and `similarity`. Anything else is rejected, including `query_to_xml`, `current_setting`, `pg_*`, `dblink*`, `lo_*`.
   - **Every table reference is schema-qualified as `marts.<visible mart>`** (§3). Unqualified names are rejected with an error asking the model to qualify them, and the SQL is never rewritten. That also rejects `pg_catalog`, `information_schema`, other schemas, and non-latest model versions such as `dim_customers_v2`.
2. **Role:** the server connects as `sherpa_agent` (read-only, `SELECT` on `marts`). System catalogs are readable by every Postgres role, which is why the guard has to block them.
3. **Timeout:** the server runs `SET LOCAL statement_timeout = '5s'` in every transaction itself, rather than relying on the role default. A timeout returns a tool error suggesting aggregation or a narrower filter.
4. **Row cap:** 100 rows to the model (above).
5. **PII masking:** any output column whose name matches a column flagged `meta.pii` in any mart (`email`, `first_name`, `last_name`) is replaced by `'***'`. Name-based by design; documented as a known limitation (aliases evade it — acceptable for synthetic data, called out in docs).

### 4.3 Few-shot examples
`examples.yaml` holds ~15 question/SQL pairs covering common patterns (aggregation by period, top-N, ratio metrics, funnel flags). They are **disjoint from the eval gold set**: no example question may be a paraphrase of a gold question. agent-design-2 checks this by reviewing the closest example per gold case (Jaccard, listed in the agent-design-2 report).

---

## 5. Agent

### 5.1 Outcomes (the typed contract)

Every run returns exactly one `Outcome`:

```
Outcome = Answered | NeedsClarification | Unanswerable | Failed     (each carries RunStats)

Interpretation:
  metrics:      list[{name, definition_ref}]           # e.g. net_revenue → doc block
  filters:      list[{term, table, column, operator, value, grounded_by}]   # grounded_by: search_values | accepted_values | literal
  time_range:   {start, end, description} | None       # resolved against as_of_date
  grain:        str | None                              # e.g. "month × sales_channel"
  tables:       list[str]
  assumptions:  list[str]                               # human-readable, e.g. "'last quarter' = 2026-Q3 (calendar)"

Answered:            answer_text, interpretation, execution_id, sql, columns, rows, returned_rows, truncated, caveats: list[str]
NeedsClarification:  question_to_user, candidate_interpretations: list[str], partial: Interpretation | None
Unanswerable:        reason, closest_supported: str | None      # e.g. "inventory is not in the warehouse"
Failed:              reason, last_sql: str | None, last_error: str | None

RunStats: variant, models, latency_ms, input_tokens, output_tokens, cost_eur, tool_calls, sql_attempts, trace_id
```

### 5.2 Never fabricate (enforced in code, not in the prompt)
- The model's final output for `Answered` contains `answer_text`, `interpretation`, `caveats` and an **`execution_id`** — never rows or SQL. The pipeline attaches `sql`, `columns`, `rows`, `returned_rows`, `truncated` from the **RecordingToolset**'s record of that execution.
- Output validators (`guards.py`), raising `ModelRetry` on failure:
  - `execution_id` must refer to a **successful** `run_sql` call recorded in this run. For single-generator variants it must be the **last** successful one, since the answer describes the final query. For `v4_voting` it must be the execution of the **selected** candidate, a choice the pipeline makes in code rather than the model.
  - **Numbers check.** Derived figures (shares, differences, growth) must be computed **in SQL**, and the system prompt says so. Every number in `answer_text` must then be one of the following, or the check fails:
    - a value in the result rows, within rounding (±0.5 in the last shown digit; percentages also tried ×100)
    - a magnitude-rounded form of a result value ("1.2M", "about 3,400"), within a relative 5%
    - `returned_rows`, or a count of distinct values in a result column
    - a date or date part (year, month, day, quarter number) appearing in the result, the question, the interpretation's `time_range`, or `as_of_date`
    - a number present in the question
- After 2 validator retries (`output_retries = 2`) the run ends as `Failed(reason="answer could not be verified against results")`.

### 5.3 Limits and failure handling

| Situation | Behaviour |
|---|---|
| Bedrock throttling / 5xx | botocore adaptive retries, `max_attempts = 5`; then `Failed(reason="model provider unavailable: …")` |
| SQL error from `validate_sql` / `run_sql` | Returned to the model as a tool error; counts as one SQL attempt |
| SQL timeout | Tool error with a hint; counts as one SQL attempt |
| SQL attempts > 3 | `Failed` with `last_sql` and `last_error`. The RecordingToolset counts attempts (`validate_sql` failures + `run_sql` calls) and raises a run-ending error on the 4th. PydanticAI's MCP toolset `max_retries` is set **explicitly** to 4, so the framework's own retry limit (default 1) never ends a run before this rule does. |
| Budget exceeded | One shared `RunUsage` per question across every agent in the pipeline, checked with `UsageLimits`: ≤ 15 tool calls **per agent**, ≤ 40 model requests and ≤ 400k total tokens **per question**. Exceeding either ends the run as `Failed(reason="budget exceeded")`. The token limit is cumulative (each request re-sends the conversation), so it is generous on purpose. agent-design-3 re-sets it to 1.5× the baseline's p99 and records the value in `FINDINGS.md`. |
| Out-of-scope question (data not in marts, writes, non-analytics) | `Unanswerable` with `reason`. The scope comes from `get_context()`. |
| Ambiguous question | `NeedsClarification` only when **no documented default applies** and the readings would lead to different queries. Examples: "best customers" (by revenue? by order count?), or "conversion" with no funnel step. Terms that *have* a documented default (below) are **never** clarified. The default is used and recorded in `assumptions`. This is a judgement call from the prompt plus the default list; the agent does not run both readings. |
| Empty result | Allowed. `caveats` must say why it is plausible or flag it as suspicious |
| Truncated result | `caveats` must say so; the answer must not claim totals computed over truncated rows |
| MCP server crash | `Failed(reason="tool server unavailable")`; the CLI exits non-zero |

Default interpretations (documented in the system prompt and the metric doc blocks): "revenue" = `net_revenue`; "sales" = `gross_revenue`; "customers" = non-deleted customers with ≥ 1 non-cancelled order unless stated; relative periods are resolved against `as_of_date`, with calendar periods ("last quarter" = previous calendar quarter).

### 5.4 Variants

Each variant is a module in `agent/variants/` exposing `async def run(question: str, ctx: RunContext) -> Outcome` and registered by name. All variants stay runnable, so ablations can be rerun at any time. `sherpa ask --variant <name>` and `evals/run.py --variant <name>` select one; the configured default is used otherwise.

| Variant | Milestone | Technique |
|---|---|---|
| `v0_baseline` | agent-design-3 | One agent (Sonnet) with all MCP tools and the system prompt. Produces `Interpretation` as part of its final output. |
| `v1_grounding` | agent-design-4 | + a **grounding step** (Haiku) before generation: extracts entity/value mentions and resolves each via `accepted_values` or `search_values`; resolved filters are passed to the generator as facts. |
| `v2_linking` | agent-design-5 | + a **schema-linking step** (Haiku): selects tables and columns; the generator receives a pruned schema (descriptions included) instead of calling `describe_table` freely. |
| `v3_critic` | agent-design-6 | + a **critic step** (Haiku) after execution: checks for empty or all-null results, fan-out (row count vs. distinct grain keys), totals that contradict known mart totals, and interpretation/SQL mismatch; up to 2 refinement loops back to the generator. |
| `v4_voting` | agent-design-7 | Generates **3 candidates** in parallel, executes all, groups them by result equality (§7.2 rules), and picks the majority; ties go to the critic. Sampling parameters aren't available (§2), so the candidates differ in their prompts instead: (a) the default prompt, (b) a different set of few-shot examples, (c) a "decompose into CTEs first" instruction, with the schema listed in a shuffled order. |
| `v5_checkpoint` | agent-design-8 | Adds the **interpretation checkpoint**: interpretation first, then SQL. With `--confirm`, the CLI shows the interpretation and waits for approve / edit (a free-text correction appended to the context) / abort. In evals it runs without pausing. |
| `v6_metricflow` | agent-design-9 (after data-platform-5) | For metric-shaped questions, picks MetricFlow metrics, dimensions and filters instead of writing SQL; falls back to SQL otherwise. The `compile_metrics(metrics, group_by, where, order_by, limit)` MCP tool runs `mf query --explain` to get **compiled SQL** without executing it, using a dbt profile target `agent` with `sherpa_agent` credentials. The variant then runs that SQL through `run_sql`, so the guard, role, masking, `execution_id` and eval re-execution all work unchanged. |

Each variant builds on the **current default**, not necessarily on the previous row. If a technique doesn't earn its place (§8), later variants are built without it.

### 5.5 CLI

```
sherpa ask "net revenue by month in 2026 for Germany" [--variant v0_baseline] [--persona stakeholder|analyst] [--confirm] [--json]
```

- `--persona stakeholder` (default): answer, interpretation (assumptions + grounded values), result table, caveats; SQL collapsed at the end.
- `--persona analyst`: SQL first (syntax-highlighted), then result table, interpretation, caveats.
- `--json`: prints the `Outcome` as JSON (for scripting and evals).
- Exit codes: 0 `Answered`, 3 `NeedsClarification`, 4 `Unanswerable`, 1 `Failed`.
- Footer: latency, tokens, cost, trace link (Logfire URL, or trace id).

---

## 6. Observability

- **From agent-design-1:** `logfire.configure(service_name="sherpa-mcp")` in the server; tool calls, guard decisions and psycopg queries instrumented (`logfire.instrument_psycopg()`).
- **From agent-design-3:** `logfire.configure(service_name="sherpa-agent")`, `logfire.instrument_pydantic_ai()`, and MCP client instrumentation. **Cross-process trace propagation** (agent → MCP server): use Logfire's MCP instrumentation if it propagates context in the pinned version (verify in agent-design-3). Otherwise pass a `trace_id` in each tool call's `_meta` and attach it to server spans as an attribute, so the two traces can be joined.
- **Span conventions:** one root span per question (`sherpa.ask`, attributes: variant, persona, outcome type); child spans per pipeline step (`ground`, `link`, `generate`, `critic`, `vote`); `RunStats` attached to the root span.
- **Eval runs** tag every root span with `eval_run_id` and `case_id`, so a failing case can be opened as a trace directly from the report.

---

## 7. Evaluation

### 7.0 Eval dataset (frozen)

Evals never run against the development warehouse, because `make advance` changes it. A separate database **`sherpa_eval`** is built reproducibly:
- `make eval-db` runs the generator with `--seed 42 --start-date 2025-01-01 --days 365`.
- It then runs `dbt build` and `dbt docs generate` with target `eval` (`SHERPA_DB_NAME=sherpa_eval`, `--target-path target-eval`). Both the target and `make eval-db` are defined in the data platform spec §3.

The MCP server used by the eval runner points at `sherpa_eval` and `dbt/target-eval`. `as_of_date` is therefore fixed at 2025-12-31, and every run sees the same data. The dataset is rebuilt only when the data platform spec changes. A rebuild bumps `dataset_version` in `cases.yaml` and requires re-running the default variant before any comparison.

### 7.1 Gold set (`evals/cases.yaml`)

Written in **two tranches**, so agent-design-1–agent-design-3 can start right after data-platform-2:
- **Tranche 1 (agent-design-2, 40 cases):** only needs the data-platform-2 marts.
- **Tranche 2 (after data-platform-4, 10 cases):** marketing and web.

Each tranche is frozen once accepted. `cases.yaml` has a top-level `gold_version` (1 = tranche 1, 2 = both). Reports state the version, and **comparisons and promotions are only made between runs with the same `gold_version` and `dataset_version`**. When tranche 2 lands, the current default is re-run first to set the new baseline.

| Category | Tranche 1 | Tranche 2 | Tests |
|---|---|---|---|
| `values` | 8 | 2 | grounding: noisy names, product/brand/category values, country names → codes, campaign names |
| `metrics` | 8 | 2 | net vs gross, refunds by order vs refund date, AOV, margin; CAC/ROAS (T2). At least 3 cases use an unqualified default term ("revenue") and expect `answered`, which tests that the agent does **not** ask for clarification |
| `grain` | 7 | 1 | fan-out traps: payments, multiple discounts, order vs line grain; sessions vs orders (T2) |
| `time` | 6 | 1 | relative periods vs `as_of_date`, calendar vs rolling, Black Friday week; session dates (T2) |
| `complex` | 5 | 2 | CTEs, window functions, cohorts; funnel conversion and lead-to-customer conversion (T2) |
| `unanswerable` | 3 | 1 | data not in the warehouse (inventory, reviews), write requests → `expected_outcome: unanswerable` |
| `clarification` | 3 | 1 | genuinely ambiguous with no documented default ("who are our best customers?") → `expected_outcome: needs_clarification` |
| **Total** | **40** | **10** | |

Case schema:

```yaml
- id: metrics-003
  category: metrics
  question: "What was net revenue in March 2026 by sales channel?"
  tranche: 1
  expected_outcome: answered            # answered | unanswerable | needs_clarification
  gold_sql: |
    select sales_channel, sum(net_revenue) as net_revenue
    from marts.fct_orders
    where not is_cancelled and order_date >= '2026-03-01' and order_date < '2026-04-01'
    group by 1
  ordered: false                        # true if row order matters
  rationale: "Net revenue per §6.6, refunds attributed to order date; cancelled excluded."
  tables: [fct_orders]                  # for schema-recall measurement
  status: draft                         # draft → reviewed → accepted
  reviews: []                           # [{reviewer: codex|gemini|human, verdict, notes}]
```

**Authoring workflow (agent-design-2):**
1. Opus 5.5 drafts the tranche's cases (question, gold SQL, rationale, tables) from the data platform spec §6 and the `sherpa_eval` marts.
2. **Automatic checks** (`evals/run.py --check-gold`): every `answered` case executes, returns ≥ 1 row (unless the rationale says an empty result is expected), returns identical results on two runs, and passes `sql_guard` (so gold SQL follows the same rules as the agent). Cases whose rationale cites a raw-data reconciliation also run a small hand-written raw-schema query (as `sherpa_admin`, stored in the case as `reconcile_sql`) that must give the same totals.
3. **Cross-model review:** Codex and Gemini each review every case against the data platform spec §6.6 (semantics, not just syntax). The owner runs them through their own CLIs or chat with a fixed review prompt stored in `evals/REVIEW_PROMPT.md`, and records each verdict in `reviews`. If either reviewer is unavailable, a fresh Opus session with no access to the drafting conversation takes its place, and this is noted in `reviews`. The owner resolves disagreements. Only `accepted` cases count in reports.
4. The few-shot disjointness check (§4.3).

Gold dates are absolute (e.g. "March 2025"), except in the `time` category, where relative questions are expected. Those cases' gold SQL is written relative to `as_of_date` (`(select max(order_date) from marts.fct_orders)`). That is fixed at 2025-12-31 on the frozen eval dataset, and the relative form still documents intent.

### 7.2 Runner (`evals/run.py`)

```
uv run python -m evals.run --variant v0_baseline [--category metrics] [--case metrics-003] [--repeat 1] [--concurrency 4] [--check-gold]
```

- Runs each accepted case through the variant (in-process, one shared MCP stdio server), with `--concurrency` questions in flight.
- **Execution for comparison** uses the runner's own connection to `sherpa_eval` as `sherpa_agent`, with `SET statement_timeout = '30s'` and **no row cap**. It re-executes the agent's final SQL (the recorded `executed_sql`) and the gold SQL directly.
- **Comparison rules (`compare.py`):**
  - **Value normalization:** numbers rounded to 2 decimals; `NULL = NULL`; text compared exactly; dates and timestamps compared as ISO strings.
  - **Row count** must be equal.
  - **Column mapping.** Column names and order are ignored. Each gold column is matched to a distinct agent column whose normalized values form the same multiset; extra agent columns are allowed (e.g. a name next to an id). This only proposes candidate mappings. If several agent columns fit one gold column, every combination is tried, which is bounded because results are small.
  - **Whole-row check.** Under a candidate mapping, the agent's rows are projected onto the mapped columns and compared with the gold rows as **whole tuples**: as a multiset of tuples if `ordered: false`, as an ordered list if `ordered: true`. The case passes if any mapping matches. This stops values from being paired with the wrong keys.
- **Scoring:** `answered` cases pass on result match. `unanswerable` and `needs_clarification` cases pass if the outcome type matches.
- **Comparisons and promotion:** every candidate report is compared with a run of the current default **made in the same session**, so both see the same models and the same day's provider behaviour. Promotion decisions require `--repeat 2` on both.
- **Per-case record** (`runs/<timestamp>-<variant>/cases.jsonl`): case id, outcome type, pass/fail, the diff summary on failure, agent SQL, `RunStats`, schema recall (gold `tables` ⊆ tables the agent queried), trace id.
- **Report** (`runs/<…>/report.md`): execution accuracy (EX) overall and per category; outcome-type accuracy; p50/p95 latency; mean and total cost; mean tool calls and SQL attempts; schema recall; failing case ids with trace links; and the diff against the previous run of the current default variant.
- **Cost** = tokens × `prices.yaml` (EUR per million input/output tokens per model ID, maintained by hand).
- Budget for code: `run.py` + `compare.py` + `report.py` together stay **under ~400 lines**. No eval framework dependency.

### 7.3 Budgets (reported, not enforced)
p50 latency < 20 s, p95 < 60 s per question; mean cost per question is reported, with no fixed target. These are targets to compare variants against, not gates.

---

## 8. Milestones

Each milestone has one implementation plan, `docs/superpowers/plans/<date>-agent-design-<n>-<name>.md`. Each milestone ends with tests green, `make lint` clean, and its acceptance met. **Every plan's last task is a documentation check:** update `CLAUDE.md` and `README.md` wherever the milestone changed what they describe (commands, layout, status, conventions), so they always reflect the current state.

| Milestone | Builds | Acceptance |
|---|---|---|
| **agent-design-1 — MCP server** (needs data-platform-2) | `sherpa_mcp` with all §4.1 tools, `sql_guard`, masking, manifest loader with reload, both transports, compose `mcp` service, Logfire in the server (stderr only) | Tool tests run over **both stdio and HTTP** with identical results. `sql_guard` rejects a fixed list of ≥ 20 hostile inputs, including: multi-statement; `DELETE`; `SET`; `SELECT … INTO`; a data-modifying CTE; `pg_sleep`; `query_to_xml`; `current_setting`; `pg_catalog`/`information_schema` references; unqualified tables; `dim_customers_v2`; comment tricks; a trailing `--` comment. `run_sql` on `select * from marts.fct_order_items` returns 100 rows and `truncated = true`. PII is masked in `describe_table` samples and `run_sql` output. Nothing is ever written to stdout. Works in MCP Inspector |
| **agent-design-2 — Gold set + evals** | `make eval-db`; tranche 1 of `cases.yaml` (40, accepted); `REVIEW_PROMPT.md`; `run.py`, `compare.py`, `report.py`, `--check-gold`, `prices.yaml`; an **oracle variant** (`oracle`, test-only: returns the gold SQL) | `--check-gold` green. The oracle scores 100% on answered cases, which proves the comparison rules. `compare.py` unit tests cover column permutation, extra columns, ordered vs unordered, rounding and NULLs, and **a wrong key/value pairing that per-column matching would accept** |
| **agent-design-2b — Tranche 2** (after data-platform-4) | 10 more cases (marketing and web), `gold_version: 2` | Same checks; the current default is re-run as the new baseline |
| **agent-design-3 — Baseline** | `v0_baseline`, outcomes, RecordingToolset, guards, CLI with personas, agent Logfire, trace propagation, output mode verified on Bedrock, optional `phoenix` compose profile (`arizephoenix/phoenix`, pinned, OTLP on `127.0.0.1:4317`, UI on `:6006`) | First baseline report committed to `evals/runs/`. Guard tests (FunctionModel) prove that fabricated numbers and stale `execution_id`s trigger retries and then `Failed`, and that allowed number forms (rounded magnitudes, dates, counts) pass. Failure-handling tests cover throttling, the 4th SQL attempt and budget limits. The budget is re-set from the baseline p99 |
| **agent-design-4–agent-design-7 — Techniques** | `v1_grounding`, `v2_linking`, `v3_critic`, `v4_voting`, one per milestone | Each: a report against the current default, plus a `FINDINGS.md` entry (what changed, per-category effect, cost/latency effect, the decision) |
| **agent-design-8 — Checkpoint** | `v5_checkpoint`, `--confirm` flow | Manual check: approve, edit and abort paths work in the CLI; the eval report runs non-interactively |
| **agent-design-9 — MetricFlow** (needs data-platform-5) | `compile_metrics` tool, `v6_metricflow` | Report comparing the metric path vs the SQL path on the `metrics` and `time` categories |

**Promotion rule (agent-design-4–agent-design-9):** a technique becomes the default variant if, with `--repeat 2` against a same-session run of the current default (same `gold_version` and `dataset_version`), it improves accepted-case EX by **≥ 2 cases in both repeats**, without more than doubling p50 latency or mean cost. Otherwise it stays a non-default variant, and the finding is recorded. With 40–50 cases this rule is coarse on purpose: it only detects large effects, and `FINDINGS.md` says so.

---

## 9. Testing Strategy

| Layer | How |
|---|---|
| `sql_guard`, masking, manifest parsing, `compare.py` | Pure unit tests |
| MCP tools | Integration tests against the local `prod` marts (marker `integration`), run over both transports via a parametrized client fixture |
| Agent pipelines, guards, failure handling | PydanticAI `TestModel` / `FunctionModel` with a fake MCP toolset. No network |
| Live model behaviour | Only via the eval runner (marker `live`, never in CI) |
| CI | Unit tests + lint only. Integration tests need a built warehouse and run locally |

---

## 10. Configuration (`.env` additions)

| Variable | Default | Purpose |
|---|---|---|
| `AWS_PROFILE` | `ClaudeCode` | Bedrock credentials (local) |
| `AWS_REGION` | `eu-central-1` | Bedrock region |
| `SHERPA_MODEL_DEFAULT` | `bedrock:eu.anthropic.claude-sonnet-5-5` | Baseline / generator |
| `SHERPA_MODEL_FAST` | `bedrock:eu.anthropic.claude-haiku-5-5` | Grounding, linking, critic |
| `SHERPA_EFFORT_DEFAULT`, `SHERPA_EFFORT_FAST` | `medium` | Thinking effort per step |
| `SHERPA_DEFAULT_VARIANT` | `v0_baseline` | Updated as techniques are promoted |
| `SHERPA_EVAL_DB_NAME`, `SHERPA_EVAL_TARGET_PATH` | `sherpa_eval`, `dbt/target-eval` | Frozen eval dataset |
| `MCP_TRANSPORT` | `stdio` | `stdio` or `http` |
| `MCP_HTTP_URL` | `http://127.0.0.1:8765/mcp` | Client URL when `http` |
| `SHERPA_DBT_TARGET_PATH` | `dbt/target-prod` | Manifest/catalog location |
| `LOGFIRE_TOKEN`, `LOGFIRE_SEND_TO_LOGFIRE` | — / `true` | Tracing backend |

`.env.example` documents all of them. Nothing account-specific is committed.

---

## 11. Dependency on the Data Platform

The agent relies only on the data platform spec's §11 (Interface to the Agent). That covers:
- the `marts` schema and the `sherpa_agent` role
- the `target-prod` and `target-eval` artifacts, including `meta.{domain, grain, primary_key, pii, synonyms}`, `metric_<name>` doc blocks and `accepted_values` tests
- `pg_trgm` and `as_of_date`
- the `eval` and `agent` dbt targets

Everything the agent needs from the warehouse is defined there, not here.

---

## 12. Decisions Made by the Spec Author

- stdio is the default transport; HTTP is an optional compose service; both are tested.
- The model never returns rows or SQL in its final output; the pipeline attaches them from the recorded execution. Numbers in the answer are checked against the result.
- `NeedsClarification` only when readings differ materially; otherwise documented defaults plus recorded assumptions.
- Evals re-execute the agent's final SQL without a row cap and compare results with column-permutation matching; extra columns are allowed.
- Gold set: Opus drafts, Codex and Gemini review, the owner resolves; only `accepted` cases count.
- Promotion rule: ≥ 2 more cases correct, ≤ 2× latency and cost.
- PII masking is name-based (a documented limitation).
- Few-shot similarity is token Jaccard (no embeddings) — ~15 examples don't justify a vector store.
- Evals run on a frozen `sherpa_eval` database (fixed seed and dates), never on the dev warehouse.
- Gold set in two tranches (40 after data-platform-2, 10 after data-platform-4), versioned; comparisons only within a version, against a same-session default run, `--repeat 2` for promotions.
- `sql_guard` is the enforcement layer: function allow-list, mandatory `marts.` qualification (SQL is never rewritten), system catalogs blocked.
- `run_sql` fetches ≤ 101 rows from a server-side cursor instead of wrapping the query in `LIMIT`.
- Voting diversity comes from prompt variants, because sampling parameters are not available on these models.
- MetricFlow answers are compiled to SQL (`mf query --explain`) and executed through `run_sql`, so every answer path shares the same guard and verification.
- Budget limits are per question, shared across agents and deliberately generous, then recalibrated from the agent-design-3 baseline.

---

## 13. Later Ideas (explicitly out of scope)

- Expose SQL Sherpa itself as an MCP tool for Claude Desktop/Code (would reuse the HTTP transport).
- Multi-turn follow-ups ("now only Germany"), reusing the previous `Interpretation`.
- A web UI with the interpretation checkpoint as an editable form.
- Saving accepted analyst queries as new few-shot examples (with review).
