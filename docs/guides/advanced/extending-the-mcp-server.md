# Extending the MCP server

How to change what `sherpa_mcp` offers: add a tool, allow a new SQL function, change the guard's hints, upgrade sqlglot, add or change a few-shot example, and what to do when the data platform adds marts. Each section lists the files to touch, in order, and the tests that prove the change. Read [Using the MCP server](../basic/using-the-mcp-server.md) first. Commands run from the repository root; the guard and example tests need nothing but Python, while the tool and server tests need Postgres and the prod warehouse (`make build-prod`).

The code is in `agent/mcp-server/src/sherpa_mcp/`:

| File | Holds |
|---|---|
| `server.py` | `build_server` (registers the eight tools on an `MCPServer`, with the descriptions the model reads), `main` (logging, Logfire, the startup connection check, transport choice) |
| `tools.py` | `WarehouseTools`, one method per tool, the frozen Pydantic models they return, and the fixed scope texts (`DOMAIN_SUBJECTS`, `DOES_NOT_COVER`, `NOTES`) |
| `sql_guard.py` | `SqlGuard(mart_columns, allowed_functions=ALLOWED_FUNCTIONS)`, the hint maps and the `[guard:<rule>]` codes; pure, no I/O |
| `masking.py` | `PiiMasker`; pure |
| `catalog.py` | `MartCatalogLoader` and the `MartCatalog` model built from `manifest.json` and `catalog.json`; pure parsing plus file reads |
| `database.py` | `AgentDatabase`: the pool, read-only transactions, timeout, batched fetch, row cap, cell cap and result budget, the error codes |
| `examples.py`, `examples.yaml` | The few-shot index (stemming, synonyms, weighted Jaccard) and its data |
| `settings.py` | `McpSettings`, the only place that reads configuration |

Tests are in `agent/mcp-server/tests/`, one file per module. The ones marked `warehouse` need the built prod warehouse; the guard, masking, example and catalog tests do not.

**The layout stays flat** for now: nine modules in one package. It is restructured when ui-1 becomes the second importer of `sql_guard` and `masking`, when `tools.py` passes about 450 lines, or when a new tool arrives, whichever comes first. The likely shape then is a safe-SQL core (`sql_guard`, `masking`, `catalog` and the cell helpers of `database.py`) apart from the MCP adapter (`server.py`, `tools.py`), with re-export modules so that `sherpa_mcp.sql_guard` and `sherpa_mcp.catalog` keep working as import paths.

## 1. Add a tool

Example: a `count_rows(table)` tool. Four files, in this order.

**1. The output model and the method** (`tools.py`). Tool inputs are method parameters; outputs are frozen Pydantic models (no bare dicts or tuples cross the boundary). Put the logic in a `WarehouseTools` method, so it is testable without MCP:

```python
class RowCount(BaseModel):
    model_config = ConfigDict(frozen=True)

    table: str
    rows: int


class WarehouseTools:
    ...

    def count_rows(self, table: str) -> RowCount:
        mart = _resolve_mart(self._catalog(), table)
        query = sql.SQL("select count(*) from {}.{}").format(sql.Identifier(MARTS_SCHEMA), sql.Identifier(mart.name))
        return RowCount(table=mart.name, rows=int(str(self._database.scalar(query))))
```

Rules the existing methods follow, and yours should too:

- Resolve user-given names against the catalog (`_resolve_mart`, `_resolve_column`) instead of pasting them into SQL; build SQL with `psycopg.sql` identifiers and literals, and pass values as bound parameters.
- Run every query through `AgentDatabase`. They give you the read-only transaction, the 5 s timeout, the row cap and the error mapping. Never open another connection.
  - `fetch` and `scalar` are for the server's own queries, whose size it controls: values come back as psycopg loads them.
  - `fetch_for_model` is for rows the model reads (model-written SQL, sample rows): JSON-safe values, the 10,000-character cell cap, the 50,000-character result budget, and at most two of them at once.
  - `explain` plans a query without running it.
- Raise `WarehouseToolError` for a failure the model can act on, with a message that says what to do. Database and catalog errors already carry their codes (`[sql]`, `[timeout]`, `[unreachable]`, `[unavailable]`). Anything else is a crash that the model sees only as `Error executing tool <name>`.
- If the tool can run model-written SQL, send it through `self._guard(...)` first, and mask the result with `PiiMasker`.

