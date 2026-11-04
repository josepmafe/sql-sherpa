# agent-design-2 — Gold Set and Evals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An execution-accuracy eval harness (`sherpa_evals`: gold-set checks, result comparison, a runner and its report) and tranche 1 of the gold set (40 accepted cases over the frozen `sherpa_eval` database), proven end to end by an oracle that scores 100%.

**Architecture:**
- **One new package.** `agent/evals/src/sherpa_evals/`, with the gold set in `agent/evals/cases.yaml`.
- **Pure core.** The comparison (`compare.py`) and the gold set's static checks (`cases.py`) are pure. The database access (`warehouse.py`) and the runner (`run.py`) do the I/O.
- **Its own interface to a variant.** An `Answerer` turns a question into an `Attempt`, so the evals depend on no agent type. The `oracle` answers with the gold SQL; agent-design-3 adds the `AgentService` answerer.
- **Reuses `sherpa_mcp`:** its `SqlGuard`, `MartCatalogLoader` (over `target-eval`) and `ExampleIndex` (the few-shot disjointness check).

**Tech Stack:** Python 3.12, Pydantic, pydantic-settings, psycopg 3 (async), PyYAML, sqlglot 30.24.0 (pinned, through `sherpa_mcp`), Typer; pytest.

**Spec:** `docs/superpowers/specs/2026-10-22-sql-sherpa-agent-design.md`:
- §4.3 (few-shot disjointness)
- §7.0 (the frozen eval dataset)
- §7.1 (gold set, schema, authoring workflow)
- §7.2 (runner, comparison rules, records, report, cost)
- §7.3 (budgets)
- §8 (agent-design-2 acceptance)
- §9 (testing)

Also data platform spec §3 (the `eval` target and `make eval-db`), §6.5 and §6.6 (marts and metric definitions) and §11.

**This plan's commit changes the specs:**
- **Data platform §4.4 and §5.4:** R8 is relaxed to the rate the generator reaches (about 0.7% of orders), so `sherpa_eval` is not rebuilt.
- **Agent §7.1:**
  - the case schema gains `allow_empty` and `reconcile_sql`
  - `--check-gold` checks every case: no clock, no hash literal, `ORDER BY` with `ordered` or `LIMIT`, `tables` equal to what the gold SQL reads, `reconcile_sql` under the comparison rules, and the closest few-shot example
- **Agent §7.2:** the runner talks to a variant through an `Answerer`; a model missing from `prices.yaml` makes the cost unknown.

**Owner decisions (2026-11-03):**
- **R8:** relax the spec.
- **Unanswerable cases:** the spec's mix of three, each a different refusal (inventory or reviews, an email domain, a write request). The surname and first-name questions go to tranche 2 or later.
- **Runner interface:** a small eval-side protocol.
- **Reviewers:** Codex and Gemini, run by the owner with `REVIEW_PROMPT.md`.

**`CLAUDE.md` is binding.**

