# Extending the MCP server

How to change what `sherpa_mcp` offers: add a tool, allow a new SQL function, add or change a few-shot example, and what to do when the data platform adds marts. Each section lists the files to touch, in order, and the tests that prove the change. Read [Using the MCP server](../basic/using-the-mcp-server.md) first. Commands run from the repository root; the guard tests need nothing but Python, while the tool and server tests need Postgres and the prod warehouse (`make build-prod`, `make docs-prod`).

The code is in `agent/mcp-server/src/sherpa_mcp/`:

| File | Holds |
|---|---|
| `server.py` | `build_server` (registers the eight tools on an `MCPServer`), `main` (Logfire, transport choice) |
| `tools.py` | `WarehouseTools`, one method per tool, and the frozen Pydantic models they return |
| `sql_guard.py` | `SqlGuard` and `ALLOWED_FUNCTIONS`; pure, no I/O |
| `masking.py` | `PiiMasker`; pure |
| `catalog.py` | `MartCatalogLoader` and the `MartCatalog` model built from `manifest.json` and `catalog.json`; pure parsing plus file reads |
| `database.py` | `AgentDatabase`: the pool, read-only transactions, timeout, row cap, error mapping |
| `examples.py`, `examples.yaml` | The few-shot index and its data |
| `settings.py` | `McpSettings`, the only place that reads configuration |

Tests are in `agent/mcp-server/tests/`, one file per module. The ones marked `warehouse` need the built prod warehouse; the guard, masking, example and catalog tests do not.

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
- Run every query through `AgentDatabase.fetch`, `scalar` or `explain`. They give you the read-only transaction, the 5 s timeout, the row cap and the error mapping. Never open another connection.
- Raise `WarehouseToolError` for a failure the model can act on, with a message that says what to do. Anything else is a crash that the model sees only as `Error executing tool <name>`.
- If the tool can run model-written SQL, send it through `self._guard(...)` first, and mask the result with `PiiMasker`.

**2. The registration** (`server.py`, in `build_server`). The handler delegates to the `WarehouseTools` method inside `_tool_errors`, which turns a `WarehouseToolError` into a tool error. The description is what the model reads when it chooses a tool, so say when to call it and what it returns:

```python
    @server.tool(description="The number of rows in one mart. Use it to size a table before selecting from it.")
    def count_rows(table: TableName) -> RowCount:
        with _tool_errors("count_rows"):
            return tools.count_rows(table)
```

Import `RowCount` at the top of `server.py` with the other models. Keep the registered tools to the ones a model needs: every tool's description is paid for on each request.

**3. The tests.**

- `tests/test_tools.py` (marked `warehouse`): call the method against the real warehouse, including the unhappy path (an unknown table must raise `WarehouseToolError` that lists the valid tables).
- `tests/test_server.py`: add the name to `TOOL_NAMES`, so `test_lists_the_eight_tools` covers it (rename it to the new count), and add an assertion to the transport tests. The `mcp_client` fixture runs each test over **stdio** (a subprocess), **HTTP** (a subprocess on a free port) and the **compose `mcp` container** (skipped unless `SHERPA_TEST_MCP_CONTAINER=1`), so one test proves identical behaviour on all of them.

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

The guard rejects every function that is not on the list, so the common case is a query that fails with `function xyz is not allowed`. Before you allow it, ask whether the model can do without; every addition widens what a hostile or confused query can reach.

**What to check first.** The function must be harmless in a read-only role: no side effects, no file or network access, no catalog or session introspection (`pg_*`, `current_setting`, `dblink*`, `lo_*`, `query_to_xml` stay out), no unbounded output (`generate_series`), and no way to run a nested query from a string.

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

**2. The tests** (`tests/test_sql_guard.py`). Every addition needs a test, because the list is the guard's safety boundary and nothing else checks it:

- Add a call to the `test_accepts_every_allowed_function` parameter list, written the way a model would write it (`"translate(b, 'x', 'y')"`). The test selects it from `marts.fct_orders` and expects no errors.
- If the function has a form that can be abused (a subquery as an argument, a schema-qualified spelling, a table-valued form in `FROM`), add the hostile form to the `test_rejects_hostile_inputs` list with the error fragment you expect.

```bash
uv run pytest agent/mcp-server/tests/test_sql_guard.py -q
```