**2. The registration** (`server.py`, in `build_server`). The handler delegates to the `WarehouseTools` method inside `_tool_errors`, which turns a `WarehouseToolError` into a tool error. The description is what the model reads when it chooses a tool, so say when to call it, what it returns, and what it does not check. Bound every input in its `Field` (`max_length` for text, `ge`/`le` for numbers); the SDK checks the schema before the tool runs:

```python
    @server.tool(description="The number of rows in one mart. Use it to size a table before selecting from it.")
    def count_rows(table: TableName) -> RowCount:
        with _tool_errors("count_rows"):
            return tools.count_rows(table)
```

Import `RowCount` at the top of `server.py` with the other models. Keep the registered tools to the ones a model needs: every tool's description is paid for on each request.

**3. The tests.**

- `tests/test_tools.py` (marked `warehouse`): call the method against the real warehouse, including the unhappy path (an unknown table must raise `WarehouseToolError` that lists the valid tables).
- `tests/test_server.py`: add the name to `TOOL_NAMES` and one call to `PARITY_CALLS`. `test_lists_the_eight_tools` then covers the listing (rename it to the new count), and `test_every_tool_answers_as_in_process` checks that `PARITY_CALLS` names every tool and that each call returns over the transport what the method returns in process. The `mcp_client` fixture runs each test over **stdio** (a subprocess), **HTTP** (a subprocess on a free port) and the **compose `mcp` container** (skipped unless `SHERPA_TEST_MCP_CONTAINER=1`), so one test proves identical behaviour on all of them. If the new input has bounds, add them to the schema test and the out-of-bounds test in `TestBuildServer`.
- Wherever the docs and docstrings say "eight tools" (the docstrings of `server.py` and `tools.py`, the agent spec's repository layout, both guides and the README's guide list), change the count.

```bash
uv run --env-file .env pytest agent/mcp-server/tests/test_tools.py agent/mcp-server/tests/test_server.py -m warehouse
```

`test_server.py` also holds the check that stdout carries only protocol messages in stdio mode (`test_stdio_stdout_carries_only_protocol`). A new tool must not `print`, and logging goes through `logging` (stderr).

**4. The container and the docs.** Rebuild the service so it serves the new tool: `docker compose up -d --build --wait --no-deps mcp`, and run the same tests against it:

```bash
SHERPA_TEST_MCP_CONTAINER=1 uv run --env-file .env pytest agent/mcp-server/tests/test_server.py -m warehouse
```

Then add the tool to [Using the MCP server](../basic/using-the-mcp-server.md) with one real call, and to the tool table in the agent spec (§4.1).

## 2. Allow a new SQL function

The guard rejects every function that is not on the list, so the common case is a query that fails with `[guard:function] function xyz is not allowed`. Before you allow it, ask whether the model can do without; every addition widens what a hostile or confused query can reach.

**What to check first.** The function must be harmless in a read-only role: no side effects, no file or network access, no catalog or session introspection (`pg_*`, `current_setting`, `dblink*`, `lo_*`, `query_to_xml` stay out), no unbounded output (`generate_series`, `repeat`; `lpad` and `rpad` pass only with a literal length up to 10,000, as do type lengths such as `char(n)`), and no way to run a nested query from a string.

**1. The list** (`sql_guard.py`, `ALLOWED_FUNCTIONS`). The names are the ones **sqlglot** reports, not always the Postgres spelling. sqlglot parses some Postgres functions into nodes with another name (`string_agg` becomes `group_concat`, `date_trunc` becomes `timestamp_trunc`, `~` becomes `regexp_like`), and the comment above the list records the mapping. The guard's error message names the function as the model wrote it (`function string_agg ...`), so find the name the list compares against by asking sqlglot:

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

When the Postgres spelling and that name differ, list the sqlglot name (and the Postgres one too when either can appear) and add the mapping to the comment. It also works the other way: `btrim(...)` already passes because sqlglot reads it as `trim`, so check before you add anything.