**Base:** `main` at `c0fbd2d` (#22). The MCP test audit (#18) and the PII masked views (#20) are in, so the guard the gold SQL must pass is final.

**Model selection:** every task names its implementer and reviewer models. Dispatch exactly those. Fix rounds 4–5 go one tier up. The final whole-branch review is Task 8's panel.

## Global Constraints

- **Frozen data (agent spec §7.0):**
  - everything runs on `sherpa_eval`, never `sherpa`
  - `as_of_date` is 2025-12-31; the orders run from 2025-01-01 to 2025-12-31
  - nothing here rebuilds it; `make eval-db` only with the owner's OK, because a rebuild bumps `dataset_version`
- **Tranche 1 reads only the data-platform-2 marts:** `dim_customers`, `dim_products`, `dim_promotions`, `dim_date`, `fct_orders`, `fct_order_items`, `fct_order_discounts`, `fct_returns`, `fct_refunds`. It reads no `fct_sessions`, campaign, ad-spend or lead mart (tranche 2).
- **Category counts (agent spec §7.1, tranche 1):** `values` 8, `metrics` 8, `grain` 7, `time` 6, `complex` 5, `unanswerable` 3, `clarification` 3 = **40**.
- **Comparison rules (agent spec §7.2):**
  - numbers rounded to 2 decimals; `NULL = NULL`; text exact; dates and timestamps as ISO strings
  - equal row counts
  - column names and order ignored, extra agent columns allowed
  - whole-row match under some candidate mapping, as a multiset or, when `ordered`, as a list
- **Execution for comparison:**
  - the runner's own connection to `sherpa_eval` as `sherpa_agent`, with `statement_timeout = 30s` and **no row cap**
  - `reconcile_sql` runs as `sherpa_admin`, read-only
- **Line budget (agent spec §7.2):** `run.py` + `compare.py` + `report.py` under about 400 lines. No eval framework dependency.
- **Package rules (`CLAUDE.md`):**
  - frozen Pydantic models across modules
  - `EvalSettings` (pydantic-settings, `frozen=True`, prefix `SHERPA_`) is the only reader of the environment
  - module constants for every limit; `StrEnum`/`Literal` for closed sets
  - async code makes no blocking call (ruff `ASYNC`)
  - stdlib logging; `print` only as CLI output (Typer)
- **Packaging:** a new dependency group `evals` (`typer`; it reuses `sherpa_mcp`'s groups), added to `default-groups`. `agent/evals/src/sherpa_evals` joins the wheel's packages, `testpaths` and mypy's `files`.
- **Tests:**
  - the comparison and the static checks are pure unit tests in the default suite
  - anything that reads `sherpa_eval` is marked `warehouse` (it needs `make eval-db`), so CI runs the rest
- **Every commit** leaves `make lint` and `make test` green. Commit as `Josep Martinez <57620224+josepmafe@users.noreply.github.com>`.

## Review Focus

1. **Gold SQL that reads the clock or has no fixed order.** A `current_date` passes today and changes tomorrow; a `limit 10` without a total `ORDER BY` picks different tied rows on another plan. Expected: `--check-gold` refuses a clock function and a `LIMIT` without `ORDER BY`. The drafting rules (Task 5) require ties broken by a key, and `REVIEW_PROMPT.md` asks the reviewers to check it. Tests: `TestStaticChecks` (Task 1).
2. **Numbers that are equal but typed differently.** `avg()` gives `numeric` in one query and `float8` in another; `1` against `1.00`; `-0.00`; a value at the rounding edge (`0.005`); `timestamptz` against its ISO text; `NULL` against `0`. Expected: equal values match, and `NULL` never matches `0`. Tests: `TestNormalize` (Task 2).
3. **A wrong pairing that per-column matching would accept** (agent spec §8): two columns whose multisets match while the rows pair the wrong keys with the wrong values. Expected: fail. Test: `test_swapped_values_across_keys_fail` (Task 2).
4. **An agent query that fails, times out or returns a huge result.** Expected: that case fails with the error in its diff, every other case still runs, and the report counts it. The 30 s timeout bounds a huge result, since there is no row cap. Test: `test_a_failing_case_does_not_stop_the_run` (Task 4).
5. **A model missing from `prices.yaml`.** Expected: that attempt's cost is unknown, and the report says "cost unknown (no price for <model>)", never €0. Test: `test_unknown_model_makes_cost_unknown` (Task 4).

---

## Environment (before Task 1)

The worktree has none of the gitignored files (`CLAUDE.md`, Environment notes):
1. Copy `.env` from the main checkout.
2. Run `UV_LINK_MODE=copy uv sync`.
3. Check that `sherpa_eval` exists and `data-platform/dbt/target-eval/manifest.json` is present in this worktree. If not, ask the owner before `make eval-db`.

The eval database was rebuilt on 2026-11-01 (#21), and it is not rebuilt here: R8 is relaxed, not changed.

## File Structure

```
agent/evals/
  cases.yaml            # gold set: gold_version 1, dataset_version 1, tranche 1 (40)
  prices.yaml           # EUR per million input/output tokens per model ID
  REVIEW_PROMPT.md      # the fixed prompt for the cross-model review
  runs/                 # <timestamp>-<variant>/{cases.jsonl (gitignored), report.md (committed)}
  src/sherpa_evals/
    __init__.py
    settings.py         # EvalSettings
    cases.py            # GoldCase, GoldSet, Review; static checks (pure)
    compare.py          # normalize, compare (pure)
    warehouse.py        # EvalDatabase: execute as sherpa_agent / sherpa_admin on sherpa_eval
    answerers.py        # Answerer, Attempt, AttemptStats, ModelUsage, OracleAnswerer, ANSWERERS
    report.py           # CaseRecord, render_report (pure), previous-run lookup
    run.py              # Typer CLI: --check-gold and the eval run
  tests/                # mirrors the package
pyproject.toml, .gitignore
```

---

### Task 1: The package, the gold-set schema and its static checks

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:**
- Create: `agent/evals/src/sherpa_evals/{__init__,settings,cases}.py`, `agent/evals/tests/test_cases.py`, `agent/evals/tests/conftest.py`
- Modify: `pyproject.toml` (group `evals` with `typer`, `default-groups`, wheel `packages`, `testpaths`, mypy `files`)

**Interfaces — Produces:**
- **Types:**
  - `Category = Literal["values", "metrics", "grain", "time", "complex", "unanswerable", "clarification"]`
  - `ExpectedOutcome = Literal["answered", "unanswerable", "needs_clarification"]`
  - `CaseStatus = Literal["draft", "reviewed", "accepted"]`
- **`Review`** (frozen): `reviewer: Literal["codex", "gemini", "opus", "human"]` (`opus` = the spec's fresh stand-in session), `verdict: Literal["accept", "fix", "reject"]`, `notes: str`.
- **`GoldCase`** (frozen): `id`, `category`, `question`, `tranche: Literal[1, 2]`, `expected_outcome`, `gold_sql: str | None`, `ordered: bool = False`, `allow_empty: bool = False`, `reconcile_sql: str | None = None`, `rationale: str`, `tables: tuple[str, ...] = ()`, `status: CaseStatus = "draft"`, `reviews: tuple[Review, ...] = ()`.
  - The `id` matches `^<category>-\d{3}$`.
  - An `answered` case has `gold_sql` and a non-empty `tables`; any other case has neither, nor a `reconcile_sql`.
- **`GoldSet`** (frozen): `gold_version: int`, `dataset_version: int`, `cases: tuple[GoldCase, ...]`.
  - `GoldSet.load(path: Path) -> GoldSet` rejects duplicate ids and duplicate questions.
  - `.accepted() -> tuple[GoldCase, ...]`.
- **`static_problems(case: GoldCase, visible_marts: frozenset[str]) -> list[str]`**, pure, on `gold_sql` (and on `reconcile_sql` for the hash rule):
  - `SqlGuard(visible_marts).check(sql)` is refused: the guard's message
  - a call of `now`, `current_date` or `current_timestamp` (sqlglot nodes, not text search): `"reads the clock (<name>); write dates, or use (select max(order_date) from marts.fct_orders)"`
  - a 32-hex literal (`[0-9a-fA-F]{32}` in a string literal): `"holds a 32-hex literal; hashes change with every eval-db rebuild"`
  - `ordered` or a top-level `LIMIT` without a top-level `ORDER BY`: `"ordered or limited without ORDER BY"`
  - the set of `marts` tables the SQL reads differs from `set(tables)`: `"tables lists X, the SQL reads Y"`
- **`EvalSettings`** (frozen, prefix `SHERPA_`, `.env`):
  - `db_host`, `db_port`
  - `eval_db_name = "sherpa_eval"`
  - `agent_user`, `agent_password: SecretStr`, `admin_user`, `admin_password: SecretStr`
  - `eval_target_path: Path = Path("data-platform/dbt/target-eval")`
  - `default_variant = "v0_baseline"`
- **Constants:** `CASES_PATH`, `PRICES_PATH` and `RUNS_DIR` (`agent/evals/…`, from the package location).

- [ ] **Step 1: Tests first** (`test_cases.py`; cases built by a `make_case(id, **overrides)` builder in `conftest.py`, asserting only what each test passes):
  - **`TestGoldCase`:**
    - an answered case without `gold_sql` is refused
    - an unanswerable case with `gold_sql` is refused
    - `id` `metrics-003` with category `grain` is refused
    - `metrics-3` is refused
  - **`TestGoldSet`:** duplicate ids are refused; duplicate questions are refused; `accepted()` keeps only accepted cases; it loads a two-case YAML from `tmp_path`.
  - **`TestStaticChecks`**, parametrized, each over a visible-mart set of the nine tranche-1 marts:
    - a clean query: `[]`
    - `current_date` and `now()`: the clock problem
    - `where email = 'd41d8cd98f00b204e9800998ecf8427e'`: the hash problem
    - `limit 10` without `order by`: the order problem
    - `ordered: true` without `order by`: the order problem
    - `tables: [fct_orders]` while the SQL also joins `dim_customers`: the tables problem
    - an unqualified `from fct_orders`: the guard's message
    - a hash literal in `reconcile_sql` only: the hash problem

  Run them. Expected: FAIL (no module).
- [ ] **Step 2: Implement.** `uv sync`, then `make test` and `make lint`. Commit `feat: sherpa_evals package, gold-set schema and static checks`.

---

### Task 2: The comparison

**Model:** implementer `sonnet` · reviewer **`opus`** (the scoring every later milestone trusts)

**Files:** `agent/evals/src/sherpa_evals/compare.py`, `agent/evals/tests/test_compare.py`.

**Interfaces — Produces:**
- `Result` (frozen): `columns: tuple[str, ...]`, `rows: tuple[tuple[Any, ...], ...]`.
- `normalize(value: Any) -> Hashable`:
  - `None` stays `None`
  - `bool` stays itself
  - `int`, `float` and `Decimal` become `Decimal(str(value)).quantize(Decimal("0.01"), ROUND_HALF_UP)`, with `-0.00` as `0.00`
  - `date` gives `isoformat()`; `datetime` gives its UTC `isoformat()` (a naive one as is)
  - anything else gives `str(value)`
- `Comparison` (frozen): `passed: bool`, `diff: str | None` (one line).
- `compare(gold: Result, agent: Result, *, ordered: bool) -> Comparison`.
- `MAX_MAPPINGS: Final = 10_000`: candidate mappings tried at most, after which the case fails with `"too many candidate column mappings"`.

**Algorithm:**
1. Normalise every cell of both results.
2. Unequal row counts fail: `"gold has N rows, the agent M"`.
3. For each gold column, the candidates are the agent columns whose normalised values form the same multiset (`Counter`). A gold column with no candidate fails: `"no agent column matches gold column <name>"`.
4. Try every assignment of distinct agent columns to gold columns (backtracking, in a stable order).
5. Under an assignment, project the agent rows onto the assigned columns and compare them with the gold rows as a `Counter` of tuples, or, when `ordered`, as a list.
6. The first match passes. If none matches: `"no column mapping matches the gold rows as whole rows"`.

A gold result with zero rows matches an agent result with zero rows when the agent has at least as many columns.

- [ ] **Step 1: Tests first.**
  - **`TestNormalize`:**
    - `Decimal("1")`, `1`, `1.0` and `Decimal("1.004")` normalise equal
    - `Decimal("1.005")` gives `1.01` (half up)
    - `-0.0001` gives `0.00`
    - `float(1/3)` and `Decimal("0.3333")` match
    - `None` never equals `Decimal("0")`
    - `date(2025, 12, 31)` gives `"2025-12-31"`
    - a `datetime` in UTC+01:00 gives its UTC ISO string
    - `"ABC"` and `"abc"` differ
  - **`TestCompare`:**
    - column permutation passes
    - an extra agent column passes
    - a missing column fails with its diff
    - unordered rows in another order pass
    - `ordered=True` in another order fails
    - duplicate rows count (two `(a, 1)` against one fails)
    - different row counts fail
    - NULLs in the same places pass
    - **`test_swapped_values_across_keys_fail`:** gold `[("DE", 10), ("FR", 20)]`, agent `[("DE", 20), ("FR", 10)]`: each column's multiset matches, the rows don't, so it fails
    - two agent columns that both fit one gold column: the one that makes whole rows match is found
    - 0 against 0 rows passes
    - the bound: with `MAX_MAPPINGS` patched to 2, three interchangeable columns whose only matching assignment is the last one fail with `"too many candidate column mappings"`, and with the default limit the same input passes

  Run them. Expected: FAIL.
- [ ] **Step 2: Implement.** `make test`, `make lint`. Commit `feat: result comparison with whole-row column mapping`.

---

### Task 3: The eval database and `--check-gold`

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `agent/evals/src/sherpa_evals/warehouse.py`, `agent/evals/src/sherpa_evals/run.py` (the `--check-gold` part), `agent/evals/tests/test_warehouse.py`, `agent/evals/tests/test_check_gold.py`.

**Interfaces:**
- **Consumes:** `GoldSet`, `static_problems` (Task 1), `Result`, `compare` (Task 2), `sherpa_mcp.catalog.MartCatalogLoader(settings.eval_target_path).load().visible_marts()`, `sherpa_mcp.examples.ExampleIndex.from_yaml(sherpa_mcp.server.EXAMPLES_PATH).top(question, 1)`.
- **Produces:**
  - `EvalSqlError(Exception)`, which carries Postgres's first error line
  - `EvalDatabase(settings)`:
    - `async execute(sql) -> Result`: as `sherpa_agent` on `eval_db_name`, session options `default_transaction_read_only=on` and `statement_timeout=30s` (`EVAL_STATEMENT_TIMEOUT = "30s"`), all rows
    - `async execute_admin(sql) -> Result`: as `sherpa_admin`, read-only transaction, same timeout
    - one new async connection per call, closed after it
  - `CheckRow` (frozen): `case_id`, `problems: tuple[str, ...]`, `rows: int | None`, `closest_example: str`, `closest_score: float`
  - `async check_gold(gold: GoldSet, db: EvalDatabase, visible_marts: frozenset[str], examples: ExampleIndex) -> tuple[CheckRow, ...]`
  - CLI: `uv run python -m sherpa_evals.run --check-gold [--case <id>]`

**What `check_gold` checks** (every case, whatever its status; agent spec §7.1 step 2):
- **Static:** `static_problems`.
- **Answered cases:**
  - run the gold SQL twice: the two results must `compare` equal under the case's `ordered` (`"not deterministic: …"`)
  - 0 rows without `allow_empty` is `"returns no rows"`
  - with `reconcile_sql`, its admin result must `compare` equal to the gold result (`"reconcile_sql disagrees: <diff>"`)
  - an `EvalSqlError` is a problem, not a crash
- **Every case:** the closest few-shot example and its score.

The CLI prints a markdown table (case, problems, rows, closest example, score) and exits 1 if any case has a problem.

- [ ] **Step 1: Tests first.**
  - **`test_check_gold.py`** (default suite): a fake `EvalDatabase` (`create_autospec`, with results per SQL), a two-mart visible set, and a two-example `ExampleIndex`:
    - a clean answered case has no problems and its row count
    - a result that differs between the two runs is "not deterministic"
    - 0 rows is a problem, and with `allow_empty` it isn't
    - a disagreeing `reconcile_sql` is a problem
    - an `EvalSqlError` from the gold SQL is a problem, and the next case is still checked
    - an unanswerable case runs no SQL and gets its closest example
    - the exit code is 1 with a problem and 0 without
  - **`test_warehouse.py`** (`warehouse`, on `sherpa_eval`):
    - `execute("select current_setting('statement_timeout')")` gives `30s`
    - `execute` returns more than 101 rows (`select order_id from marts.fct_orders`): no cap
    - `create table marts.eval_probe (i int)` is refused by the read-only session (`cannot execute CREATE TABLE in a read-only transaction`)
    - `execute_admin` reads `raw_shop.orders`
    - a query against `marts.dim_customers_unmasked` raises `EvalSqlError` (permission denied for `sherpa_agent`)

  Run them. Expected: FAIL.
- [ ] **Step 2: Implement.** `make test`, `make lint`, `uv run --env-file .env pytest -m warehouse agent/evals`. Commit `feat: the eval database and --check-gold`.

---

### Task 4: The runner, the oracle, the records and the report

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `agent/evals/src/sherpa_evals/{answerers,report,run}.py`, `agent/evals/prices.yaml`, `.gitignore`, `agent/evals/tests/test_answerers.py`, `agent/evals/tests/test_run.py`, `agent/evals/tests/test_report.py`, and the negative control: `agent/evals/tests/mutants.py` (test helpers, not shipped) and `agent/evals/tests/test_negative_control.py`.

**Interfaces:**
- **Consumes:** Tasks 1–3.
- **Produces:**
  - `ModelUsage` (frozen): `model: str`, `input_tokens: int`, `output_tokens: int`.
  - `AttemptStats` (frozen): `latency_ms: int`, `usage: tuple[ModelUsage, ...]`, `tool_calls: int`, `sql_attempts: int`, `trace_id: str | None`.
  - `Attempt` (frozen): `outcome: Literal["answered", "unanswerable", "needs_clarification", "failed"]`, `sql: str | None` (the final executed SQL when answered), `reason: str | None`, `stats: AttemptStats`.
  - `Answerer` (Protocol): `name: str`; `async attempt(question: str) -> Attempt`. It gets only the question, never the case.
  - `OracleAnswerer(gold: GoldSet)`: `name = "oracle"`; answers a question with its case's `expected_outcome` and `gold_sql`, and zero stats.
  - `ANSWERERS: dict[str, Callable[[GoldSet, EvalSettings], Answerer]] = {"oracle": …}`. agent-design-3 adds the variants here.
  - `Prices.load(PRICES_PATH)`, `.cost_eur(usage) -> Decimal | None`: `None` when a model has no price.
  - `CaseRecord` (frozen): `case_id`, `category`, `repeat: int`, `expected_outcome`, `outcome`, `passed: bool`, `diff: str | None`, `agent_sql: str | None`, `latency_ms`, `cost_eur: Decimal | None`, `unpriced_models: tuple[str, ...]`, `tool_calls`, `sql_attempts`, `schema_recall: bool | None`, `trace_id`.
  - `RunMeta` (frozen): `variant`, `gold_version`, `dataset_version`, `started_at: datetime`, `repeat`.
  - `render_report(meta: RunMeta, records: Sequence[CaseRecord], previous: Sequence[CaseRecord] | None, previous_name: str | None) -> str`, pure.
  - `previous_run(runs_dir, variant, gold_version, dataset_version) -> tuple[str, list[CaseRecord]] | None`: the newest run directory of the default variant with the same versions, read from its `cases.jsonl`.
  - `async run_cases(cases: Sequence[GoldCase], answerer: Answerer, db: EvalDatabase, *, repeat: int = 1, concurrency: int = 4) -> list[CaseRecord]`: scores exactly the cases it is given. The CLI passes `gold.accepted()`, so the negative control can pass its own.
  - CLI: `uv run python -m sherpa_evals.run --variant oracle [--category …] [--case …] [--repeat 1] [--concurrency 4]`.

**How the runner scores a case** (agent spec §7.2):
- **Answered cases:** they need `attempt.outcome == "answered"`. Then the agent's SQL and the gold SQL run through `EvalDatabase.execute` (the gold result cached per run) and must `compare` equal.
  - an `EvalSqlError` from the agent's SQL fails the case: `"agent SQL failed: …"`
  - any other outcome fails it: `"expected answered, got <outcome>: <reason>"`
- **Other cases:** they pass when the outcome types match.
- **Schema recall:** for answered cases only, whether the gold `tables` are a subset of the `marts` tables the agent's SQL reads (sqlglot).
- **Running:** accepted cases only, `--repeat` times, with at most `--concurrency` attempts in flight (`asyncio.Semaphore`).
- **Output:** records go to `RUNS_DIR/<YYYYMMDDTHHMMSSZ>-<variant>/cases.jsonl`, and `report.md` sits beside it. `.gitignore` ignores `agent/evals/runs/*/cases.jsonl`.

**The report:**
- **Header:** variant, `gold_version`, `dataset_version`, date, repeats, case count.
- **Execution accuracy (EX):** overall and per category, over answered cases, and per repeat when `--repeat > 1`.
- **Outcome-type accuracy:** over all cases.
- **Latency:** p50 and p95, next to the §7.3 targets (20 s and 60 s).
- **Cost:** mean and total, or "cost unknown (no price for …)".
- **Effort:** mean tool calls and SQL attempts.
- **Schema recall.**
- **Failing cases:** id, outcome, diff, trace id.
- **Diff against the previous run of `default_variant`:** EX change, and the newly passing and failing ids; otherwise "No earlier run of <variant> with gold_version G and dataset_version D."

**`prices.yaml`:** `models: {<model id>: {input_eur_per_mtok, output_eur_per_mtok}}` for `eu.anthropic.claude-sonnet-5-5`, `eu.anthropic.claude-haiku-5-5` and `eu.anthropic.claude-opus-5-5`. Values come from the AWS Bedrock on-demand price list for `eu-central-1` on the day, converted at the ECB reference rate of that day, with both recorded in a header comment (`# prices as of YYYY-MM-DD, USD→EUR <rate>`). The owner checks them.

- [ ] **Step 1: Tests first.**
  - **`test_answerers.py`:** the oracle answers each answered case with its gold SQL, and an unanswerable one with `unanswerable` and no SQL; an unknown question raises `KeyError`.
  - **`test_run.py`** (fake `EvalDatabase`, a hand-written `Answerer` stub):
    - a matching answered case passes
    - a mismatch fails with the comparison's diff
    - `needs_clarification` against an expected `answered` fails with its diff
    - an expected `unanswerable` met with `unanswerable` passes
    - **`test_a_failing_case_does_not_stop_the_run`:** an `EvalSqlError` on one case's agent SQL fails it, and the next case is still scored
    - only accepted cases run
    - `--repeat 2` gives two records per case with `repeat` 1 and 2
    - concurrency: an `AsyncMock` answerer that counts how many attempts are in flight at once never exceeds the limit
    - schema recall is `False` when the agent's SQL leaves out a gold table
  - **`test_report.py`:**
    - EX and outcome accuracy from fixed records (3 answered of which 2 pass: `66.7%`)
    - p50 and p95 from fixed latencies
    - **`test_unknown_model_makes_cost_unknown`**
    - the previous-run diff lists the newly passing and failing ids
    - no previous run: the literal sentence
    - `previous_run` picks the newest directory with the same versions and ignores one with another `gold_version`

  Run them. Expected: FAIL.
- [ ] **Step 2: The negative control, tests first.** The oracle only proves that a right answer passes; a harness that passed everything would score 100% too. These answerers are wrong on purpose and run through `run_cases` on the real `sherpa_eval`.
  - **`mutants.py`.** Each mutation is a sqlglot transform, never a string replace:
    - `drop_cancelled_filter(sql) -> str`: removes `not <x>.is_cancelled`, `not is_cancelled` and `is_cancelled = false` from `AND` chains in `WHERE` and `JOIN … ON`
    - `gross_for_net(sql) -> str`: turns each column reference `net_revenue` into `gross_revenue`, keeping the output aliases
    - `flip_order(sql) -> str`: inverts every top-level `ORDER BY` direction
    - `MutantAnswerer(gold, mutation)`: the oracle, answering with `mutation(gold_sql)`
    - `AlwaysAnsweredAnswerer`: answers every question `answered` with `select 1 as x`
    - `identical(gold: Result, other: Result, *, ordered: bool) -> bool`: a deliberately naive check, the same columns in the same order and the same normalised rows (as a `Counter`, or a list when `ordered`)
  - **The fixture cases** (in the test module, `status: accepted`, gold SQL written by hand on `sherpa_eval`). Each mutation has a case it must change:
    - `metrics-901`: net revenue by sales channel in March 2025. `gross_for_net` changes it. `drop_cancelled_filter` does not, because a cancelled order's `net_revenue` is 0 (data platform spec §6.5), which shows the control tells "different SQL" apart from "different answer".
    - `metrics-902`: orders and gross revenue per month in Q1 2025, non-cancelled. `drop_cancelled_filter` changes it.
    - `complex-901`: the top 5 products by 2025 net revenue, ties broken by `product_id`, `ordered: true`. `flip_order` changes it.
    - `unanswerable-901`: "How many units of each product are in stock?"
  - **`test_negative_control.py`** (`warehouse`):
    - `test_a_mutant_passes_only_when_its_result_is_identical`: for every fixture case and mutation that changes the SQL, the record's `passed` equals `identical(gold result, mutant result)`, so the comparison neither accepts a different result nor rejects an identical one
    - `test_each_mutation_fails_at_least_one_case`: each of the three mutations fails a fixture case, so each one bites
    - `test_always_answered_fails_every_case`: every fixture case fails, the unanswerable one included
    - **The same three over the accepted gold set**, parametrized from `cases.yaml`. Until Task 6 accepts cases, each skips itself with "no accepted gold cases yet"; from then on they run on the real set.
- [ ] **Step 3: Implement.** Check the line budget: `wc -l run.py compare.py report.py` is under about 400; the test helpers don't count. `make test`, `make lint`, `uv run --env-file .env pytest -m warehouse agent/evals`. Commit `feat: eval runner, oracle answerer, report and the negative control`.

---

### Task 5: The review prompt and the tranche-1 draft

**Model:** implementer **`opus`** (agent spec §2: gold-set drafting runs on Opus 5.5) · reviewer `sonnet` (schema, counts, rules, `--check-gold`; the semantic review is Task 6)

**Files:** `agent/evals/REVIEW_PROMPT.md`, `agent/evals/cases.yaml`, `agent/evals/tests/test_gold_set.py`.

- [ ] **Step 1: `REVIEW_PROMPT.md`** (fixed; Task 6's reviewers get exactly this, with `cases.yaml` and data platform spec §6):
  - **The role:** a senior analytics engineer reviewing text-to-SQL gold cases for a frozen e-commerce warehouse.
  - **The checklist, per case:**
    - Does the question have one reading, or is it meant not to (`needs_clarification`)?
    - Does the gold SQL answer exactly it under §6.6? Cancelled orders excluded where the metric says so; refunds by order date or by refund date as asked; test accounts are already out of the marts.
    - No fan-out across grains.
    - Date bounds inclusive or exclusive as the question says.
    - Ties broken by a key under `LIMIT`.
    - `ordered` right.
    - `tables` complete.
    - `expected_outcome` right.
    - Not a paraphrase of a few-shot example.
  - **The output:** a YAML list `- {id, verdict: accept | fix | reject, notes}`, with notes that say what to change.
- [ ] **Step 2: Test first** (`test_gold_set.py`, default suite, on the real `cases.yaml`):
  - it loads with `gold_version: 1` and `dataset_version: 1`
  - all 40 cases are `tranche: 1`, with exactly the Global Constraints' category counts
  - every `answered` case reads only the nine tranche-1 marts (`static_problems` over that set is `[]`)
  - no question repeats a few-shot example's question word for word

  Run it. Expected: FAIL (no file).
- [ ] **Step 3: Draft the 40 cases** (`status: draft`, `reviews: []`), from data platform spec §6 and the `sherpa_eval` marts. Explore the data read-only as `sherpa_agent`, through `EvalDatabase.execute`. Rules:
  - **Dates:**
    - absolute dates in 2025, except in `time`
    - `time` questions are relative and their gold SQL is relative to `(select max(order_date) from marts.fct_orders)` (2025-12-31)
    - their wording must differ from the few-shot examples' ("net revenue last month", "orders in the last 30 days", "Black Friday week compared with the week before")
  - **`metrics`:**
    - net against gross, refunds by order date against refund date, AOV, margin
    - at least 3 cases ask for an unqualified "revenue" and expect `answered` with `net_revenue` (agent spec §5.3 defaults)
  - **`values`:** noisy or partial product, brand and category names, and country names that map to codes. Each value comes from the data (`search_values`-style grounding), never invented.
  - **`grain`:**
    - fan-out traps: payments, orders with several discounts (`fct_order_discounts`), order against line grain
    - one answered case over the hashed email: `count(distinct email)` over `dim_customers`. Join customers by keys, never by a hash.
  - **`complex`:** CTEs, window functions, a cohort.
  - **`unanswerable`, 3 cases:**
    - data not in the warehouse (inventory or product reviews)
    - a value the marts hold only as a hash ("How many of our customers use a gmail address?")
    - a write request
  - **`clarification`, 3 cases:** genuinely ambiguous, with no documented default ("Who are our best customers?").
  - **Answered cases, every one:**
    - `rationale` cites the spec section
    - `tables` lists exactly what the gold SQL reads
    - a top-N breaks ties by a key
    - `ordered: true` only when the question asks for an order
  - **Reconciliation:** at least 3 answered cases carry a `reconcile_sql` over `raw_shop` (for example a month's net revenue, a count of non-test orders, the stacked-discount orders).
- [ ] **Step 4: Check.** `uv run --env-file .env python -m sherpa_evals.run --check-gold` exits 0. Rewrite any case whose closest few-shot score is above 0.5, or say in its rationale why it isn't a paraphrase. Run `make test`.
- [ ] **Step 5: Commit** `feat: gold set tranche 1 (40 draft cases) and the review prompt`. Paste the `--check-gold` table into the task report.

---

### Task 6: Cross-model review, acceptance and the oracle run

**Model:** the owner runs the reviews. Recording verdicts and fixing cases: implementer `sonnet`, reviewer `opus` (every fixed case's semantics).

- [ ] **Step 1: The owner runs Codex and Gemini** with `REVIEW_PROMPT.md` over `cases.yaml` and hands back both YAML verdict lists. If one is unavailable, a fresh Opus subagent with no drafting context takes its place, recorded as `reviewer: opus` (agent spec §7.1 step 3).
- [ ] **Step 2: Record and resolve.**
  - **Record:** each verdict goes into its case's `reviews`, and every reviewed case becomes `status: reviewed`.
  - **Resolve:** cases with a `fix` or `reject`, or where the reviewers disagree, go to the owner in one table (case, reviewer, notes, proposed change). The owner decides each one.
  - **Fix:** a fixed case goes through `--check-gold` again.
  - **Accept:** every case the owner accepts becomes `status: accepted`.
- [ ] **Step 3: The acceptance run.**
  - `--check-gold` exits 0 (agent spec §8).
  - `uv run --env-file .env python -m sherpa_evals.run --variant oracle` gives EX 100% on the 34 answered cases and outcome accuracy 100% on all 40.
  - The negative control over the accepted gold set passes (`uv run --env-file .env pytest -m warehouse agent/evals/tests/test_negative_control.py`), with no skip left. How many accepted cases each mutation changed and failed goes in the PR description.
  - Commit `agent/evals/runs/<timestamp>-oracle/report.md` and `cases.yaml`: `feat: gold set tranche 1 accepted; the oracle scores 100%`.

---

### Task 7: Documentation

**Model:** docs implementer `sonnet` · reviewer `haiku`

- [ ] **Step 1: `CLAUDE.md`.**
  - agent-design-2 joins the done plans; the current-plan line becomes agent-design-3
  - the layout gains `agent/evals/`
  - the pure/I/O list: `sherpa_evals`' `compare` and `cases` are pure; `warehouse` and `run` do the I/O
  - the Tests section, one line each:
    - gold SQL passes `--check-gold` before it is reviewed
    - a gold case is changed only through the review workflow, never to fit an agent's answer
    - the eval tests that read `sherpa_eval` are `warehouse`
  - the Commands section gains the two eval commands as documented plain commands, not Make targets
- [ ] **Step 2: `README.md`.** The status line; the Stack row "Observability & evals": the runner is no longer *planned*.
- [ ] **Step 3: Guides** (short; one symptom → fix table each):
  - **New `basic/running-the-evals.md`:**
    - `--check-gold` and an eval run, with their flags
    - reading a report (EX, outcome accuracy, schema recall, the diff against the default)
    - where the records live
    - symptoms: `--check-gold` "not deterministic"; "cost unknown"; the eval database missing (`make eval-db`, with the owner's OK)
  - **New `advanced/extending-the-gold-set.md`** (how tranche 2 is added in agent-design-4):
    - the case schema and the drafting rules of Task 5
    - the review workflow and `REVIEW_PROMPT.md`
    - `gold_version` and `dataset_version`, and when each changes
    - adding an `Answerer`
  - **README:** link both guides.
- [ ] **Step 4:** `make lint`. Commit `docs: running the evals and extending the gold set`.

---

### Task 8: Panel review

**Model:** every reviewer and the devil's advocate on `opus`, dispatched by the controller (`CLAUDE.md`). Fixes go through the usual loop.

- [ ] **Step 1: Reviewers**, in parallel. Each gets `git diff main...HEAD`, this plan, its focus and the spec sections named:
  - **AI engineer** (agent spec §7, §8):
    - whether the harness measures what the technique milestones need
    - whether the `Answerer` protocol is enough for agent-design-3's `AgentService` (tokens per model, the final executed SQL, outcomes)
    - schema recall
    - the report's comparison against the default run
  - **Analytics domain expert** (data platform spec §6.5, §6.6): every accepted case's question against its gold SQL, the category coverage, the `unanswerable` and `clarification` cases, and the reconciliations.
  - **Software engineer** (`CLAUDE.md`):
    - the comparison's algorithm and its bound
    - async I/O
    - the package layout and the line budget
    - the default-suite and `warehouse` split, and CI
  - **Data engineer** (data platform spec §3, §7.0 of the agent spec): `sherpa_eval` as a frozen dataset, its grants as used here (the agent role and the admin role, read-only), the R8 spec change, and `dataset_version`.

  No security reviewer: no role, grant or network surface changes. The admin login is used read-only, only for `reconcile_sql`.
- [ ] **Step 2: Devil's advocate,** with the diff and every finding. It asks which wrong answer would still pass the comparison, beyond the three mutations the negative control proves (rounding, extra columns that hide a wrong grain, an empty result against `allow_empty`), and which gold cases a reviewer would read differently from the drafter.
- [ ] **Step 3: Triage and fix:** `make lint`, `make test`, the `warehouse` eval tests, `--check-gold`, and the oracle run again. CI green. The triage table and the `--check-gold` table (with the closest examples) go in the PR description.

---

## Notes for later plans (owner notes, not tasks)

- **agent-design-3:**
  - adds the `AgentService` answerer to `ANSWERERS`, mapping `PlanOutcome`/`AnswerOutcome` and `RunStats` to `Attempt`, with tokens per model
  - creates `FINDINGS.md` with the first baseline
  - the fabricated-number guard must not read the digit runs of a hash as numbers (agent spec §5.2)
- **agent-design-4 (tranche 2):** the owner's PR #20 questions ("customers whose last name starts with A?", "most common first name?") are candidates for its `unanswerable` slot or for later techniques. Its lead questions count by `lead_id` and join customers through `converted_customer_id`, never through a hash (data-platform-4 notes).