```
...........                                                              [100%]
227 passed in 0.92s
```

**3. Prove the test can fail (a manual mutation check).** The guard tests are only worth as much as their ability to fail when the guard is wrong, so mutate the line you added: remove the name from `ALLOWED_FUNCTIONS` and confirm that a test fails. Do the same for a function that must stay rejected: allow it temporarily and confirm a hostile-input test fails. For example, removing `initcap`:

```
FAILED agent/mcp-server/tests/test_sql_guard.py::TestSqlGuard::test_accepts_every_allowed_function[initcap(b)]
1 failed, 226 passed in 1.30s
```

Restore the line afterwards (`git diff` must show only your intended change). A mutation that no test notices means the test is missing.

**4. The docs.** The list of allowed functions is `ALLOWED_FUNCTIONS` itself; the agent spec §4.2 names the categories. Update the spec only when a new category appears.

## 3. Add or change a few-shot example

`get_examples` ranks the entries of `agent/mcp-server/src/sherpa_mcp/examples.yaml` by word overlap with the question and returns the top `k`. The file is a list of `question`, `sql` and `notes`:

```yaml
- question: What is our net revenue each month?
  sql: |
    select
        date_trunc('month', order_date)::date as month,
        sum(net_revenue) as net_revenue
    from marts.fct_orders
    where not is_cancelled
    group by 1
    order by 1
  notes: Net revenue is summed over non-cancelled orders; refunds are already attributed to the order date.
```

**1. Write the example.** The SQL must follow the same rules the model has to follow: `marts.<mart>` tables, allowed functions, named columns, metrics as `get_metric_definitions` defines them. Use the shape of a pattern (aggregation by period, top-N, ratio metric, funnel flag) rather than a question a user is likely to ask verbatim. The notes say what is easy to get wrong.

**2. Keep it disjoint from the gold set.** The evaluation's gold set (agent spec §7) is the yardstick for the agent. An example that paraphrases a gold question lets the agent copy the answer and inflates its score. The rule: no example question may be a paraphrase of a gold question, and the reviewer checks it by listing the closest example to each gold case by word overlap (Jaccard, the same measure `get_examples` ranks by). When you change an example, re-check it against the gold questions in `agent/evals/` once they exist, and make the wording or the pattern different if any gold question is close.

**3. Run the tests.** `test_examples_yaml_passes_the_guard` in `tests/test_examples.py` runs every example through `SqlGuard` and requires non-empty notes, and it asserts the number of examples (`15`); change that number when you add or remove one.

```bash
uv run pytest agent/mcp-server/tests/test_examples.py -q
```

Then run the new SQL against the warehouse with `run_sql` (or `psql`) to be sure it returns what the notes claim. The container serves the file baked into its image, so rebuild it: `docker compose up -d --build --wait --no-deps mcp`.

## 4. When the data platform adds marts

Nothing in the server's code changes. The marts come from the dbt artifacts: a model in the `marts` schema is a mart (from data-platform-4, only those with `access: public`), its domain, grain, primary key, PII flags, synonyms, accepted values and metric references come from its YAML, and the guard's list of visible tables is built from the same catalog.

What to do after the data platform builds a new or changed mart:

1. Run `make build-prod` and `make docs-prod`, so `manifest.json` and `catalog.json` in `data-platform/dbt/target-prod/` are current. The server reloads when either file's modification time changes, so a running server (including the container, which mounts the folder read-only) picks the change up on the next call, with no restart. If the new mart has a PII column, mark it `meta.pii: true` in the dbt YAML first: the masking and `search_values` follow that flag.
2. Update the `MARTS` lists in the tests:
   - `data-platform/tests/dbt/test_agent_access.py` has the data platform's list of marts, which `tests/test_tools.py` reads to check that `list_tables` returns exactly those marts;
   - `agent/mcp-server/tests/test_examples.py` has its own `MARTS` set, which the example test compares to the catalog when `catalog.json` exists.
3. Remove from `DOES_NOT_COVER` in `tools.py` the entries the new marts now cover (the web sessions, marketing and leads entries leave when the data platform loads them), and update the test in `tests/test_tools.py` that checks the scope.
4. Run the whole suite:

```bash
make test
uv run --env-file .env pytest -m warehouse
```

Add a few-shot example for the new mart's typical question if its pattern is new (section 3).
