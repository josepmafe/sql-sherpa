# MCP Server Test Audit — Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gaps the MCP server test audit found. `sql_guard` and the masking are the agent's safety boundary and the web app imports them, so the guard's tests must fail when its rules or its limits change in either direction. The tool and database layers must pin the spec's values, not their shape. When it is done, each of the audit's 32 surviving mutants fails the default suite.

**Architecture:** this is tests, plus two small changes the tests prove necessary (the guard's statement names, and one sentence in the agent spec). One fake database and one fixture catalog in `conftest.py` let the tool logic be tested in the default suite, without the warehouse. The guard's tests become tables of exact errors. Each defence-in-depth layer is tested with the other layers turned off.

**Tech Stack:** pytest, psycopg, `unittest.mock.create_autospec`, sqlglot 30.24.0 (unchanged).

**Spec:** `docs/superpowers/specs/2026-10-22-sql-sherpa-agent-design.md`: §3 (the tool server), §4.1 (tools), §4.2 (SQL safety: the guard, the read-only path, masking). This plan's commit adds one sentence to §4.2 item 6 (a NULL is not masked). **`CLAUDE.md` is binding.**

**Source:** the read-only audit of 2026-10-30: 181 test functions (791 collected items), 83% coverage on the default suite and 96% with the warehouse tests, and 94 one-line mutants of which 56 were caught and 32 are real gaps.

**Base:** `main`. This plan touches `agent/mcp-server/` only, so it runs alongside ui-3, the UI test plan and the data-platform test plan. Land it before the agent-design-2 plan is implemented: the eval harness will rely on the guard.

**Model selection:** every task names its implementer and reviewer models. Dispatch exactly those. Fix rounds 4–5 go one tier up. The final whole-branch review is Task 6's panel.

## Global Constraints

- **Tests first for production changes** (`CLAUDE.md` regression rule). The production code this plan may change is `sql_guard._statement_kind` (Task 1) and nothing else; a test proving another bug stops the work and goes to the owner.
- **Spec values as literals.** A test compares with the spec's list or number written in the test, never with the module constant it checks (Review Focus 1).
- **Defence in depth is tested layer by layer:** patch the session options to test `SET LOCAL`; skip the guard to test the read-only session.
- **The shared warehouse is read-only.** Warehouse tests only read the marts as `sherpa_agent`. Nothing writes to `sherpa` or `sherpa_eval`.
- **Timing tests keep a 2× margin** on a slow machine, or patch the clock.
- **A worktree has none of the gitignored files** (`CLAUDE.md`, Environment notes): copy `.env`, run `uv sync`, `make build-prod` once.
- **Every commit** leaves `make lint` and `make test` green.

## Review Focus

1. **Tests that restate the code:** an assertion against `ALLOWED_FUNCTIONS`, `MAX_CELL_CHARS` or `DOES_NOT_COVER` proves nothing. Every limit and list is asserted as the spec's literal. Test: `TestAllowedFunctions::test_the_allow_list_is_exactly_the_specs` (Task 1).
2. **Widening the guard silently:** adding one function to the allow-list must fail a test, and so must each rejected family the spec names (`pg_*`, `dblink*`, `lo_*`, `set_config`, sequence functions). Tests: the allow-list set and the family table (Task 1).
3. **A timeout that is only enforced by another layer:** removing `SET LOCAL statement_timeout`, or changing the fetch deadline from 5 s to 6 s, must fail. Tests: `test_set_local_timeout_is_enforced_on_its_own` (Task 3).
4. **Tests that pass for the wrong reason:** a refusal test must show which layer refused. The "write attempt" tests today are stopped by `DECLARE CURSOR`'s grammar and never reach the read-only session. Tests: `test_a_write_is_refused_by_the_read_only_session` (Task 3).
5. **A fixture that cannot tell the rule from the default:** a primary-key order test needs a table stored in another order; a cap test needs a column on each side of the cap. Tests in Task 2.

---

## File Structure

```
agent/mcp-server/tests/
  conftest.py              # + fixture_target, fake_database, fake_tools; REPO_ROOT and mart list in one place
  fixtures/manifest.json   # dbt 1.12 shape (meta and config.meta) and a second mart with PII
  test_sql_guard.py        # exact-set allow-list, family rows, one table of exact errors
  test_tools.py            # unit layer on the fixture catalog and the fake database
  test_database.py, test_catalog.py, test_masking.py, test_server.py, test_examples.py
agent/mcp-server/src/sherpa_mcp/sql_guard.py   # _statement_kind names only
```

---

### Task 1: The guard

**Model:** implementer `sonnet` · reviewer **`opus`** (this is the security boundary; the reviewer re-derives the spec's rejected families)

**Files:** `tests/test_sql_guard.py`, `src/sherpa_mcp/sql_guard.py` (`_statement_kind`).

- [ ] **Step 1: Tests first.**
  - **`TestAllowedFunctions::test_the_allow_list_is_exactly_the_specs`:** `ALLOWED_FUNCTIONS` equals a literal set written in the test, copied from spec §4.2. (Today the only use is `ALLOWED_FUNCTIONS | {"md5"}`, which proves widening works, not that nothing else is allowed.)
  - **Family rejections,** parametrized with at least three members each, from spec §4.2, plus the ones the audit named: `pg_*` (`pg_terminate_backend`, `pg_read_file`, `pg_sleep`), `dblink*` (`dblink_connect`, `dblink`, `dblink_exec`), `lo_*` (`lo_create`, `lo_import`, `lo_export`), `set_config`, sequence functions (`setval`, `nextval`, `currval`), and `inet_server_addr`. Each asserts the `[guard:function]` code and the function's name in the message.
  - **Function names in any case:** `SIMILARITY(…)` and `MAKE_DATE(…)` are accepted; the allow-list lookup is lower-cased (mutant at `sql_guard.py:528`).
  - **One table of exact errors:** `(sql, rule_code, expected_message_fragment)` for every hostile and hint case. It replaces the three overlapping groups at `:341-479`, `:550-627` and `:637-741`. Every row asserts the `[guard:<rule>]` code, not only a fragment.
  - **The `array_to_json` hint** is asserted (mutant at `sql_guard.py:69`).
  - **Statement names** (the model sees these): `begin` → "BEGIN", `truncate t` → "TRUNCATE", `listen x` → "LISTEN", `discard all` → "DISCARD", `table pg_roles` → "TABLE". Today they read "TRANSACTION", "TRUNCATETABLE" and "ALIAS", sqlglot's node names.
  - **Shrink the grid:** the 7 functions × 25 templates (175 cases) at `:766-806` becomes 2 functions (`row_to_json`, `num_nulls`) × the 25 templates, because the guard never looks at the name there.
  Run them. Expected: the statement-name tests FAIL (today the names are sqlglot's); the allow-list and family tests pass already, and must fail when a function is added or a family member is allowed (check with a throwaway edit).
- [ ] **Step 2: Implement** `_statement_kind`: a small mapping from sqlglot's key to the SQL keyword for the awkward ones (`transaction` → the first word of the statement, `truncatetable` → `TRUNCATE`, an `alias`-parsed command → its first word). Keep `Command` handling as is. No other change.
- [ ] **Step 3:** `make test`, `make lint`. Check the UI suite still passes (`uv run --env-file .env pytest ui/backend -q`), since it imports the guard. Commit `test: pin the guard's allow-list, rejected families and statement names`.

---

### Task 2: The tool layer in the default suite

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `tests/conftest.py`, `tests/fixtures/manifest.json`, `tests/test_tools.py`, `tests/test_examples.py`.

**Interfaces — Produces** (`conftest.py`):
- **`fixture_target(tmp_path)`:** a dbt target folder holding the copies of `tests/fixtures/*.json` (moved from `test_catalog.py`).
- **`fake_database`:** `create_autospec(AgentDatabase, instance=True)`, with `fetch`, `fetch_for_model` and `scalar` returning canned `QueryResult`s set per test. It replaces `NoDatabase`, `CountingDatabase`, `EmptyDatabase` and the `# type: ignore` comments.
- **`fake_tools(fixture_target, fake_database)`:** a `WarehouseTools` over both.
- **`REPO_ROOT`, `EXAMPLES_PATH` and the mart list** defined once; `test_tools.py` and `test_examples.py` stop redefining them (the mart list has three copies today).

- [ ] **Step 1: The fixture.** Refresh `fixtures/manifest.json` to the dbt 1.12 shape (columns carry both `meta` and `config.meta`), add a second mart with a PII column, and make one mart's stored order differ from its primary-key order. Check `make build-prod`'s real manifest for the shape, and note the date in a comment.
- [ ] **Step 2: Tests first,** all in the default suite (each failing under its audit mutant):
  - **`list_tables`:** every field of the mapping (`domain`, `grain`, `description`, synonyms), not only the names (mutant `domain=grain`).
  - **`describe_table`:** synonyms present; samples ordered by primary key (the query text contains the `order by <pk>` of a mart stored in another order); `distinct_values`:
    - a PII text column gives `None` and issues no query
    - exactly 30 stored values are listed and 31 give `None`
    - NULLs never appear in the list
    - the non-PII filter is exercised by a PII column with 2 values
  - **`search_values`:** the query sets both similarity thresholds (assert `set_similarity_threshold`, and the 0.2/0.6 values the spec names) and orders by similarity descending, then value (`order by 2 desc, 1`).
  - **`validate_sql`:** `select c from marts.dim_customers c` is refused with `[guard:whole_row]`, and `explain` is never called when the guard refuses (`fake_database.explain.assert_not_called()`).
  - **`get_context`:**
    - `does_not_cover` and `notes` are compared with the spec §4.1 lists as literals
    - the two-year boundary: a history of 729 days adds the short-history note and 730 days does not (mutant: 365)
    - `;;` and a trailing `--` comment through `run_sql` (mutant `while` → `if`)
    - the 22008 hint
    - the pool timeout answering `[unreachable]`
    - upper-case allowed functions
  - Move the two `warehouse`-marked classes at `test_tools.py:383-423` that need only a catalog into the default suite on `fixture_target`.
- [ ] **Step 3: Warehouse twins** (read-only, kept small): `lead_source` has no NULL and exactly 4 values in `distinct_values`; two matches of `search_values` come back in descending similarity, and one with similarity between 0.2 and 0.3 is found.
- [ ] **Step 4:** `make test`, `uv run --env-file .env pytest -m warehouse agent/mcp-server`. Commit `test: tool logic on a fixture catalog and a fake database`.

---

### Task 3: The database layers

**Model:** implementer `sonnet` · reviewer **`opus`** (layered timeouts and the extended-protocol refusal)

**Files:** `tests/test_database.py`.

- [ ] **Step 1: Tests first,** each failing under its audit mutant:
  - **`test_set_local_timeout_is_enforced_on_its_own`:** patch `AGENT_SESSION_OPTIONS` to a 60 s timeout, then assert `current_setting('statement_timeout')` is `5s` inside `fetch` and inside `explain` (mutants: `SET LOCAL` removed, set to 6 s).
  - **The fetch deadline** (`FETCH_DEADLINE_SECONDS`): a query slower than 5 s stops between 5 and 7 s; the existing `< 10 s` bound is replaced.
  - **`test_explain_refuses_several_statements`:** `explain("select 1; select 2")` raises `[sql] cannot insert multiple commands into a prepared statement` (mutant: `prepare=True` dropped; the current `analyze select 1` is a syntax error in either protocol).
  - **The write tests,** rewritten (Review Focus 4): `select … for update` is refused by the read-only session with the exact text `cannot execute SELECT FOR UPDATE in a read-only transaction`, so the refusal comes from the session, not from `DECLARE CURSOR`'s grammar; a second test runs a read-write session with the guard skipped and gets `permission denied` on a mart, which is the privilege layer (the role has no UPDATE and no CREATE in schema `marts`).
  - **The result budget:** a result of 50,000 characters is kept in full and one of 50,001 is truncated (mutant 50,100).
  - **The fetch-slot wait:** `acquire` is called with `timeout=10.0`; the pool timeout is `10.0` (mutants 11 s, 30 s).
  - **The large cell:** use a 2,000,000-character `lpad` instead of 100 MB (it timed out at 5.7–6.6 s in 4 of 5 runs here): still above the 1,000,000-character large-row threshold and far past the 10,000-character cell cap.
- [ ] **Step 2:** delete the shallow tests at `test_database.py:173` (a dataclass is frozen) and `:414` (psycopg binds parameters). `make test`. Commit `test: database layers tested one at a time, with the spec's limits as literals`.

---

### Task 4: Masking and the catalog

**Model:** implementer `haiku` · reviewer `sonnet`

**Files:** `tests/test_masking.py`, `tests/test_catalog.py`.

- [ ] **Step 1: Tests first.**
  - **Masking:** assert the literal `'***'` instead of the `MASK` constant (the default suite never pins it today). A NULL stays NULL (spec §4.2 item 6, now written).
  - **Catalog:**
    - `test_pii_columns_are_the_specs` (warehouse): `pii_columns()` equals `{email, first_name, last_name}`, lower-cased
    - PII in a second mart is found (mutant: only the first mart is read)
    - `attached_node` filters the tests of a mart (mutant: ignored)
    - a failed reload is retried on the next call: a second `load()` while the file is still broken logs a second warning (mutant: the stamp updated before the read)
  - Reuse `_bump_mtime` where `test_catalog.py:107-131` and `:162` inline it; `make_orders_public` becomes a module-level helper.
- [ ] **Step 2:** `make test`. Commit `test: masking literal and NULL, built PII set, catalog retry`.

---

### Task 5: Tidy-up and the server

**Model:** implementer `haiku` · reviewer `sonnet`

**Files:** `tests/test_server.py`, `tests/test_examples.py`, `tests/test_tools.py`.

- [ ] **Step 1:**
  - Unit-test `_transport_security` directly: the allowed hosts include `localhost`, `127.0.0.1` and `[::1]` (and the origins the spec names), and reject another; the 15 s startup wait gets a bound (it took 25 s once).
  - `test_examples.py`: read the columns from the YAML always, with a warehouse twin that compares with the build; drop the `== 19` change detector at `:161`.
  - Delete the shallow constant-substring tests at `test_tools.py:43-62`, folded into the spec-list test of Task 2.
- [ ] **Step 2:** `make test`. Commit `test: transport security unit test, examples read from the YAML`.

---

### Task 6: Documentation and panel review

**Model:** docs implementer `sonnet` · reviewer `haiku`. The panel runs as `CLAUDE.md` describes, every reviewer on `opus`, by the controller.

- [ ] **Step 1: Docs.**
  - **`CLAUDE.md`, Tests:** add the audit's MCP rules, one line each:
    - every limit the spec names gets a boundary test on both sides, asserting the spec's literal value, never the module constant
    - a security allow-list is pinned by an exact-set test, and each rejected family the spec names has at least three members in the rejection table
    - a defence-in-depth layer is tested with the other layers turned off
    - a refusal test asserts which layer refused
    - tool logic that needs no rows is unit-tested in the default suite on the fixture catalog and an autospec'd `AgentDatabase`
    - data-dependent assertions use data that tells the cases apart
    - never compare a tool's output with the constant it returns
    - timing tests keep a 2× margin or patch the clock
    - when dbt is bumped, refresh the fixture manifest from a real build
  - **`advanced/extending-the-mcp-server.md`:** "Allow a new SQL function" names the exact-set test it must update, and the family rows.
  - Commit `docs: MCP testing rules from the test audit`.
- [ ] **Step 2: Panel review.** Every reviewer gets `git diff main...HEAD`, its focus, this plan and agent spec §4:
  - **Security engineer:** the guard's allow-list and families against the spec and what the role can reach (`setval`, `pg_terminate_backend`: reachable with `sherpa_agent`'s privileges or not), the statement-name change, the masking tests.
  - **Software engineer:** the fake database and fixture catalog, the unit layer's honesty (does it test our logic or the fake?), speed and flakiness.
  - **AI engineer:** the model-facing texts the tests now pin: guard messages, hints and statement names, from the point of view of an agent that has to recover from them.
  - **Devil's advocate:** which changes to the guard would still pass; whether a new test restates the code.
- [ ] **Step 3:** Triage, fix, re-verify (`make lint`, `make test`, the warehouse suite once). The triage table and the table of the audit's mutants, all now killed, go in the PR description.
