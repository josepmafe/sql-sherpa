# Extending the gold set

How to add or change gold cases (tranche 2 arrives in agent-design-4), update the model prices, extend the negative control and add an `Answerer`. Using the runner: [Running the evals](../basic/running-the-evals.md). Rules: [agent spec](../../superpowers/specs/2026-10-22-sql-sherpa-agent-design.md) §7.

## Module map

Code is in `agent/evals/src/sherpa_evals/`; tests in `agent/evals/tests/`, roughly one file per module.

| File | Holds |
|---|---|
| `cases.py` | `GoldCase`, `GoldSet`, `Review`, and `static_problems` (the checks that need no database); pure except `GoldSet.load`, which reads `cases.yaml` |
| `compare.py` | `compare`, the comparison rules (§7.2) and `MAX_MAPPINGS`; pure |
| `warehouse.py` | `EvalDatabase` (one read-only connection per query), `check_gold` |
| `answerers.py` | `Answerer`, `Attempt`, `AttemptStats`, `ModelUsage`, the `oracle`, the `ANSWERERS` registry |
| `report.py` | `Prices` (`Prices.load` reads `prices.yaml`), `CaseRecord`, `render_report` (pure), `previous_run`, `write_run` |
| `run.py` | The CLI and `run_cases`, which scores the cases it is given |
| `agent/evals/` | `cases.yaml`, `REVIEW_PROMPT.md`, `prices.yaml`, `runs/` |

`make test` runs the default tests. The ones that read `sherpa_eval` are `warehouse`: `uv run --env-file .env pytest agent/evals/tests -m warehouse` (needs `make eval-db`).

## Writing a case

The schema is in spec §7.1 (`GoldCase` validates it: an answered case needs `gold_sql` and `tables`, any other outcome has neither, and `id` is `<category>-<nnn>`). Rules the gold SQL follows, most of which `--check-gold` enforces:

- **One statement**, accepted by the MCP server's `sql_guard`, reading only visible marts. `tables` equals exactly the marts it reads.
- **Text filters are case-insensitive**: `lower(p.brand) = 'nordvik'`, in `gold_sql` and `reconcile_sql` alike.
- **No clock and no 32-hex literal.** Relative questions are written against `(select max(order_date) from marts.fct_orders)`; hashes change with every eval database rebuild.
- **Ties under `LIMIT` are broken by a key**, and `ordered: true` only when the question asks for an order, with a total `ORDER BY`.
- **Select only the columns the question asks for.** Every gold column must be matched by an agent column, while extra agent columns are fine, so an unrequested gold column fails a correct agent.
- **Write for the comparison**: numbers round to 2 decimals half-up, so express rates and shares as percentages; a `date` and a midnight timestamp do not match, so cast month and week buckets with `::date`; a boolean matches a 0/1 column. A gold result with many equal columns can exceed `MAX_MAPPINGS` and fail every attempt with `too many candidate column mappings`.
- **Defaults are not clarified**: a question using "revenue" or "customers" is `answered` under the defaults in spec §5.3.
- **Does not reuse a few-shot question's words** (`test_gold_set.py` checks the word sets); the closest-example score from `--check-gold` flags paraphrases.
- **A `reconcile_sql`** rebuilds the answer from `raw_shop` (test accounts removed there, since only the marts exclude them). It runs as `sherpa_admin` in a read-only transaction and must give the same result. At least three answered cases carry one.

Check as you go: `uv run --env-file .env python -m sherpa_evals.run --check-gold --case <id>`.

## Adding tranche 2

Tranche 1 has one ordered case in 34, so tranche 2 adds more ordered, tie-heavy and rounding-sensitive cases. It adds cases where dropping the cancelled-orders filter changes the answer beyond net-revenue questions, and relative-time cases whose `as_of_date` is not a quarter or year end. The agent's prompt should match names case-insensitively (`lower(column) = 'literal'`), which is why gold text filters are lowercase.

## The review workflow

A case moves `draft` → `reviewed` → `accepted`, and a draft has no reviews yet.

1. **Draft** the case with `status: draft` and a rationale that cites the spec section it relies on.
2. **`--check-gold` is clean** for it before any review.
3. **Review.** The owner runs Codex and Gemini with `agent/evals/REVIEW_PROMPT.md`, attaching `cases.yaml`, `agent/mcp-server/src/sherpa_mcp/examples.yaml` and data platform spec §6. Each returns a YAML list of `accept | fix | reject` with notes. When a reviewer is unavailable, a fresh Opus session with no access to the drafting conversation stands in and is recorded as `reviewer: opus`. `human` is the owner.
4. **Record** each verdict in the case's `reviews` (`reviewer`, `verdict`, `notes`). Apply a `fix` to the case, re-run `--check-gold`, and have it reviewed again.
5. **Accept.** The owner resolves disagreements; the case becomes `accepted` once it has two distinct reviewers. Only accepted cases count in reports.

