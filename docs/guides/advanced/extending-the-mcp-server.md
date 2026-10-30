# Extending the MCP server

How to change `sherpa_mcp`: add a tool, allow a SQL function, change guard hints, upgrade sqlglot, add an example, and react to new marts. Each section lists the files to touch, in order, and the tests that prove the change. Read [Using the MCP server](../basic/using-the-mcp-server.md) first. The guard rules themselves are in the [agent spec](../../superpowers/specs/2026-10-22-sql-sherpa-agent-design.md) §4. Commands run from the repository root.

## Module map

Code is in `agent/mcp-server/src/sherpa_mcp/`; tests in `agent/mcp-server/tests/`, one file per module.

| File | Holds |
|---|---|
| `server.py` | `build_server` (registers the eight tools and their descriptions), `main` (logging, Logfire, startup check, transport) |
| `tools.py` | `WarehouseTools` (one method per tool), the frozen output models, the scope texts `DOMAIN_SUBJECTS` (one phrase per `meta.domain`, `marketing_web` included), `DOES_NOT_COVER`, `NOTES` |
| `sql_guard.py` | `SqlGuard`, `ALLOWED_FUNCTIONS`, the hint maps, the `[guard:<rule>]` codes; pure |
| `masking.py` | `PiiMasker`; pure |
| `catalog.py` | `MartCatalogLoader`, `MartCatalog`, built from `manifest.json` and `catalog.json` |
| `database.py` | `AgentDatabase`: pool, read-only transactions, timeout, row, cell and result caps, error codes |
| `examples.py`, `examples.yaml` | The few-shot index and its data |
| `settings.py` | `McpSettings`, the only place that reads configuration |

## Test layers

| Layer | Tests | Needs | Run |
|---|---|---|---|
| Pure | `test_sql_guard.py`, `test_masking.py`, `test_examples.py`, `test_catalog.py` | Python only | `uv run pytest agent/mcp-server/tests -q` |
| Warehouse | `test_tools.py`, `test_server.py` | Postgres and `make build-prod` | `uv run --env-file .env pytest agent/mcp-server/tests -m warehouse` |
| Container | `test_server.py` over the compose `mcp` service | `SHERPA_TEST_MCP_CONTAINER=1`, `mcp` service up | add the variable to the warehouse command |

