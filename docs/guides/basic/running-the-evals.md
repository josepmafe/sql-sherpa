# Running the evals

`sherpa_evals` scores a text-to-SQL variant on the gold set: 40 accepted questions (`agent/evals/cases.yaml`), each with the expected outcome and, for an answered case, the gold SQL whose result the variant's result must match. Rules: [agent spec](../../superpowers/specs/2026-10-22-sql-sherpa-agent-design.md) §7; to add or change cases or variants, [Extending the gold set](../advanced/extending-the-gold-set.md).

## What you need

- Postgres up and the frozen `sherpa_eval` database built ([Building the warehouse](building-the-warehouse.md#the-frozen-eval-database)). Every query runs there, never on the main database. `make eval-db` rebuilds it from scratch, after which the owner bumps `dataset_version` in `cases.yaml` by hand, so run it only when the owner agrees.
- `.env` filled in ([README Quickstart](../../../README.md)) and `uv sync` done.

Both commands are plain commands, run from the repository root.

## Checking the gold set

```
uv run --env-file .env python -m sherpa_evals.run --check-gold [--case values-001]
```

For every case, whatever its status, it prints one table row with the problems found (`-` when none), the gold row count, and the closest few-shot example with its score. It runs each gold query twice, as `sherpa_agent`, and exits with code 1 when any case has a problem. The checks are listed in the spec §7.1 step 2.

```
| case | problems | rows | closest example | score |
| values-001 | - | 1 | How many new customers placed their first order in each month? | 0.06 |
```

## Scoring a variant

```
uv run --env-file .env python -m sherpa_evals.run --variant oracle [--category metrics] [--case metrics-003] [--repeat 1] [--concurrency 4]
```

| Flag | Meaning |
|---|---|
| `--variant` | The answerer to score. `oracle` returns each case's gold SQL and expected outcome, needs no model and must score 100%; it proves the comparison rules. |
| `--category`, `--case` | Run only that category or case id. Only `accepted` cases are ever run. |
| `--repeat` | Attempts per case (default 1). |
| `--concurrency` | Cases in flight, each holding at most one connection at a time, during scoring (default 4, at most 16: the `sherpa_agent` role allows 20 connections and the MCP server shares them). |

Each query runs as a single statement in a real read-only transaction with a 30 s statement timeout and no row cap. A case that fails never stops the run.

## Reading a report

The runner prints `report.md` and the directory it wrote. The parts that matter:

```
Execution accuracy (EX): 100.0% (34/34)

| category | EX |
| --- | --- |
| complex | 100.0% (5/5) |
| grain | 100.0% (7/7) |
| ... one row per category |

Outcome-type accuracy: 100.0% (40/40)
Latency: p50 0.0 s (target 20 s), p95 0.0 s (target 60 s)
Cost: mean EUR 0.0000 per case, total EUR 0.0000
Schema recall: 100.0% (34/34)
No failing cases.
No earlier run of v0_baseline with gold_version 1 and dataset_version 1.
```

- **EX**: the share of answered cases whose result matches the gold result, overall and per category.
- **Outcome-type accuracy**: over all cases, whether the variant answered, declined (`unanswerable`) or asked (`needs_clarification`) as expected. Unanswerable and clarification cases pass on the outcome alone.
- **Schema recall**: of the answered cases, the share whose SQL reads every mart in the case's `tables`.
- **Failing cases**: a table of case id, outcome, a one-line diff (such as `gold has 12 rows, the agent 13`) and the trace id.
- **The diff against the default**: the last section compares with the newest earlier run of the default variant (`v0_baseline`) with the same `gold_version` and `dataset_version`: the EX change in points, and the cases newly passing and newly failing. With `--category` or `--case` the change is computed over the case ids both runs share. It needs that earlier run's `meta.json` and its local `cases.jsonl` (gitignored), so on a fresh clone the report prints `No earlier run of ...`.

## Where the records live

`agent/evals/runs/<timestamp>-<variant>/` (a `.n` suffix when two runs start in the same second):

- `report.md` and `meta.json` are committed. `meta.json` holds the variant, `gold_version`, `dataset_version`, start time and repeats, which is how the next run finds the run to diff against (the diff also reads that run's local `cases.jsonl`).
- `cases.jsonl` (one record per attempt) is gitignored.

A run you only made to try something is yours to delete before committing.

## When something goes wrong

| Symptom | Fix |
|---|---|
| `--check-gold` reports `not deterministic: ...` | The gold query returns different results on two runs: add a total `ORDER BY` tie-break to a `LIMIT`, drop a clock or random function, or fix a join that fans out |
| `--check-gold` reports `connection failed: ... database "sherpa_eval" does not exist` | Build the eval database: `make eval-db`, with the owner's OK (it drops and rebuilds `sherpa_eval`) |
| `--check-gold` reports `returns no rows` | Fix the query, or set `allow_empty: true` when an empty result is the right answer and the rationale says why |
| Report shows `Cost: cost unknown (no price for <model>)` | `agent/evals/prices.yaml` has no entry for that model ID; add it ([Extending the gold set](../advanced/extending-the-gold-set.md#prices)). The oracle uses no model, so its cost is 0 |
| `Invalid value for '--concurrency': 17 is not in the range 1<=x<=16` | Lower it; the connection limit is shared with the MCP server |
| `Invalid value for --variant` | Pass one of the names it lists (`oracle` until the agent variants exist) |
| `no accepted gold case matches` | The `--category` or `--case` names no accepted case; `--check-gold --case <id>` also covers draft cases |
| `too many candidate column mappings` in a failing diff | The gold result has many equal columns, so the comparison gave up; change the gold query ([Extending the gold set](../advanced/extending-the-gold-set.md#writing-a-case)) |
