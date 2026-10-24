# agent-design-1 — MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `sherpa_mcp`: a read-only MCP server that exposes the prod marts and their dbt metadata through eight tools, enforces the SQL guard, masks PII, and runs over both stdio and Streamable HTTP, with a docker-compose `mcp` service and Logfire tracing.

**Architecture:** Pure modules do the logic, and are unit-testable without a database:
- `catalog` parses the dbt artifacts into a typed `MartCatalog`
- `sql_guard` checks SQL with sqlglot
- `masking` replaces PII values
- `examples` ranks the few-shot examples

`AgentDatabase` owns the read-only psycopg pool as `sherpa_agent`. `WarehouseTools` implements the tools on top of those, with Pydantic inputs and outputs. `server` registers them on FastMCP and picks the transport.

**Tech Stack:** Python 3.12; the official `mcp` SDK (its bundled `FastMCP`; the client is used in tests); `sqlglot`; `psycopg[binary,pool]` 3; Pydantic v2 + pydantic-settings; PyYAML; `logfire[psycopg]`; pytest with the `anyio` plugin (ships with `mcp`); Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-22-sql-sherpa-agent-design.md`: §3 (architecture, transports, visible marts), §4 (tools, SQL safety, few-shot examples), §6 (observability, from agent-design-1), §9, §10. The interface this relies on: `docs/superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md` §11. **`CLAUDE.md` is binding** (Working Style, Python Guidelines, layout, Git Bash for `make`).

## Global Constraints

- **Incremental commits (CLAUDE.md):** each task adds only the files, dependencies, config, Make targets, `.env` variables and `.gitignore` entries it uses. Every commit leaves `make lint` and `make test` green.
- **Layout:** `agent/mcp-server/src/sherpa_mcp/` plus `agent/mcp-server/tests/`, mirroring the package. The Dockerfile sits at `agent/mcp-server/Dockerfile`.
- **Packaging:**
  - One root `pyproject.toml`. Add the package to `[tool.hatch.build.targets.wheel] packages`, its tests to `testpaths`, its source to mypy `files`, and a dependency group `mcp` to `default-groups`.
  - FastMCP comes from the **official `mcp` SDK** (`mcp.server.fastmcp.FastMCP`), latest stable, pinned exactly. Don't use the standalone `fastmcp` package; the spec requires picking one and not mixing.
- **Read-only, enforced in four places** (spec §4.2):
  1. `SqlGuard`
  2. the `sherpa_agent` role
  3. the connection `options` (`-c default_transaction_read_only=on -c statement_timeout=5s`)
  4. `SET LOCAL statement_timeout = '5s'` in every transaction
- **Limits:** row cap **100** (fetch 101, so `truncated` is known), statement timeout **5 s**, `search_values` similarity threshold **0.2**, `get_examples` `k` default 3, max 10.
- **The server never writes to stdout** (stdio carries JSON-RPC): `logging` goes to stderr, and Logfire runs with `console=False`.
- **Every tool input and output is a frozen Pydantic model.** User-facing tool errors are `WarehouseToolError(message)`, and the server turns them into MCP tool errors (`isError`).
- **Identifiers never reach SQL as text.** Table and column names are validated against the catalog and then composed with `psycopg.sql.Identifier`. Values are always bound parameters.
- **Marker `warehouse`** for tests that need `make build-prod`. They are excluded by default, as today.

## Review Focus

1. **CTE names used as tables** (`with t as (...) select * from t`) must pass the guard, even though they aren't `marts.`-qualified. Rejecting them would block most real queries. Test: `TestSqlGuard::test_cte_references_are_allowed_unqualified` (Task 2).
2. **Quoted and mixed-case identifiers** (`"marts"."fct_orders"`, `MARTS.Fct_Orders`) must resolve to the same mart, and quoted names of non-marts must still be rejected. Test: `TestSqlGuard::test_identifier_case_and_quotes` (Task 2).
3. **A `search_values` term containing `%`, `_`, `'` or `\`** must be matched literally as a bound parameter, never break the query, and never widen the match. Test: `TestWarehouseTools::test_search_values_term_with_metacharacters` (Task 5).
4. **Missing or half-written dbt artifacts at startup** (no `target-prod/`, or `manifest.json` present but `catalog.json` missing): the server starts, and every tool returns a clear error naming `make build-prod` instead of crashing. Test: `TestMartCatalogLoader::test_missing_artifacts_raise_catalog_unavailable` (Task 1) and `TestWarehouseTools::test_tools_report_missing_artifacts` (Task 5).
5. **A query that hits the 5-second timeout** returns a tool error with the aggregation hint, and the pooled connection is usable for the next call. Test: `TestAgentDatabase::test_timeout_is_reported_and_connection_reusable` (Task 4).