`make test` runs the first layer (plus the other components' tests).

## 1. Add a tool

Example: `count_rows(table)`. Files, in order:

**1. `tools.py`: output model and method.** Outputs are frozen Pydantic models; put the logic in a `WarehouseTools` method so it is testable without MCP.

```python
class RowCount(BaseModel):
    model_config = ConfigDict(frozen=True)

    table: str
    rows: int


class WarehouseTools:
    def count_rows(self, table: str) -> RowCount:
        mart = _resolve_mart(self._catalog(), table)
        query = sql.SQL("select count(*) from {}.{}").format(sql.Identifier(MARTS_SCHEMA), sql.Identifier(mart.name))
        return RowCount(table=mart.name, rows=int(str(self._database.scalar(query))))
```

Rules to follow:

- Resolve user-given names with `_resolve_mart` / `_resolve_column`; build SQL with `psycopg.sql`; pass values as bound parameters.
- Run every query through `AgentDatabase` (never open another connection). `fetch` and `scalar` are for the server's own queries; `fetch_for_model` is for rows the model reads (JSON-safe, cell cap, result budget); `explain` plans without running.
- Raise `WarehouseToolError` for failures the model can act on, with a message saying what to do. Anything else reaches the model only as `Error executing tool <name>`.
- Model-written SQL goes through `self._guard(...)` first, and its result through `PiiMasker`.

**2. `server.py`: registration in `build_server`.** Import `RowCount` with the other models. The description is what the model reads when choosing: say when to call it, what it returns, what it does not check. Bound every input in its `Field` (`max_length`, `ge`/`le`).

```python
    @server.tool(description="The number of rows in one mart. Use it to size a table before selecting from it.")
    def count_rows(table: TableName) -> RowCount:
        with _tool_errors("count_rows"):
            return tools.count_rows(table)
```

**3. Tests.**

- `test_tools.py` (`warehouse`): call the method, including the unhappy path (an unknown table raises `WarehouseToolError` listing the valid tables).
- `test_server.py`: add the name to `TOOL_NAMES` and one call to `PARITY_CALLS`; rename `test_lists_the_eight_tools`. `mcp_client` runs each test over stdio, HTTP and the compose container, so one test proves identical behaviour on all three. Add input bounds to the schema and out-of-bounds tests in `TestBuildServer`.
- `test_stdio_stdout_carries_only_protocol` guards stdout: no `print`, log through `logging`.

**4. Container and docs.** Update every "eight tools" (docstrings of `server.py` and `tools.py`, the agent spec layout, both MCP guides, the README guide list). Add the tool to [Using the MCP server](../basic/using-the-mcp-server.md) and the spec's tool table (§4.1). Then:

```bash
uv run --env-file .env pytest agent/mcp-server/tests/test_tools.py agent/mcp-server/tests/test_server.py -m warehouse
docker compose up -d --build --wait --no-deps mcp
SHERPA_TEST_MCP_CONTAINER=1 uv run --env-file .env pytest agent/mcp-server/tests/test_server.py -m warehouse
```

## 2. Allow a new SQL function

A function not on the list fails with `[guard:function] function xyz is not allowed`. Every addition widens what a hostile query can reach, so first ask whether the model can do without. It must be harmless in a read-only role:

- no side effects, file or network access;
- no catalog or session introspection (`pg_*`, `current_setting`, `dblink*`, `lo_*`, `query_to_xml`);
- no unbounded output (`generate_series`, `repeat`; `lpad`/`rpad` pass only with a literal length up to 10,000);
- no running a nested query from a string.

**1. `sql_guard.py`, `ALLOWED_FUNCTIONS`.** Names are the ones **sqlglot** reports, not always the Postgres spelling (`string_agg` is `group_concat`, `date_trunc` is `timestamp_trunc`, `~` is `regexp_like`; the comment above the list records the mapping). Ask sqlglot:

```bash
uv run python - <<'EOF'
import sqlglot
from sqlglot import exp

func = sqlglot.parse_one("select string_agg(sales_channel, ',') from marts.fct_orders", read="postgres").find(exp.Func)
print(func.name.lower() if isinstance(func, exp.Anonymous) else func.sql_name().lower())
EOF
```

```
group_concat
```

List the sqlglot name (and the Postgres one if both can appear) and extend the mapping comment. Some already pass: `btrim` is read as `trim`. `ALLOWED_FUNCTIONS` is the default of `SqlGuard`'s `allowed_functions`, so another caller can pass its own set.

**2. `test_sql_guard.py`.** The list is the safety boundary and nothing else checks it:

- update the set in `TestAllowedFunctions::test_the_allow_list_is_exactly_the_specs`: it compares the full spec §4.2 list with `ALLOWED_FUNCTIONS`, so an addition fails it until you list the name there under its category;
- check the rejected-family rows (the parameters of `test_a_rejected_family_member_is_refused_by_name`): your function must not belong to a family the spec rejects (`pg_*` admin and file functions, `dblink*`, `lo_*`, `*_to_xml`, `setval`/`nextval`/`currval`, `current_setting`/`set_config`), and a new rejected family needs at least three members there;
- add the call, written as a model would, to the `test_accepts_every_allowed_function` parameters;
- add any abusable form (subquery argument, schema-qualified spelling, table-valued form, unbounded size) to `test_rejects_hostile_inputs` with the expected error fragment.

**3. Mutation check.** Remove your name from `ALLOWED_FUNCTIONS` and confirm a test fails; allow a function that must stay rejected and confirm a hostile-input test fails. Restore the line (`git diff` shows only your change). A mutation no test notices means a missing test.

```bash
uv run pytest agent/mcp-server/tests/test_sql_guard.py -q
```

The spec (§4.2) names categories, not functions; update it only for a new category.

## 3. Change a guard hint

Every rejection is `[guard:<rule>] <message>; <hint>`. Hints live in `sql_guard.py`: `FUNCTION_HINTS` (named functions), `JSON_HINT`, `_OPERATOR_HINTS`, and the defaults `DEFAULT_FUNCTION_HINT` / `DEFAULT_OPERATOR_HINT`. A hint says what to write instead, in a few words, and never repeats the query. The `<rule>` codes are stable: `tools._rule_class` reads them for the `sherpa.guard` trace, so add a rule only for a new kind of check.

Tests in `test_sql_guard.py`: `test_rejection_carries_a_remedy_hint` (one case per hint) and `test_every_error_starts_with_its_rule_code`.

## 4. Upgrade sqlglot

The pin in `pyproject.toml` (`sqlglot==...`) is exact: the parser is the guard's view of a query, so a bump is a guard change.

1. Change the pin and run `uv lock`.
2. Run the guard suite (hostile inputs included) and the example check:

   ```bash
   uv run pytest agent/mcp-server/tests/test_sql_guard.py agent/mcp-server/tests/test_examples.py -q
   ```

3. On a failure, inspect the new tree (`sqlglot.parse_one(sql, read="postgres")`) before touching the test. A hostile input that now passes is a guard bug to fix in `sql_guard.py`, never an assertion to relax.

## 5. Add or change a few-shot example

`get_examples` ranks the entries of `examples.yaml` (each has `question`, `sql`, `notes`) against the question and returns the best `k` with scores.

**Ranking** (`examples.py`): words are reduced to stems (`STOP_WORDS` removed, `SUFFIXES` rules), `SYNONYMS` add stems (`aov` adds `average`, `order`, `value`). Score = `QUESTION_WEIGHT` (0.75) x Jaccard with the example's question + 0.25 x Jaccard with its question, notes and the table and column names of its SQL. Score 0 is never returned.

1. **Write it.** The SQL follows the model's rules: `marts.<mart>` tables, allowed functions, metrics as `get_metric_definitions` defines them, relative periods from `max(order_date)`. Teach a pattern (period, top-N, ratio, cross-grain join, funnel flag), not a likely verbatim question. Check every number in the notes with a read-only query.
2. **Keep it disjoint from the gold set** (agent spec §7): no example may paraphrase a gold question, or the agent copies the answer and its score inflates. Compare closest examples to gold cases by Jaccard once `agent/evals/` exists.
3. **Test.** `test_examples.py`: `test_examples_yaml_passes_the_guard` (guard, non-empty notes, example count, currently `19`: change it when you add or remove one) and `test_examples_yaml_ranks_each_question_first`. `TestExampleIndex` pins scores if stem or synonym rules change. Then run each example on the warehouse and rebuild the container (it bakes in the file):

```bash
uv run pytest agent/mcp-server/tests/test_examples.py -q
uv run --env-file .env pytest agent/mcp-server/tests/test_tools.py -m warehouse -k every_example_runs -q
docker compose up -d --build --wait --no-deps mcp
```

## 6. When the data platform adds marts

The query code does not change: marts, domains, grains, PII flags, synonyms and metric references come from the dbt artifacts, and the guard's visible tables and columns from the same catalog.

1. `make build-prod` refreshes `manifest.json` and `catalog.json` in `data-platform/dbt/target-prod/`. A running server, including the container (it mounts `data-platform/dbt` read-only), reloads when their modification time changes. Mark a new PII column `meta.pii: true` in the dbt YAML first; masking, `search_values` and `distinct_values` follow that flag.
2. Update the `MARTS` lists in the tests: `data-platform/tests/dbt/test_reader_access.py` (read by `test_tools.py` to check `list_tables`) and `agent/mcp-server/tests/test_examples.py` (compared to the catalog).
3. Update the scope texts in `tools.py`: add a `DOMAIN_SUBJECTS` phrase for a new domain, and drop the `DOES_NOT_COVER` entries the new marts now cover (the campaign entry stays until data-platform-4 adds its mart); add a `NOTES` line for a trap the marts have (sessions are web only). `test_get_context_as_of_and_scope` in `test_tools.py` checks them.
4. Run `make test` and `uv run --env-file .env pytest -m warehouse`. Add an example if the mart brings a new pattern (section 5).

## Common errors

| Symptom | Fix |
|---|---|
| `[guard:function] function xyz is not allowed` | Section 2; check the sqlglot name, not the Postgres one |
| Allowed function still rejected | The list holds the sqlglot name; see the snippet in section 2 |
| Tool crashes as `Error executing tool <name>` | Raise `WarehouseToolError` for expected failures |
| `test_every_tool_answers_as_in_process` fails | The tool is missing from `PARITY_CALLS` or `TOOL_NAMES` |
| `test_stdio_stdout_carries_only_protocol` fails | Something printed to stdout; use `logging` |
| `test_examples_yaml_passes_the_guard` fails on the count | Update the example count (`19`) |
| Container lacks the new tool or example | `docker compose up -d --build --wait --no-deps mcp` |
| New mart missing from `list_tables` | `make build-prod`, then update both `MARTS` lists |