A gold case changes only through this workflow, never to fit an agent's answer: when a variant fails a case, the variant is wrong until a review says the case is.

## `gold_version` and `dataset_version`

Both are top-level keys in `cases.yaml`, recorded in each run's `meta.json`. A report diffs only against a run with the same pair.

| Key | Changes when | Then |
|---|---|---|
| `gold_version` | a tranche lands (1 = tranche 1, 2 = both); each tranche is frozen once accepted | Re-run the current default variant first to set the new baseline |
| `dataset_version` | `make eval-db` rebuilds the eval database (new data, new salt) | Re-run `--check-gold`, since a gold result may change, then the baseline |

`test_gold_set.py` pins the tranche-1 shape (counts per category, the marts it reads, versions of 1); update it with the tranche.

## The negative control

The oracle proves a right answer passes; `tests/mutants.py` and `tests/test_negative_control.py` prove a wrong one fails. Mutants rewrite the gold SQL with sqlglot (`drop_cancelled_filter`, `gross_for_net`, `flip_order`), and an always-`answered` answerer must fail every case. For each case a mutation changes, the case passes only when the mutant's result is identical to the gold result.

```
uv run --env-file .env pytest agent/evals/tests/test_negative_control.py -m warehouse
```

To add a mutation: write `def my_mutation(sql: str) -> str` in `mutants.py` (parse with `sqlglot.parse_one(sql, read=DIALECT)`, change the tree, return `tree.sql(dialect=DIALECT)`), add it to `MUTATIONS`, and run the file above. Each mutation must fail at least one case, so if none of the fixture or accepted cases is affected, add a fixture case in `FIXTURE_CASES` that it does affect.

## Prices

`agent/evals/prices.yaml` lists EUR per million input and output tokens per Bedrock model ID. A model that is not in it makes the cost of a run unknown ("cost unknown (no price for <model>)"), never 0. The values in the file are placeholders until the owner checks them against the Bedrock on-demand price list for `eu-central-1` and the ECB reference rate of the day. To update them, change the numbers, then the header comment with the date, the rate and the source figures in USD. To add a model, add an entry under `models:` with the ID exactly as the variant reports it in `ModelUsage.model`.

## Adding an `Answerer`

The runner knows variants only through the protocol in `answerers.py`:

```python
class Answerer(Protocol):
    name: str

    async def attempt(self, question: str) -> Attempt: ...
```

An `Attempt` holds the `outcome` (`answered`, `unanswerable`, `needs_clarification` or `failed`), the final executed `sql` when answered, a `reason`, and `AttemptStats`: `latency_ms`, `usage` (a `ModelUsage` per model with its token counts, which `prices.yaml` turns into cost), `tool_calls`, `sql_attempts` and `trace_id`. A variant with no model reports `NO_STATS`.

Files, in order:

1. **The answerer class** (agent-design-3 adds the one that wraps `AgentService`): its constructor takes what it needs, and `attempt` returns `outcome="failed"` with a reason instead of raising on a model failure.
2. **`ANSWERERS` in `answerers.py`**: add `"name": lambda gold, settings: MyAnswerer(...)`. The key is what `--variant` accepts.
3. **A test** in `test_answerers.py`, with no network. `run_cases` is tested in `test_run.py` on fake answerers.
4. **Concurrency**: the runner holds at most one connection per case in flight, during scoring (at most 16, because `sherpa_agent` allows 20). A variant that opens its own database connections, such as an MCP server per case, must count them in its share of that limit, or lower `MAX_CONCURRENCY` in `run.py`.

## When something goes wrong

| Symptom | Fix |
|---|---|
| `tables lists {...}, the SQL reads {...}` | Make `tables` equal the marts the SQL reads |
| `ordered or limited without ORDER BY` | Add a total `ORDER BY`, ending in a key |
| `reads the clock (...)` | Write the date, or use `(select max(order_date) from marts.fct_orders)` |
| `holds a 32-hex literal` | Join or filter by `customer_id`; email hashes change with each rebuild |
| `reconcile_sql disagrees: ...` | The marts and the raw schemas give different answers: find which is wrong (test accounts, cancelled orders, refund dates) before touching either query |
| The oracle scores below 100% | A comparison rule or the case is wrong; read the diff in the failing table, then run `uv run --env-file .env pytest agent/evals/tests` |
| A mutation test finds no case it bites | The mutation changes nothing on those cases; add a fixture case it affects |
| A run directory appears in `git status` | Commit `report.md` and `meta.json` of runs worth keeping; delete the rest |