---

## File Structure

```
agent/mcp-server/
├── Dockerfile
├── src/sherpa_mcp/
│   ├── __init__.py
│   ├── settings.py      # McpSettings
│   ├── catalog.py       # MartCatalog, Mart, MartColumn, MetricDefinition, MartCatalogLoader, CatalogUnavailable
│   ├── sql_guard.py     # SqlGuard, GuardResult, ALLOWED_FUNCTIONS
│   ├── masking.py       # PiiMasker, MASK
│   ├── examples.py      # Example, ExampleIndex
│   ├── examples.yaml    # ~15 question/SQL pairs
│   ├── database.py      # AgentDatabase, QueryResult, WarehouseToolError
│   ├── tools.py         # WarehouseTools + tool input/output models
│   └── server.py        # build_server(), main()
└── tests/
    ├── conftest.py      # fixture manifest/catalog, warehouse fixtures, mcp client fixture
    ├── fixtures/manifest.json  fixtures/catalog.json   # small hand-written dbt artifacts
    ├── test_settings.py  test_catalog.py  test_sql_guard.py  test_masking.py  test_examples.py
    ├── test_database.py  test_tools.py  test_server.py
```

---

### Task 1: Package, settings and the mart catalog

**Adds tooling:**
- dependency group `mcp` = `mcp` (pinned), `sqlglot`, `psycopg[binary,pool]`, `pydantic`, `pydantic-settings`, `pyyaml`, added to `default-groups`
- `pyproject.toml`: `packages` += `agent/mcp-server/src/sherpa_mcp`; `testpaths` += `agent/mcp-server/tests`; mypy `files` += `agent/mcp-server/src`
- `.env.example` += `SHERPA_DBT_TARGET_PATH=data-platform/dbt/target-prod`

**Files:** `settings.py`, `catalog.py`, `tests/fixtures/{manifest,catalog}.json`, `tests/test_settings.py`, `tests/test_catalog.py`.

**Interfaces — Produces:**
- `McpSettings(BaseSettings)`: frozen, env prefix `SHERPA_`, reads `.env`.
  - Fields: `db_host`, `db_port`, `db_name`, `agent_user`, `agent_password: SecretStr`, `dbt_target_path: Path = Path("data-platform/dbt/target-prod")`, `mcp_host: str = "127.0.0.1"`, `mcp_port: int = 8765`, and `transport: Literal["stdio", "http"] = "stdio"` read from **`MCP_TRANSPORT`** (`validation_alias`, no prefix; spec §10).
  - `agent_conninfo() -> str`: libpq conninfo with `options="-c default_transaction_read_only=on -c statement_timeout=5s"` and `connect_timeout=10`.