`ALLOWED_FUNCTIONS` is the default of `SqlGuard`'s `allowed_functions` parameter. Another caller (the UI's query runner, from ui-1) can pass its own set without changing the agent's.

**2. The tests** (`tests/test_sql_guard.py`). Every addition needs a test, because the list is the guard's safety boundary and nothing else checks it:

- Add a call to the `test_accepts_every_allowed_function` parameter list, written the way a model would write it (`"translate(b, 'x', 'y')"`). The test selects it from `marts.fct_orders` and expects no errors.
- If the function has a form that can be abused (a subquery as an argument, a schema-qualified spelling, a table-valued form in `FROM`, an unbounded size argument), add the hostile form to the `test_rejects_hostile_inputs` list with the error fragment you expect.

```bash
uv run pytest agent/mcp-server/tests/test_sql_guard.py -q
```

```
........................................................................ [ 14%]
...
514 passed in 1.20s
```

**3. Prove the test can fail (a manual mutation check).** The guard tests are only worth as much as their ability to fail when the guard is wrong, so mutate the line you added: remove the name from `ALLOWED_FUNCTIONS` and confirm that a test fails. Do the same for a function that must stay rejected: allow it temporarily and confirm a hostile-input test fails. For example, removing `initcap`:

```
FAILED agent/mcp-server/tests/test_sql_guard.py::TestSqlGuard::test_accepts_every_allowed_function[initcap(b)]
1 failed, 513 passed in 1.59s
```

Restore the line afterwards (`git diff` must show only your intended change). A mutation that no test notices means the test is missing.

**4. The docs.** The list of allowed functions is `ALLOWED_FUNCTIONS` itself; the agent spec §4.2 names the categories. Update the spec only when a new category appears.

## 3. Change a guard hint

Every rejection is `[guard:<rule>] <message>; <hint>`. The hints live in `sql_guard.py`: `FUNCTION_HINTS` for named functions (`generate_series`, `to_timestamp`, `grouping`, ...), `JSON_HINT` for the JSON functions, `_OPERATOR_HINTS` for operators (`= ANY(ARRAY[...])`, `~`), and the defaults `DEFAULT_FUNCTION_HINT` and `DEFAULT_OPERATOR_HINT`. A hint says what to write instead, in a few words, and never repeats the query (the message before it may quote it). The `<rule>` codes are stable: `tools._rule_class` reads them for the `sherpa.guard` trace, so add a rule only with a new kind of check.

Tests: `test_rejection_carries_a_remedy_hint` (one case per hint) and `test_every_error_starts_with_its_rule_code` in `tests/test_sql_guard.py`.

## 4. Upgrade sqlglot

sqlglot is pinned exactly in `pyproject.toml` (`sqlglot==...`): its parser is the guard's view of a query, so a new version can read a query differently from the one the tests were written against, and a query the guard used to reject might parse into something it accepts. Treat a bump as a guard change:

1. Change the pin and run `uv lock`.
2. Run the whole guard suite, the hostile inputs included, and the example check:

```bash
uv run pytest agent/mcp-server/tests/test_sql_guard.py agent/mcp-server/tests/test_examples.py -q
```

```
...
536 passed in 0.95s
```

3. If a test fails, read what changed in sqlglot's parse (`sqlglot.parse_one(sql, read="postgres")` shows the tree) before you touch the test: a hostile input that now passes is a guard bug to fix in `sql_guard.py`, never an assertion to relax.

## 5. Add or change a few-shot example

`get_examples` ranks the entries of `agent/mcp-server/src/sherpa_mcp/examples.yaml` against the question and returns the best `k` with their scores. The file is a list of `question`, `sql` and `notes`:

```yaml
- question: What was net revenue last month?
  sql: |
    with period as (
        select
            (date_trunc('month', max(order_date)) - interval '1 month')::date as month_start,
            date_trunc('month', max(order_date))::date as next_month_start
        from marts.fct_orders
    )
    ...
  notes: >-
    Last month is the calendar month before the month of as_of_date (max(order_date)), not before today. ...
```

**How the ranking works** (`examples.py`). The question and each example are reduced to word stems (`STOP_WORDS` removed, the crude `SUFFIXES` rules, so `monthly`, `months` and `month` meet), and each word in `SYNONYMS` adds the stems it stands for (`aov` adds `average`, `order`, `value`). The score is `QUESTION_WEIGHT` (0.75) times the Jaccard similarity with the example's question plus 0.25 times the Jaccard similarity with everything the example says: its question, its notes and the table and column names its SQL uses. Examples with a score of 0 are never returned.

**1. Write the example.** The SQL must follow the same rules the model has to follow: `marts.<mart>` tables, allowed functions, named columns, metrics as `get_metric_definitions` defines them, relative periods derived from `max(order_date)`. Use the shape of a pattern (a period, top-N, ratio metric, a join across grains, a funnel flag) rather than a question a user is likely to ask verbatim. The notes say what is easy to get wrong, and every number or claim in them must be checked against the warehouse with a read-only query.

**2. Keep it disjoint from the gold set.** The evaluation's gold set (agent spec §7) is the yardstick for the agent. An example that paraphrases a gold question lets the agent copy the answer and inflates its score. The rule: no example question may be a paraphrase of a gold question, and the reviewer checks it by listing the closest example to each gold case by Jaccard similarity of their words. When you change an example, re-check it against the gold questions in `agent/evals/` once they exist, and make the wording or the pattern different if any gold question is close.

**3. Run the tests.** In `tests/test_examples.py`, `test_examples_yaml_passes_the_guard` runs every example through `SqlGuard` with the marts' columns, requires non-empty notes and asserts the number of examples (`19`; change it when you add or remove one), and `test_examples_yaml_ranks_each_question_first` checks that each example is the top match for its own question. If a synonym or stem rule changes, the ranking tests in `TestExampleIndex` pin the expected scores.

```bash
uv run pytest agent/mcp-server/tests/test_examples.py -q
```

```
......................                                                   [100%]
22 passed in 0.39s
```

Then run every example against the warehouse: `test_get_examples_returns_k_and_every_example_runs` in `tests/test_tools.py` requires at least one row and no truncation for each:

```bash
uv run --env-file .env pytest agent/mcp-server/tests/test_tools.py -m warehouse -k every_example_runs -q
```

```
.                                                                        [100%]
1 passed, 42 deselected in 0.50s
```

The container serves the file baked into its image, so rebuild it: `docker compose up -d --build --wait --no-deps mcp`.

## 6. When the data platform adds marts

Nothing in the server's query code changes. The marts come from the dbt artifacts: a model in the `marts` schema is a mart (from data-platform-4, only those with `access: public`), its domain, grain, primary key, PII flags, synonyms, accepted values and metric references come from its YAML, and the guard's list of visible tables and their columns is built from the same catalog.

What to do after the data platform builds a new or changed mart:

1. Run `make build-prod`, so `manifest.json` and `catalog.json` in `data-platform/dbt/target-prod/` are current. The server reloads when either file's modification time changes, so a running server (including the container, which mounts `data-platform/dbt` read-only) picks the change up on the next call, with no restart; `describe_table`'s stored values are read again for the new build. If the new mart has a PII column, mark it `meta.pii: true` in the dbt YAML first: the masking, `search_values` and `distinct_values` follow that flag. Changed descriptions and grains reach the tools through the rebuilt artifacts, and the Postgres comments (`persist_docs`) only through the rebuild.
2. Update the `MARTS` lists in the tests:
   - `data-platform/tests/dbt/test_reader_access.py` has the data platform's list of marts, which `tests/test_tools.py` reads to check that `list_tables` returns exactly those marts;
   - `agent/mcp-server/tests/test_examples.py` has its own `MARTS` set, which the example test compares to the catalog (or to the marts' dbt YAML without a prod build).
3. Update the scope texts in `tools.py`: add a `DOMAIN_SUBJECTS` phrase for a new domain (`marketing_web` from data-platform-4), and remove from `DOES_NOT_COVER` the entries the new marts now cover (the web sessions entry with data-platform-3's `fct_sessions`, the campaign and lead entries with data-platform-4). `test_get_context_as_of_and_scope` in `tests/test_tools.py` checks them.
4. Run the whole suite:

```bash
make test
uv run --env-file .env pytest -m warehouse
```

Add a few-shot example for the new mart's typical question if its pattern is new (section 5).