- `MartColumn` (frozen): `name`, `data_type`, `description`, `is_pii: bool`, `is_primary_key: bool`, `accepted_values: tuple[str, ...] | None`, `synonyms: tuple[str, ...]`, `metric_refs: tuple[str, ...]` (the metric doc blocks its description uses, from the manifest column's `doc_blocks`, e.g. `net_revenue`). The UI's Inspector uses it too.
- `Mart` (frozen): `name`, `description`, `domain`, `grain`, `primary_key: tuple[str, ...]`, `columns: tuple[MartColumn, ...]`, `synonyms: tuple[str, ...]`, `tests: tuple[str, ...]` (names of the data tests on the model or its columns, from test nodes whose `depends_on` includes it; for the UI's Inspector); `column(name) -> MartColumn | None` (case-insensitive).
- `MetricDefinition` (frozen): `name` (without the `metric_` prefix), `definition`.
- `MartCatalog` (frozen): `marts: tuple[Mart, ...]`, `metrics: tuple[MetricDefinition, ...]`, `generated_at: datetime`.
  - `mart(name) -> Mart | None` (case-insensitive)
  - `visible_names() -> frozenset[str]`
  - `pii_columns() -> frozenset[str]` (lower-case names)
  - `domains() -> tuple[str, ...]`
- `MartCatalogLoader(target_path: Path)`: `load() -> MartCatalog`. It caches and reloads when `manifest.json`'s or `catalog.json`'s mtime changes. It raises `CatalogUnavailable(message)` naming `make build-prod` when either file is missing or unparsable.
- **Visible marts** (spec §3): model nodes whose relation schema is `marts`.
  - If any model in the manifest declares `access: public`, only `public` ones count; before data-platform-4 none do.
  - Column types come from `catalog.json`.
  - `accepted_values` come from the manifest's `accepted_values` test nodes attached to the column.
  - `meta` gives `domain`, `grain`, `primary_key`, `pii`, `synonyms`.
  - Metrics are the manifest `docs` nodes named `metric_*`.

- [ ] **Step 1:** Write `tests/fixtures/manifest.json` and `catalog.json`, the smallest real-shaped artifacts:
  - two marts (`fct_orders` with `customer_id`, `order_date`, `net_revenue`, `current_status` with an `accepted_values` test; `dim_customers` with `email` `meta.pii: true` and `customer_segment` with `meta.synonyms`)
  - one staging model
  - one `metric_net_revenue` doc block

  Copy key shapes from a real `data-platform/dbt/target-prod/manifest.json`, trimmed.
- [ ] **Step 2: Write failing tests:**
  - `TestMcpSettings`:
    - `test_agent_conninfo_forces_read_only_and_timeout`: the conninfo's `options` contains both `-c` settings
    - `test_transport_reads_unprefixed_env`: `MCP_TRANSPORT=http` gives `transport == "http"`
  - `TestMartCatalogLoader`:
    - `test_loads_only_marts_schema_models`: the staging model is absent
    - `test_column_types_meta_accepted_values_metric_refs_and_tests`
    - `test_metrics_from_doc_blocks`: the name is `net_revenue`
    - `test_public_access_rule_applies_once_any_model_is_public`: the fixture is edited in `tmp_path` so one mart is `public` and the other `protected`; only the public one is visible
    - `test_reloads_on_mtime_change`
    - `test_missing_artifacts_raise_catalog_unavailable`: both cases, missing folder and manifest without catalog
  - `TestMartCatalog`:
    - `test_lookup_is_case_insensitive`
    - `test_pii_columns`
- [ ] **Step 3:** Run `uv run pytest agent/mcp-server/tests -v`. Expected: FAIL (no package).
- [ ] **Step 4:** Implement `settings.py` and `catalog.py`. Re-run, plus `make lint`. Expected: PASS, clean.
- [ ] **Step 5:** Commit `feat: sherpa_mcp settings and the mart catalog from dbt artifacts`.

---

### Task 2: SQL guard

**Files:** `sql_guard.py`, `tests/test_sql_guard.py`.

**Interfaces — Produces:**
- `GuardResult` (frozen): `ok: bool`, `errors: tuple[str, ...]`, `tables: tuple[str, ...]` (the qualified `marts.<name>` referenced).
- `SqlGuard(visible_marts: frozenset[str])`: `check(sql: str) -> GuardResult`. It never raises on bad SQL; that is a result.
- `ALLOWED_FUNCTIONS: Final[frozenset[str]]`. A function's name is `func.sql_name().lower()`, or `func.name.lower()` for `exp.Anonymous`. The set holds exactly:
  - **aggregates:** `count sum avg min max stddev stddev_samp stddev_pop variance var_samp var_pop percentile_cont percentile_disc mode string_agg array_agg bool_and bool_or`
  - **window:** `row_number rank dense_rank ntile lag lead first_value last_value nth_value percent_rank cume_dist`
  - **math:** `abs round ceil ceiling floor trunc power pow sqrt ln log exp mod sign greatest least`
  - **string:** `lower upper length char_length substring substr trim ltrim rtrim replace concat concat_ws left right split_part strpos position lpad rpad initcap`
  - **date:** `date_trunc date_part extract age now current_date current_timestamp to_char to_date make_date date_bin`
  - **conditional and casts:** `coalesce nullif case if cast try_cast`
  - **trigram:** `similarity`

  If sqlglot names a construct differently for Postgres (e.g. `extract`, `case`, `cast`), map the name sqlglot actually emits, in a comment next to the set. The unit test (Step 1) pins the behaviour, not sqlglot's internal names.

Rules (spec §4.2, all required):
1. `sqlglot.parse(sql, read="postgres")` must yield **exactly one** statement. Parse errors and `exp.Command` are rejected.
2. **The top level must be a query:** `exp.Select`, or a set operation (`Union`/`Intersect`/`Except`) of selects, optionally with a `WITH`.
   - Rejected anywhere in the tree: `exp.Insert`, `Update`, `Delete`, `Merge`, `Create`, `Drop`, `Alter`, `Set`, `Copy`, `Command`, a select with `into`, and locking clauses (`FOR UPDATE`/`SHARE`).
3. **Every function call is in `ALLOWED_FUNCTIONS`.**
4. **Every `exp.Table` is either a CTE name in scope or `marts.<visible mart>`.** Comparison is case-insensitive; quoted names are compared by their value. Unqualified non-CTE tables are rejected with "qualify it as marts.<name>". Other schemas (incl. `pg_catalog`, `information_schema`) and unknown marts are rejected.
5. **Errors name the offending part**, e.g. `function pg_sleep is not allowed`, `table staging.stg_shop__orders is outside marts`. A query with several problems lists them all.

- [ ] **Step 1: Write failing tests** (`class TestSqlGuard`, visible marts `{fct_orders, dim_customers, fct_order_items}`):
  - **Accepted:** `test_accepts_plain_select`, `test_accepts_window_and_aggregates`, `test_accepts_union_of_selects`, `test_cte_references_are_allowed_unqualified`, `test_identifier_case_and_quotes` (accepts `"marts"."fct_orders"` and `MARTS.Fct_Orders`; rejects `"staging"."fct_orders"`), `test_trailing_semicolon_and_comment_ok`, `test_reports_tables_referenced`.
  - **`test_rejects_hostile_inputs`**, parametrized over ≥ 22 inputs with the expected error fragment each:
    - multiple statements (`select 1; drop table x`) and a statement after a comment
    - `delete`, `update`, `insert`
    - `set statement_timeout = 0`, `reset all`, `alter role sherpa_agent set default_transaction_read_only = off`
    - `select … into x`, a data-modifying CTE (`with d as (delete … returning *) select …`), `copy … to stdout`
    - `pg_sleep(10)`, `query_to_xml('select 1', true, true, '')`, `current_setting('x')`, `set_config('a','b',false)`, `dblink(...)`, `lo_import(...)`
    - `pg_catalog.pg_roles`, `information_schema.tables`, an unqualified `fct_orders`, `staging.stg_shop__orders`, `marts.dim_customers_v2` (an unknown mart)
    - `select … for update`
  - `test_lists_every_violation`: `pg_sleep` plus a staging table gives two errors.
  - `test_parse_error_is_a_rejection`: no exception.
- [ ] **Step 2:** Run. Expected: FAIL. Implement. Re-run. Expected: PASS.
- [ ] **Step 3:** `make mutate MODULE=agent/mcp-server/src/sherpa_mcp/sql_guard.py`. Expected: no unjustified surviving mutants. mutmut doesn't run on native Windows, so use WSL or Linux, as `CLAUDE.md` says.
- [ ] **Step 4:** Commit `feat: SQL guard (single read-only query over marts, function allow-list)`.

---

### Task 3: PII masking and few-shot examples

**Files:** `masking.py`, `examples.py`, `examples.yaml`, `tests/test_masking.py`, `tests/test_examples.py`.

**Interfaces — Produces:**
- `MASK: Final = "***"`. `PiiMasker(pii_columns: frozenset[str])`: `mask(columns: Sequence[str], rows: Sequence[Sequence[object]]) -> list[list[object]]`. It replaces non-null values of columns whose name (case-insensitive) is a PII column; null stays null. Name-based by design (spec §4.2).
- `Example` (frozen): `question`, `sql`, `notes`.
- `ExampleIndex(examples: Sequence[Example])`: `top(question: str, k: int) -> list[Example]`. Ranking is by token-set Jaccard (tokens = lower-cased `[a-z0-9]+`, stop words removed, with a fixed tuple of ~30 English stop words in the module); ties break by file order. `k` must be 1..10.
- `ExampleIndex.from_yaml(path) -> ExampleIndex`.
- **`examples.yaml`:** 15 pairs over the data-platform-2 marts:
  - monthly net revenue
  - top-N products by net revenue
  - AOV by sales channel
  - refund rate by category
  - customers by segment
  - VIP share of revenue
  - discount usage by promotion (`fct_order_discounts`)
  - returns by reason
  - days to deliver
  - first-order customers per month (`is_first_order`)
  - gross vs net revenue
  - Black Friday week vs the week before (`dim_date`)
  - margin by brand
  - orders by payment method
  - cancelled-order share

  Every SQL qualifies `marts.`, follows the metric definitions (non-cancelled orders, refunds by order date), and none paraphrases a gold-set question (agent-design-2 checks disjointness).

- [ ] **Step 1: Write failing tests:**
  - `TestPiiMasker`:
    - `test_masks_pii_by_name_case_insensitive`
    - `test_keeps_nulls_and_other_columns`
    - `test_empty_result`
  - `TestExampleIndex`:
    - `test_top_ranks_by_jaccard`
    - `test_ties_keep_file_order`
    - `test_k_bounds` (0 and 11 raise `ValueError`)
    - `test_question_with_only_stop_words_returns_first_k`
  - `test_examples_yaml_passes_the_guard`: every example's SQL is accepted by `SqlGuard` with the real mart names from data platform spec §6.5.
- [ ] **Step 2:** Run (FAIL) → implement → run (PASS) → lint. Commit `feat: PII masking and the few-shot example index`.

---

### Task 4: Read-only database access

**Adds tooling:** `psycopg_pool` (in the `psycopg[pool]` extra already added).

**Files:** `database.py`, `tests/test_database.py`, `tests/conftest.py` (`warehouse` fixtures: settings from `.env`, an `AgentDatabase`, and an admin connection for setup checks).

**Interfaces — Produces:**
- `WarehouseToolError(Exception)`: a message meant for the model.
- `QueryResult` (frozen): `columns: tuple[str, ...]`, `rows: tuple[tuple[object, ...], ...]` (JSON-safe values: `Decimal` → `str`, dates and timestamps → ISO strings), `returned_rows: int`, `truncated: bool`, `elapsed_ms: int`.
- `AgentDatabase(settings: McpSettings)`:
  - a `psycopg_pool.ConnectionPool` (min 1, max 4) on `settings.agent_conninfo()`
  - `fetch(query: sql.Composable | str, params: Sequence[object] = (), max_rows: int = 100) -> QueryResult`
  - `explain(sql_text: str) -> None`, which raises `WarehouseToolError` with Postgres's message
  - `scalar(query, params) -> object`
  - `close()`

  Each call is one read-only transaction that first runs `SET LOCAL statement_timeout = '5s'`, then executes on a **named server-side cursor** and `fetchmany(max_rows + 1)`.
  - **Errors:** a timeout (`QueryCanceled`) becomes `WarehouseToolError("the query took longer than 5 s; aggregate or filter more narrowly")`. `InsufficientPrivilege` and other Postgres errors become a `WarehouseToolError` with Postgres's message.
  - `set_similarity_threshold=True` (used by `search_values`) adds `SET LOCAL pg_trgm.similarity_threshold = 0.2`.

- [ ] **Step 1: Write failing tests** (`TestAgentDatabase`, marker `warehouse`):
  - `test_session_is_read_only`: `show transaction_read_only` → `on`.
  - `test_fetch_caps_and_flags_truncation`: `select * from marts.fct_order_items` gives 100 rows and `truncated`.
  - `test_fetch_exact_cap_is_not_truncated`: `… limit 100` gives 100 rows, not truncated.
  - `test_timeout_is_reported_and_connection_reusable`: an aggregate over a self cross join of `fct_order_items` raises the timeout error within ~6 s, and the next `fetch` succeeds.
  - `test_write_attempt_is_refused`: `create table marts.x(i int)` sent straight to `fetch`, bypassing the guard, raises a `WarehouseToolError`.
  - `test_role_settings_unchanged`: an admin query of `pg_db_role_setting` for `sherpa_agent` equals its value before the test module ran (spec §4.2 item 5).
  - `test_values_are_json_safe`: a `Decimal`, a `date` and a `timestamptz` come back as strings.
- [ ] **Step 2:** `uv run --env-file .env pytest -m warehouse agent/mcp-server/tests/test_database.py -v`. Expected: FAIL. Implement. Re-run. Expected: PASS (needs `make build-prod`).
- [ ] **Step 3:** Commit `feat: read-only agent database access with timeout and row cap`.

---

### Task 5: The eight tools

**Files:** `tools.py`, `tests/test_tools.py`.

**Interfaces — Consumes:** Tasks 1–4.

**Produces:** `WarehouseTools(loader: MartCatalogLoader, database: AgentDatabase, examples: ExampleIndex)`. Each public method maps to one tool (spec §4.1) and takes and returns frozen Pydantic models.

| Method → tool | Input | Output model |
|---|---|---|
| `get_context()` | — | `WarehouseContext(as_of_date, first_order_date, domains, covers: tuple[str, ...], does_not_cover: tuple[str, ...])` |
| `list_tables()` | — | `TableList(tables: tuple[TableSummary(name, description, domain, grain, primary_key), ...])` |
| `describe_table(table)` | `table: str` | `TableDescription(name, description, grain, primary_key, columns: tuple[ColumnDescription(name, data_type, description, is_pii, accepted_values, synonyms)], sample_rows: QueryResult)` |
| `search_values(table, column, term, limit=10)` | `limit` 1..50 | `ValueMatches(matches: tuple[ValueMatch(value, similarity, row_count), ...])` |
| `get_metric_definitions()` | — | `MetricList(metrics: tuple[MetricDefinition, ...])` |
| `get_examples(question, k=3)` | — | `ExampleList(examples: tuple[Example, ...])` |
| `validate_sql(sql)` | — | `Validation(ok, errors, tables)` |
| `run_sql(sql)` | — | `SqlResult(execution_id, columns, rows, returned_rows, truncated, elapsed_ms)` |

Decisions:
- **`get_context`:**
  - `as_of_date` = `select max(order_date) from marts.fct_orders` (data platform spec §6.5); `first_order_date` = `min`.
  - `covers` lists the catalog's domains with their marts.
  - `does_not_cover` is a fixed tuple: inventory and stock, product reviews, currencies other than EUR, and (until data-platform-3/4) web sessions, marketing campaigns and ad spend, leads.
- **Table and column names** are resolved through the catalog (case-insensitive). Unknown names raise `WarehouseToolError` listing the valid ones.
- **`describe_table` samples:** `select * from marts.<t> order by <primary key> limit 3`, masked.
- **`search_values`:**
  - only for `text` columns, and never PII ones; both are checked from the catalog
  - SQL (identifiers via `sql.Identifier`): `select <col> as value, similarity(<col>, %s) as similarity, count(*) as row_count from marts.<t> where <col> % %s group by 1 order by 2 desc, 1 limit %s`, with the similarity threshold set
- **`validate_sql`:** the guard first; if it passes, `explain`; errors from both are returned, never raised.
- **`run_sql`:**
  - the guard first; a rejection raises `WarehouseToolError` with the guard's errors
  - then `fetch` the text **exactly as written**, minus trailing `;` and whitespace
  - mask, `execution_id = sha256(executed_sql)[:16]`
- **The catalog** is loaded per call through the loader, so artifact changes take effect. `CatalogUnavailable` becomes `WarehouseToolError`.

- [ ] **Step 1: Write failing tests** (`TestWarehouseTools`, marker `warehouse`, against the real prod marts; the catalog loader points at `data-platform/dbt/target-prod`):
  - `test_get_context_as_of_and_scope`: `as_of_date` equals an admin `max(order_date)`; `does_not_cover` mentions inventory.
  - `test_list_tables_are_the_nine_marts`: the names equal `MARTS` in `data-platform/tests/dbt/test_agent_access.py`.
  - `test_describe_table_samples_are_ordered_by_pk_and_masked`: `dim_customers` samples have `email == "***"` and ascending `customer_id`.
  - `test_describe_table_unknown_name_lists_valid_ones`.
  - `test_search_values_finds_brand_by_typo`: a misspelt brand from the catalog returns the right brand first.
  - `test_search_values_term_with_metacharacters`: terms `%`, `_`, `o'neil`, `\` return a list (possibly empty) and no error.
  - `test_search_values_refuses_pii_and_non_text`: `dim_customers.email`, and a numeric column.
  - `test_get_metric_definitions_lists_the_doc_blocks`: names equal `gross_revenue, discount_amount, refunded_revenue, net_revenue, cogs, gross_margin, aov, refund_rate`.
  - `test_get_examples_returns_k_and_every_example_runs`: every `examples.yaml` SQL executes through `run_sql`, returns ≥ 1 row and isn't truncated.
  - `test_validate_sql_reports_guard_and_explain_errors`: a staging reference fails the guard; `select nope from marts.fct_orders` fails `EXPLAIN`.
  - `test_run_sql_truncates_masks_and_ids`: `select * from marts.fct_order_items` → 100 rows, `truncated`; `select email from marts.dim_customers limit 5` → all `***`; the same SQL with different trailing whitespace gives the same `execution_id`.
  - `test_run_sql_rejects_hostile_sql_with_guard_errors`.
  - `test_tools_report_missing_artifacts` (not `warehouse`): a loader on an empty `tmp_path` makes every tool raise `WarehouseToolError` mentioning `make build-prod`.
- [ ] **Step 2:** Run (FAIL) → implement → run (PASS) → lint. Commit `feat: the eight warehouse tools`.

---

### Task 6: FastMCP server, both transports, Logfire

**Adds tooling:**
- `logfire[psycopg]` in group `mcp`
- console script `sherpa-mcp = "sherpa_mcp.server:main"`
- `.env.example` += `MCP_TRANSPORT=stdio`, `SHERPA_MCP_PORT=8765`, `LOGFIRE_TOKEN=` (commented, optional)
- Makefile target **`mcp-dev`**: `MCP_TRANSPORT=http uv run --env-file .env sherpa-mcp`, the server on the host over HTTP, for MCP Inspector

**Files:** `server.py`, `tests/test_server.py`, `tests/conftest.py` (`mcp_client` fixture).

**Interfaces — Produces:**
- `build_server(tools: WarehouseTools) -> FastMCP`: registers the eight tools under the spec's names, with descriptions taken from spec §4.1 and written for a model. Each handler is a thin function that calls the matching `WarehouseTools` method and returns its model; a `WarehouseToolError` turns into an MCP tool error.
- `main() -> None`:
  - logging to **stderr**
  - `logfire.configure(service_name="sherpa-mcp", console=False, send_to_logfire="if-token-present")` and `logfire.instrument_psycopg()`
  - builds `McpSettings`, the loader, the database and the examples
  - runs `transport="stdio"`, or `"streamable-http"` on `mcp_host:mcp_port` at path `/mcp`
- **`mcp_client` fixture** (async, `anyio`), parametrized `["stdio", "http"]`:
  - **stdio:** `stdio_client(StdioServerParameters(command="uv", args=["run", "sherpa-mcp"], env=<explicit SHERPA_*, MCP_*, PATH, SystemRoot from the test env>))`. Explicit, because the SDK passes only a minimal environment (spec §3).
  - **http:** starts `sherpa-mcp` as a subprocess with `MCP_TRANSPORT=http` and a free port, waits until the port accepts, and connects with `streamablehttp_client`.

- [ ] **Step 1: Write failing tests** (`TestServer`, marker `warehouse`, parametrized over both transports via `mcp_client`):
  - `test_lists_the_eight_tools`
  - `test_same_answers_over_both_transports`: `list_tables`, `get_context`, `run_sql("select count(*) from marts.fct_orders")` give the same structured content on each transport
  - `test_tool_error_is_an_mcp_error`: `run_sql("delete from marts.fct_orders")` returns `isError` with the guard's message
  - `test_stdio_stdout_carries_only_protocol`: not parametrized. Run the server over stdio, send `initialize` and `tools/list` by hand, and assert that every stdout line parses as a JSON-RPC message.
- [ ] **Step 2:** Run (FAIL) → implement → run (PASS) → lint.
- [ ] **Step 3: Manual check (spec acceptance):** `make mcp-dev`, then MCP Inspector (`npx @modelcontextprotocol/inspector`) against `http://127.0.0.1:8765/mcp`. Every tool lists and runs. This needs internet for `npx`. Record the result in the commit message.
- [ ] **Step 4:** Commit `feat: MCP server over stdio and Streamable HTTP with Logfire`.

---

### Task 7: Docker image and the compose `mcp` service

**Adds tooling:**
- `agent/mcp-server/Dockerfile`
- the compose service `mcp`
- Makefile: `up` becomes `docker compose up -d --build --wait`, so it starts **every** service (Postgres and `mcp`); compose builds from cache, so an unchanged image costs seconds. No per-service start targets: the rare database-only case is `docker compose up -d postgres`, shown in the guide. `down` is unchanged and stops everything.

**Files:** `agent/mcp-server/Dockerfile`, `docker-compose.yml`, `Makefile`.

Decisions:
- **Image:**
  - `python:3.12-slim` plus `uv`, with the **repository root as build context**
  - copies `pyproject.toml`, `uv.lock` and `agent/mcp-server/src`
  - `uv sync --frozen --no-default-groups --group mcp --no-install-project`
  - `PYTHONPATH=/app/agent/mcp-server/src`
  - command `python -m sherpa_mcp.server`
  - a non-root user
- **Service `mcp`:**
  - `env_file: .env`, then `environment`: `SHERPA_DB_HOST: postgres`, `SHERPA_DB_PORT: "5432"`, `MCP_TRANSPORT: http`, `SHERPA_MCP_HOST: 0.0.0.0`, `SHERPA_DBT_TARGET_PATH: /app/dbt/target-prod`
  - volume `./data-platform/dbt/target-prod:/app/dbt/target-prod:ro`
  - ports `127.0.0.1:${SHERPA_MCP_PORT:-8765}:8765`
  - `depends_on: postgres: condition: service_healthy`
  - a healthcheck that opens a TCP connection to 8765 with Python
- **Default and HTTP binding:** the server binds `127.0.0.1` by default and `0.0.0.0` only inside the container, where the published port is localhost-only.

- [ ] **Step 1:** Implement. `docker compose config` shows the service.
- [ ] **Step 2:** `make up`. Expected: `postgres` and `mcp` healthy. Before `make build-prod` has produced artifacts, `mcp` still starts and its tools report the missing artifacts (Review Focus 4). Then run the Task 6 HTTP tests against the container with `MCP_HTTP_URL=http://127.0.0.1:8765/mcp`. Add a `mcp_client` variant `"container"` that is used only when `SHERPA_TEST_MCP_CONTAINER=1`, so the default suite doesn't need Docker. Expected: PASS.
- [ ] **Step 3:** `make down && make up`: both services come back healthy, and the warehouse data is unchanged (shared volume). Commit `feat: mcp docker image and compose service`.

---

### Task 8: Documentation and how-to guides

**Files:**
- Create `docs/guides/basic/using-the-mcp-server.md`, `docs/guides/advanced/extending-the-mcp-server.md`.
- Modify `README.md`, `CLAUDE.md`.

All docs describe the current state only. Run every command in a guide once while writing it, and show its real output.

- [ ] **Step 1: `basic/using-the-mcp-server.md`:**
  - what the server is for, and the read-only guarantees in plain words
  - starting it: `make up` (the container, with everything else), `make mcp-dev` (on the host, HTTP, reloading), and stdio for clients that spawn it
  - connecting MCP Inspector
  - **every tool** with one real call and its real output (trimmed)
  - what a guard rejection, a timeout and a missing-artifacts error look like, and what to do
  - masking: what's masked, and the name-based limitation
- [ ] **Step 2: `advanced/extending-the-mcp-server.md`:**
  - **adding a tool:** model, `WarehouseTools` method, registration, tests over both transports
  - **allowing a new SQL function:** the allow-list, why each addition needs a guard test, and the mutation run
  - **adding or changing a few-shot example,** and the gold-set disjointness rule
  - **what changes when the data platform adds marts:** nothing in code, a catalog reload, the `MARTS` lists in tests
- [ ] **Step 3: `README.md`:**
  - status: agent-design-1 done
  - layout: `agent/mcp-server/`
  - Guides list: the two new guides
- [ ] **Step 4: `CLAUDE.md`:**
  - Commands: `make up` (every service: Postgres and `mcp`), `make mcp-dev`
  - the generator-only mention in "mypy runs on" now lists both source trees
  - current plan → the next one per the sequencing
- [ ] **Step 4b:** `make up` now starts every service. Rewrite every description of it as "start Postgres" in `README.md`, `CLAUDE.md` and `docs/guides/` (`grep -rn "make up"` lists them). Where a guide only needs the database, show `docker compose up -d postgres`.
- [ ] **Step 5:** Check every command, path, module, class and Make target the touched docs name exists. Expected: no dangling references.
- [ ] **Step 6:** Commit `docs: MCP server guides, README and CLAUDE.md for agent-design-1`.
