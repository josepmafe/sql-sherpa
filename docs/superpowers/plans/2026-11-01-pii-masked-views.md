# PII Masked Views Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Postgres hides personal values, not the code that reads results. `dim_customers` becomes a view that hashes `email` (the names stay in `dim_customers_unmasked` only) with a salt generated inside Postgres, over a table no reader can read. Then the name-based masking, the data-error rewrite and the guard rules that only existed to protect PII or to cap padding are deleted. The change has to remove more code than it adds, with no loss of safety or function.

**Architecture:** the data side comes first: the `ensure_pii_salt` hook and `ops.pii_salt`, the `mask_pii` macro, the `dim_customers_unmasked` table and `dim_customers` view, per-relation grants, a structural singular test and a privilege pytest. The catalog then sees only the views. From that commit on the warehouse alone protects PII. Every later task only deletes code:
1. result masking and the data-error rewrite in the MCP server and the UI;
2. the guard's column resolver and its PII, padding, parser-differential and identifier-length rules, replaced by a closed list of cast types.

The old masking and the new hashing overlap for a while and never leave a gap.

**Tech Stack:** dbt-postgres (Postgres 16), sqlglot 30.24.0 (unchanged, pinned), pytest, Vitest, React.

**Spec:** the specs on this branch already describe the end state. Read `git diff main -- docs/superpowers/specs`, then the full sections:
- **Data platform spec** `docs/superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md`: §3 (roles), §6.5 (Mart metadata, **PII masking**, **Checks**, `dim_customers`, `fct_leads`), §8 (**Added by the PII masking change**; data-platform-4), §11, §12.
- **Agent spec** `docs/superpowers/specs/2026-10-22-sql-sherpa-agent-design.md`: §3 (Metadata freshness, **Which marts are visible**), §4.1 (`get_context`, `describe_table`, `run_sql`, Errors), §4.2 (items 1, 2, 6, Known limits), §8, §9, §11, §12.
- **UI spec** `docs/superpowers/specs/2026-10-24-sql-sherpa-ui-design.md`: §4 (error codes, `is_pii`), §5 (Guard, Executor, PII, Sort and filter, Data exceptions), §6 Explorer.

**`CLAUDE.md` is binding.**

**Base:** `main` (ui-3 merged), branch `pii-masked-views`. The spec edits are on the branch but not committed; Task 1 commits them with this plan. Every branch cut before this merges (`main`, the dp-4 worktree) grants `all tables in schema marts`, which includes `dim_customers_unmasked`, so run none of these there against the shared warehouse until this merges and the branch is rebased: `make build-prod`, `make eval-db`, `make advance` / `make backfill` (they run `dbt snapshot --target prod`), `dbt source freshness --target prod`, `pytest -m warehouse` (the audit test runs dbt on prod), `dbt run-operation grant_read_access --target prod`. Rebase data-platform-4 onto main first.

**Notes for the owner, not tasks:**
- **data-platform-4.** The `data-platform-4-marketing` branch (`.claude/worktrees/data-platform-4`) is not touched here. After this merges, rebase it. Its `fct_leads` then follows the pair:
  - `fct_leads_unmasked` (a table, `meta.pii` on `email`, `grants: {select: []}`) and a `fct_leads` view with `{{ mask_pii('email') }}`;
  - the two `-- depends_on:` lines in `assert_pii_marts_are_masked_views`;
  - its groups make the table `private`;
  - see guide `adding-or-changing-a-mart` §8 for the registry steps (`MARTS`/`UNMASKED` in `test_reader_access.py`, the expected set in `test_catalog.py`);
  - join leads to customers on `lead_id`, not on the hashed email.
- **agent-design-2:**
  - add an `unanswerable` gold case such as "how many customers use gmail?";
  - no gold SQL may hold a 32-character hex literal;
  - an `eval-db` rebuild makes a new salt and so new hashes.
- **agent-design-3:** the fabricated-number guard must not read the digit runs inside a hash as numbers.

**Model selection:** every task names its implementer and reviewer models; dispatch exactly those. Fix rounds 4–5 go one tier up. Task 6 is the final whole-branch review.

**Expected size** (production code):

| Area | Delete | Add |
|---|---|---|
| `sql_guard.py` | ~345 | ~30 (the cast list and its hint) |
| `masking.py` | 28 (the file) | 0 |
| Rest of `sherpa_mcp` (`tools`, `catalog`, `database`, `server`) | ~35 | ~20 |
| UI backend and frontend | ~120 | ~5 |
| dbt | ~10 | ~230 |

The dbt side breaks down as: view 30, two macros 20, `grant_read_access` +30, the table's YAML 85, the singular test 65. Tests: `test_sql_guard.py` goes from 1,534 lines to about 900, `test_masking.py` goes, and the new privilege, catalog and lineage tests add about 220.

## Global Constraints

- **The specs win.** If a step contradicts them, follow the spec and say so. The only spec edits are Task 5's, and those the panel's fixes require.
- **No reader ever sees a real value, at any commit.** Task 2 lands the views before Tasks 3–4 delete the masking. In between, the masker turns the hashes into `***`, which is redundant but safe.
- **The salt** is generated by Postgres. It appears in no SQL text, `.env`, test output, log or artifact: not under any `data-platform/dbt/target*` directory and not under `data-platform/dbt/logs/`. `ensure_pii_salt` returns plain SQL and never uses `run_query`, so `dbt compile`, `dbt docs generate` and `make lint` neither need nor write it. There is no `SHERPA_PII_SALT`.
- **No reader role and no `PUBLIC` grant** gives any privilege on an `_unmasked` table, in any schema, or on `ops.pii_salt`, after any build or `dbt run-operation grant_read_access`.
- **Fail closed:**
  - a missing view means "relation does not exist" for readers;
  - a visible mart that is still a table next to an `_unmasked` sibling (an older build) gets its privileges revoked;
  - the catalog refuses a build whose visible marts carry `meta.pii`, and drops its cached catalog when it does.
  - `grant_read_access` **never fails on a missing mart or view**, so `make build-prod` (whose `source freshness` runs the hooks first) can always repair the warehouse.
- **A visible mart is a `marts` model whose name does not end in `_unmasked`.** Do not use dbt `access:` or groups: they are a data-platform-4 feature.
- **Tests:**
  - assert the spec's literals, never the module constant;
  - privilege and leak assertions are booleans, computed so that a real value or the salt never reaches Python or a failure message (compare in SQL as admin, or `assert not leaked, "<what leaked>"`);
  - singular tests return only literal text, because `store_failures` keeps their rows;
  - warehouse tests only read.
- **Every commit:** `make lint` and `make test` green. At the end of Tasks 2–4, `uv run --env-file .env pytest -m warehouse` is green too, after `make build`, `make build-prod` and `make eval-db`.
- **Environment:**
  - On this OneDrive path, `uv run` needs `UV_LINK_MODE=copy` (README).
  - Admin commands connect to the `sherpa` database.
  - A worktree needs its own `.venv` and `.env` copy (`CLAUDE.md`).
- **Verified facts, don't re-derive them:**
  - dbt-postgres drops with `cascade`, so `dbt run --select dim_customers_unmasked` alone drops the view, while `--select dim_customers_unmasked+` or a full run recreates it.
  - `pg_default_acl` is empty.
  - `pg_stats` filters only by column privilege, not schema `USAGE`, so readers see the stats of today's `dev_marts.dim_customers` table.
  - With the `U&` check gone, `U&"x"` in a table, alias, CTE or join position is a sqlglot parse error; it only passes in expression positions.
  - No `lpad`, `rpad`, `char(n)` or `bit(n)` is used by `examples.yaml`, the dbt models or the UI's own queries.
  - sqlglot traces the view's PII columns into the salt subquery, so column lineage reads them `partial`, with the note `reads an unknown relation <db>.ops.pii_salt`. The three-part relation keeps the note from starting with a dot.

## Review Focus

1. **A path to a real value or the salt that no rule names:** `pg_stats` in any schema, `ops.pii_salt`, a whole-row or `to_jsonb(c)` reference, attribute notation, an error message, a `test_failures` table, the logs. The expectation is hashes, nothing or "permission denied". Tests: Task 2's `TestMaskedMarts`, Task 4's `test_whole_row_forms_return_only_hashes`.
2. **A partial or failed build.** The table rebuilt without its view, or main's raw table left in place, must leave readers with an error, never real values, and the next `make build-prod` repairs it. Tests: `grant_read_access`'s table-type revoke (Task 2, Step 6), the singular test's missing-view row, and the first-migration check (Task 2, Step 7).
3. **A grant regression:** the project `+grants`, a new mart, a rebuild, `run-operation`. Test: `test_no_reader_privilege_on_an_unmasked_relation_or_the_salt`, which lists `%\_unmasked` relations from `pg_class` in every schema.
4. **New code on artifacts from before this change**, which would serve the raw table. Test: `test_a_build_with_pii_in_a_visible_mart_replaces_the_cached_catalog` (Task 2).
5. **A guard deletion that widens something other than PII:** a function reached through attribute notation, a padding type, a parser differential. Tests: `TestAttributeNotationReach` and `TestCastTypes` (Task 4). Every kept hostile row of `test_sql_guard.py` passes unchanged.

---

## File Structure

```
data-platform/dbt/
  dbt_project.yml                                   # on-run-start: [ensure_extensions, ensure_pii_salt]
  macros/ensure_pii_salt.sql, macros/mask_pii.sql   # new
  macros/grant_read_access.sql                      # per relation; _unmasked revoked first; never fails on a missing mart or view
  models/marts/customers/dim_customers_unmasked.sql # git mv of today's dim_customers.sql
  models/marts/customers/dim_customers.sql          # new: the view
  models/marts/customers/_customers__models.yml     # the pair; the unit test moves to the table
  tests/singular/assert_pii_marts_are_masked_views.sql   # new
data-platform/tests/dbt/test_reader_access.py       # UNMASKED, TestMaskedMarts
agent/mcp-server/src/sherpa_mcp/
  catalog.py    # skip *_unmasked, is_pii = meta.masked, sticky refusal; visible_marts(); no mart_columns/pii_columns
  tools.py      # DOES_NOT_COVER line; no masking      server.py  # tool descriptions
  database.py   # no data_error_message                masking.py # deleted
  sql_guard.py  # SqlGuard(visible_marts, ...), CAST_TYPES; resolver and PII/pad/number/U&/identifier rules gone
agent/mcp-server/tests/  test_sql_guard.py, test_catalog.py, test_tools.py, test_database.py, test_examples.py,
                         fixtures/manifest.json; test_masking.py deleted
ui/backend/src/sherpa_ui/  readonly/{query_runner,bounded_result,sort_filter}.py, api/{errors,queries}.py,
                           lineage/lineage_service.py
ui/backend/tests/  readonly/*, api/test_queries_api.py, test_app.py, lineage/*
ui/parity/sort_filter_cases.json
ui/frontend/  src/components/{resultView.ts,ResultGrid.tsx}, src/screens/explorer/WorksheetPanel.tsx,
              src/api/schema.d.ts, tests/{ResultGrid,ExplorerResults}.test.tsx, e2e/helpers.ts
```

---

### Task 1: Spikes on a scratch database, then commit the specs and this plan

**Model:** implementer `sonnet` · reviewer **`opus`** (the grant and privilege semantics decide the design)

Run in a scratch dbt project `/tmp/sherpa-pii-spike/` against a scratch database `sherpa_pii_spike` on the compose Postgres. Use one `--target-path target-spike` for every command. Delete both at the end, and paste the outcomes into the PR description draft.

- [ ] **Step 1:** `git add docs/superpowers/specs docs/superpowers/plans/2026-11-01-pii-masked-views.md && git commit -m "docs: PII masked views (specs and plan)"`. Do not add `todos.md`.
- [ ] **Step 2: Setup.**
  - `docker compose exec -T postgres psql -U "$SHERPA_ADMIN_USER" -d sherpa -c "create database sherpa_pii_spike"`.
  - A `profiles.yml` with one target on that database (admin login from `.env`).
  - A `dbt_project.yml` with on-run-start `ensure_pii_salt`, and a `marts` folder block copied from the repo's: table, contract, `persist_docs`, the `analyze` post-hook, `+grants` to both roles.
  - The two macros exactly as Task 2 specifies them.
  - Models:
    - `x_unmasked`: a table of `id bigint` (PK), `email text`, `note text`, 3 rows, one `email` NULL; `meta.pii` on `email`; `grants: {select: []}`;
    - `x`: a view of `id, {{ mask_pii('email') }} as email, note`.
  - The singular test drafted from Task 2.
  - Run `"$REPO/.venv/Scripts/dbt.exe" --project-dir . --profiles-dir .` with the `.env` variables exported. Grant `usage on schema marts` to both roles by hand.
- [ ] **Step 3: Checks, each with its fallback.**
  1. **The salt hook:**
     - with an empty database, `dbt compile` and `dbt docs generate` succeed and leave no `ops` schema;
     - the first `dbt build` creates `ops.pii_salt` with one row of 72 characters;
     - a second build leaves it unchanged (compare `md5(salt)` as admin);
     - `drop schema ops cascade` plus a build gives a new one.
     - *Fallback:* none expected; if `compile` executes the hook, stop and ask the owner.
  2. **The grants override:**
     - `has_table_privilege` and `has_any_column_privilege` are false for both roles on `marts.x_unmasked` after two builds;
     - `has_table_privilege` is true on `marts.x`.
     - *Fallback:* if dbt appends, keep `select: []`, since `grant_read_access` revokes on prod and eval. Note it for Task 5's spec sentence, and for dev, where the pytest would then fail: stop and ask.
  3. **Contract and constraints on the view:**
     - the build fails when one view column's YAML type is changed to `integer` (revert it);
     - with no `constraints:` on the view it passes;
     - adding one only warns (remove it).
     - *Fallback:* if a view rejects the contract, stop and ask the owner.
  4. **Owner rights.** As `sherpa_agent`:
     - `select email from marts.x` returns 32-character hex values or NULL;
     - `marts.x_unmasked` and `ops.pii_salt` raise `permission denied`;
     - `pg_stats` has no row for `x_unmasked` (as admin it has one).
     - *Fallback:* none; a failure breaks the design, so stop.
  5. **Column lineage of the view.** Run `ColumnLineageBuilder` on the spike's manifest and compiled SQL (as `test_column_lineage_warehouse.py` does). `x.email` is `transformed`/`partial` with the note `reads an unknown relation sherpa_pii_spike.ops.pii_salt`.
     - *Fallback:* if the node fails as a whole, report it; Task 2 then pins whatever it reads.
  6. **`persist_docs` and the post-hook on the view:**
     - `obj_description('marts.x'::regclass)` and `col_description('marts.x'::regclass, 2)` are set;
     - `analyze` on the view gives at most a WARNING.
     - *Fallback:* the folder post-hook becomes `"{{ 'analyze ' ~ this if model.config.materialized == 'table' else '' }}"`.
  7. **Artifact grep and lint:**
     - with `S=$(psql … -Atc "select salt from ops.pii_salt")`, `grep -rlF --binary-files=text "$S" target-spike logs` prints nothing (`logs` is the spike's dbt log folder);
     - `sqlfluff lint tests/singular/<the draft>` with the repo's `.sqlfluff` passes, or passes with scoped `-- noqa: <rule>` lines (keep them in Task 2).
     - *Fallback:* if a file matches, name it and stop.
- [ ] **Step 4:** Drop `sherpa_pii_spike` (from `sherpa`), `rm -rf /tmp/sherpa-pii-spike`.

---

### Task 2: The warehouse hides PII; the catalog sees only the views

**Model:** implementer `sonnet` · reviewer **`opus`** (roles, grants and fail-closed behaviour)

**Files:**
- Create:
  - `data-platform/dbt/macros/{ensure_pii_salt,mask_pii}.sql`
  - `data-platform/dbt/models/marts/customers/dim_customers.sql`
  - `data-platform/dbt/tests/singular/assert_pii_marts_are_masked_views.sql`
- Move: `git mv …/customers/dim_customers.sql …/customers/dim_customers_unmasked.sql` (unchanged SQL).
- Modify:
  - dbt: `data-platform/dbt/dbt_project.yml`, `data-platform/dbt/macros/grant_read_access.sql`, `data-platform/dbt/models/marts/customers/_customers__models.yml`
  - data-platform tests: `data-platform/tests/dbt/test_reader_access.py`
  - MCP: `agent/mcp-server/src/sherpa_mcp/{catalog,tools}.py`, `agent/mcp-server/tests/{test_catalog,test_tools,test_examples}.py`, `agent/mcp-server/tests/fixtures/manifest.json`
  - UI: `ui/backend/src/sherpa_ui/lineage/lineage_service.py`, `ui/backend/tests/lineage/{test_lineage_service,test_column_lineage_warehouse}.py`, `ui/frontend/e2e/helpers.ts`

**Interfaces — Produces:**
- `ensure_pii_salt()` returns this SQL, like `ensure_extensions()`, with a short header comment:
  ```sql
  create schema if not exists ops;
  create table if not exists ops.pii_salt (
      id boolean primary key default true check (id),
      salt text not null check (salt <> '')
  );
  insert into ops.pii_salt (salt) values (gen_random_uuid()::text || gen_random_uuid()::text) on conflict do nothing;
  ```
- `{{ mask_pii(column) }}` renders `md5(<column> || (select salt from {{ api.Relation.create(database=target.database, schema='ops', identifier='pii_salt') }}))`. A NULL stays NULL.
- `sherpa_mcp.catalog`:
  - `UNMASKED_SUFFIX = "_unmasked"`;
  - `MartCatalog.marts` holds no `*_unmasked` model;
  - `MartColumn.is_pii` is the column's `meta.masked`;
  - `class UnmaskedArtifacts(CatalogUnavailable)` is raised for a visible mart with a `meta.pii` column. `MartCatalogLoader.load()` re-raises it after dropping its cached catalog and stamp. The message is `mart <name> has a column with meta.pii (<column>): artifacts from before PII was hashed in the mart; run <command>`, where `<command>` is `make eval-db` when the target folder is named `target-eval` and `make build-prod` otherwise.
- `NodeDetails.is_mart` is false for an `_unmasked` node.

- [ ] **Step 1: Tests first, data platform** (`test_reader_access.py`; the module is `warehouse` and `database` is parametrized over main and eval):
  - `UNMASKED = ("dim_customers_unmasked",)`. `test_manifest_marts_are_the_expected_marts` asserts that the `marts` models not ending in `_unmasked` are `MARTS`, and those that do are `UNMASKED`.
  - New `class TestMaskedMarts`:
    - `test_no_reader_privilege_on_an_unmasked_relation_or_the_salt(admin, reader)`:
      - admin lists, from `pg_class` in **every** schema, the relations named `%\_unmasked`, plus `ops.pii_salt`;
      - precondition: the list holds `marts.dim_customers_unmasked` and `ops.pii_salt`;
      - for each, `has_table_privilege(<role>, oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')` and `has_any_column_privilege(<role>, oid, 'SELECT,INSERT,UPDATE,REFERENCES')` are false, for the reader and for `public`;
      - `has_table_privilege(<role>, 'marts.<m>', 'SELECT')` is true for every `MARTS`.
    - `test_reading_an_unmasked_table_or_the_salt_is_denied`, parametrized over `("marts", "dim_customers_unmasked")` and `("ops", "pii_salt")`: `_exists(admin, …)`, then `errors.InsufficientPrivilege` for the reader.
    - `test_pg_stats_shows_readers_no_personal_column`: as the reader, `select count(*) from pg_stats where attname in ('email', 'first_name', 'last_name')` is 0. That covers every schema: views have no statistics, and the readers hold no privilege on a table with those columns. On main this needs `make build` too, because `dev_marts` holds a table today.
    - `test_pii_columns_are_md5_of_the_value_and_the_salt(admin, reader)`:
      - the reader reads `customer_id, email` from the view for one deleted customer (precondition: one exists) and 20 others;
      - every non-NULL value matches `^[0-9a-f]{32}$`;
      - admin answers with one boolean, `bool_and((v_email is not distinct from md5(t.email || s.salt)) and …)` over the table, `ops.pii_salt` and the reader's values passed as parameters, and it is true. The salt and the real values never leave Postgres.
    - `test_counts_survive_the_hashing`: `count(email)` and `count(distinct email)` read as the reader from the view equal admin's from the table.
    - `test_whole_row_forms_return_only_hashes(admin, reader, query)`. The reader runs each form for one customer with an email:
      - `select email as e from …` and `select lower(email) || '' from …`;
      - `select c::text from marts.dim_customers c where customer_id = %s`;
      - `select to_jsonb(c)::text …`, `select (c.*)::text …`, `select c.row_to_json::text …`.
      Admin then answers `select position(t.email in %s) > 0 or position(t.first_name in %s) > 0 or position(t.last_name in %s) > 0 from marts.dim_customers_unmasked t where customer_id = %s` and gets `false`. Assert `leaked is False`, with a message that names the form.
    - `test_the_salt_is_in_no_artifact(admin)`:
      - admin reads the salt;
      - `hits` lists every file under `data-platform/dbt/target*` and `data-platform/dbt/logs` whose bytes contain it;
      - `assert hits == []` (paths only).
  - Run `uv run --env-file .env pytest -m warehouse data-platform/tests/dbt -q`. Expected: the new tests FAIL.
- [ ] **Step 2: Tests first, catalog, tools, lineage** (default suite):
  - **The fixture.** In `fixtures/manifest.json`, `meta.pii` becomes `meta.masked` on `dim_customers.email` and `dim_products.supplier_email`, in both `meta` and `config.meta`. Add `model.sherpa.dim_customers_unmasked` in `marts`, with columns `customer_id` and `email` (`meta.pii: true`) and no `meta.primary_key`, so including it would raise.
  - `test_catalog.py`:
    - `test_an_unmasked_table_is_not_a_visible_mart`: `visible_names() == {"fct_orders", "dim_customers", "dim_products"}`;
    - `test_is_pii_is_the_column_s_meta_masked`;
    - `test_a_build_with_pii_in_a_visible_mart_replaces_the_cached_catalog`: load the fixture, set `meta.pii: true` on `fct_orders.order_id` and bump the mtime (`_bump_mtime`), then `pytest.raises(UnmaskedArtifacts, match="run make build-prod")` on two calls in a row (the cache is gone, not kept);
    - the same refusal under a folder named `target-eval` says `run make eval-db`;
    - existing edits of `["meta"]["pii"]` become `masked`.
  - Warehouse `test_masked_columns_are_the_specs`:
    - `{(m.name, c.name) for m in marts for c in m.columns if c.is_pii} == {("dim_customers", "email")}`;
    - each of their descriptions ends with the sentence from Step 3.
  - `test_tools.py`: the `does_not_cover` literal lists in the tests (`:40`, `:393`) gain the new line from Step 4.
  - `test_examples.py`: `_yaml_mart_columns` skips models whose name ends in `_unmasked`, so both example tests stay green. Task 4 deletes the helper.
  - `test_lineage_service.py`: `test_an_unmasked_node_is_not_offered_in_the_explorer`, with a node `dim_customers_unmasked` in `marts` added in this test (not in the shared fixture), gives `is_mart is False`.
  - Run them. Expected: the new ones FAIL.
- [ ] **Step 3: Implement the dbt side.**
  - `dbt_project.yml`: `on-run-start` becomes the list `["{{ ensure_extensions() }}", "{{ ensure_pii_salt() }}"]`.
  - The two macros as in Interfaces.
  - **`dim_customers.sql`:**
    - `{{ config(materialized='view') }}`, with a one-line comment: the published customer dimension, PII pseudonymised (spec §6.5);
    - it selects the columns of `dim_customers_unmasked` in order except `first_name` and `last_name`, with `email` as `{{ mask_pii('email') }} as email`.
  - **`_customers__models.yml`, `dim_customers_unmasked`:**
    - a short description: what it computes, real PII, no reader can query it, only the view reads it;
    - `config: grants: {select: []}`;
    - every column with `data_type` and a one-line description, `meta.pii: true` on the three PII columns, and the `customer_id` constraints;
    - no data tests;
    - the unit test keeps its name, with `model: dim_customers_unmasked`.
  - **`_customers__models.yml`, `dim_customers`:**
    - today's model description, its `meta`, and its columns with their descriptions, synonyms and every data test; no constraints;
    - `meta.masked: true` on the three PII columns;
    - each of the three descriptions ends: `Pseudonymised: an md5 hash of the value and a secret salt. Equal values hash equally, so counts, joins and group by work, but comparing it with a plain value, LIKE or a string function matches nothing; identify customers by customer_id.`;
    - do not name the `_unmasked` table, because the agent reads this text.
  - **The consumers of `dim_customers` all read the view, unchanged:**
    - the relationships tests in `_sales__models.yml` (5) and `_marketing_web__models.yml` (1);
    - the singular tests `assert_buyer_revenue_pareto`, `assert_deleted_customers_are_anonymized` (NULL stays NULL through the hash), `assert_dim_customers_without_orders_are_prospects`, `assert_first_orders_match_dim_customers` and `assert_no_test_accounts_in_marts`.
  - **`grant_read_access`**, the prod/eval branch:
    1. `usage on schema marts, extensions` to both roles, as now.
    2. List the relations of `marts` with the global macro `list_relations_without_caching(api.Relation.create(database=target.database, schema='marts'))` (name and type).
    3. **First**, `revoke all on <relation> from <agent>, <ui>, public` for every relation named `*_unmasked`.
    4. Then, for each graph model in `marts` not named `*_unmasked`:
       - not built: skip, and log it;
       - the graph has a `<name>_unmasked` sibling and the relation is a table (an older build's raw values): `revoke all … from <agent>, <ui>, public` and log it;
       - otherwise: `grant select`.
    5. The `ops` grants as now.
    It never fails on a missing mart or view. Update the header comment.
  - **`assert_pii_marts_are_masked_views.sql`.** Failing rows `(model text, problem text)` hold only literals. It starts with `-- depends_on: {{ ref('dim_customers') }}` and `-- depends_on: {{ ref('dim_customers_unmasked') }}`. Under `{% if execute %}`, over `graph.nodes`, one literal row or one SQL arm per broken rule (spec §6.5 Checks):
    - (a) a model with a `meta.pii` column that is not a table named `*_unmasked`;
    - (b) its sibling missing from the graph or not a view;
    - (c) in Postgres:
      - either relation missing (`to_regclass(<relation_name>)` is null);
      - `relkind` not `v` for the view or not `r` for the table;
      - the view's `reloptions is not null`;
      - the column names ordered by `attnum` differ;
    - (d) the view's `meta.masked` set differs from the table's `meta.pii` set, checked both ways;
    - (e) per PII column, `exists` a joined row (on the view's `meta.primary_key`) where the real value is not null and the masked one is null or equal to it;
    - (f) the view or the table is missing from `model.depends_on.nodes`, with the text `add -- depends_on lines for <view> and <table> to this test`.
    Arms are `union all`ed after `select null::text as model, null::text as problem where false`. Use Spike 7's `-- noqa` lines if it needed them.
- [ ] **Step 4: Implement the MCP and UI side.**
  - `catalog.py`, as in Interfaces:
    - skip `_unmasked` models before `_build_mart`;
    - `is_pii` from `masked`;
    - the refusal in `_build_catalog`, and `load()` handling `UnmaskedArtifacts` before the generic `CatalogUnavailable` (`self._catalog = None`, `self._stamp = None`, re-raise);
    - update the `MARTS_SCHEMA` comment.
  - `tools.py`: `DOES_NOT_COVER` gains, after "currencies other than EUR", `"customers' email addresses and names, which the marts hold only as pseudonymised hashes (comparing one with a plain value matches nothing)"`.
  - `lineage_service.py:247`: `is_mart=node.layer == Layer.MARTS and not node.name.endswith(UNMASKED_SUFFIX)`.
  - `ui/frontend/e2e/helpers.ts:17`: the `information_schema.tables` query adds `and table_name not like '%\_unmasked'`.
  - `test_column_lineage_warehouse.py`:
    - `test_a_row_count_names_the_model_it_counts` reads `model.sherpa.dim_customers_unmasked`;
    - add `test_a_masked_column_reads_the_salt_from_outside_the_project`, pinning Spike 5's reading for `dim_customers.email` (`"transformed"`, `"partial"`, the note naming `sherpa.ops.pii_salt`).
- [ ] **Step 5: Build and verify.**
  - `make build`, `make build-prod`, `make eval-db` (the last is slow: a full year).
  - The `build-prod` log shows the revoke on `marts.dim_customers_unmasked` and the grant on the view.
  - `make lint`, `make test`, `uv run --env-file .env pytest -m warehouse`, `make up && make e2e`. Expected: all pass. Warehouse tests expecting `***` still pass, because the masker masks the hashes by name.
- [ ] **Step 6: Repair check** (manual, once):
  1. `cd data-platform/dbt && DBT_PROFILES_DIR=. uv run --env-file ../../.env dbt run --select dim_customers_unmasked --target prod --target-path target-prod`. As `sherpa_agent`, `select 1 from marts.dim_customers` now fails with "relation does not exist".
  2. `dbt build --select dim_customers_unmasked+ --target prod --target-path target-prod` repairs it.
  3. Paste both outputs (about 10 lines) into the PR description.
- [ ] **Step 7: First migration and negative check.**
  - After the first `make build-prod`, and before restarting the `mcp` and `ui` containers (`docker compose restart mcp ui`), run `select email from marts.dim_customers limit 3` as `sherpa_agent` (`docker compose exec postgres psql -U sherpa_agent -d sherpa`). Expected: 32-character hex values or NULL. A failed view build leaves main's raw table in place; the hook's table-type revoke then denies it.
  - Then, once:
    1. Add `meta: {pii: true}` to `fct_orders.order_number`.
    2. `dbt test --select assert_pii_marts_are_masked_views` fails with the row `(fct_orders, …not a table named *_unmasked)`.
    3. Revert, and paste the output into the PR description.
- [ ] **Step 8: Commit:**
  ```bash
  git add data-platform agent/mcp-server ui
  git commit -m "feat: marts with PII are views that hash it over a table no reader can read"
  ```

---

### Task 3: Results are no longer masked or rewritten (MCP server and UI)

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:**
- MCP: `agent/mcp-server/src/sherpa_mcp/{masking.py (delete),tools.py,catalog.py,database.py,server.py}`, `agent/mcp-server/tests/{test_masking.py (delete),test_tools.py,test_catalog.py,test_database.py}`.
- UI backend: `ui/backend/src/sherpa_ui/readonly/{bounded_result,query_runner,sort_filter}.py`, `ui/backend/src/sherpa_ui/api/{errors,queries}.py`, `ui/backend/tests/readonly/{test_bounded_result,test_sort_filter,test_sort_filter_parity,test_query_runner}.py`, `ui/backend/tests/api/test_queries_api.py`, `ui/backend/tests/test_app.py`.
- Frontend: `ui/frontend/src/components/{resultView.ts,ResultGrid.tsx}`, `ui/frontend/src/screens/explorer/WorksheetPanel.tsx`, `ui/frontend/src/api/schema.d.ts`, `ui/frontend/tests/{ResultGrid,ExplorerResults}.test.tsx`.

**Interfaces — Produces:**
- `BoundedResult(row_cap: int)`: the `columns` parameter was only for the masker.
- `build_wrapped_query(sql_text, result_columns, sort=(), filters=())`.
- `QueryRunner.run` raises only `UiQueryError` and `UnknownColumnError`.
- `to_tool_error` and `QueryRunner._query_error` report a data exception like any other Postgres error: message, position, hint.
- `ResultGrid` has no `masked` prop.

- [ ] **Step 1: Rewrite the tests first.** They FAIL while the masker and the rewrite exist.
  - **`test_tools.py`, warehouse:**
    - `test_describe_table_samples_are_ordered_by_pk_and_masked` becomes `…_and_pseudonymised`: `email` cells match `^[0-9a-f]{32}$` or are None;
    - in `test_run_sql_truncates_masks_and_ids`, the emails are 32-character hex;
    - `test_error_text_never_carries_pii_values` becomes `test_a_data_error_quotes_a_hash`: the error matches `invalid input syntax for type integer: "[0-9a-f]{32}"`.
  - **`test_tools.py`, default suite:**
    - delete `test_sample_rows_mask_the_pii_of_every_mart`;
    - `test_rows_are_masked_by_the_pii_columns_of_every_mart` becomes `test_rows_and_flags_come_back_as_fetched`, with rows `(("acme", "a@b.com"),)`.
  - **`test_database.py`:**
    - `test_data_exceptions_never_carry_the_value` becomes `test_a_data_exception_keeps_postgres_message`: one row, `InvalidTextRepresentation('invalid input syntax for type integer: "x"')` gives `[sql] invalid input syntax for type integer: "x"`;
    - `test_division_by_zero_gets_its_remedy` becomes `…_is_postgres_message`: `[sql] division by zero`.
  - **`test_query_runner.py`:**
    - `test_pii_masked` becomes `test_pii_arrives_hashed_in_both_modes`;
    - `test_server_mode_refuses_masked_columns` becomes `test_server_mode_sorts_and_filters_a_hashed_column`:
      - sorting `email` ascending gives `mode == "server"`, and the column equals its Python `sorted` order with NULLs last;
      - `contains "@"` returns 0 rows;
      - no connection leaks.
    - the two data-error tests expect Postgres's message at the same position. The literal `"jurre931@hotmail.com"` is the query's own; the stored-value one matches the hash regex.
    - the parametrized fake data-error test keeps one row, which passes through;
    - drop the PII comment in `test_null_cells_stay_null`.
  - **Delete:**
    - `test_masking.py`;
    - `test_bounded_result.py::test_pii_is_masked_before_it_is_cut_or_counted`, and the `columns`/`NO_PII` arguments everywhere;
    - in `test_sort_filter.py`: `test_masked_columns_are_neither_sorted_nor_filtered`, `test_columns_the_masking_leaves_alone_can_be_sorted_and_filtered`, `test_an_unknown_column_is_reported_before_a_pii_one`, and every masker argument;
    - `PiiMasker` in `test_sort_filter_parity.py`;
    - `test_queries_api.py::test_a_masked_sort_or_filter_column_is_422_pii_column`;
    - `PII_COLUMN_CODE` in `test_app.py`;
    - in `test_catalog.py`: `test_pii_columns_are_the_specs`, `TestMartCatalog::test_pii_columns`, `test_pii_in_a_later_mart_is_found_when_the_first_mart_has_none`.
  - **Vitest:**
    - `ResultGrid.test.tsx`: delete the masked-column test and `Harness`'s `masked`;
    - `ExplorerResults.test.tsx`:
      - delete the `pii_column` row;
      - "is never sent for a column the catalog marks as PII" becomes "is sent for a column the catalog marks as PII, like any other";
      - the data-error literal (`:190`) becomes `invalid input syntax for type integer: "zzz"`.
- [ ] **Step 2: Delete the code.**
  - **MCP:**
    - `masking.py`;
    - `MartCatalog.pii_columns()`;
    - the two `PiiMasker(...).mask` calls and the import in `tools.py` (`rows=result.rows`, already tuples);
    - in `database.py`: `DATA_ERROR_HINTS`, `DATA_ERROR_HINT`, their comment, `data_error_message`, and the `DataError` branch of `to_tool_error`;
    - `server.py:101` and `:180`: "with PII masked as '***'" becomes "with PII columns as salted hashes", and "PII columns come back as '***'" becomes "PII columns come back as salted hashes".
  - **UI backend:**
    - the masker parameter, attribute and import of `BoundedResult` (and its `columns`), of `QueryRunner._execute`/`_fetch` (`query_runner.py:397` becomes `BoundedResult(self._settings.ui_row_cap)`) and of `build_wrapped_query`;
    - `PiiColumnError` and `PII_COLUMN_MESSAGE`;
    - `PII_COLUMN_CODE`, and `pii_column` from the 422 description;
    - the `except PiiColumnError` branch in `api/queries.py`;
    - the `DataError` branch of `_query_error` and the `data_error_message` import;
    - the "masking"/"PII-masked" docstrings and comments in `query_runner.py` and `bounded_result.py`.
  - **Frontend:**
    - `maskedColumnNames` and its `CatalogTree` import;
    - `masked`, `MASKED_HINT` and the masked option in `ResultGrid.tsx`;
    - `useCatalog`, `masked` and `pii_column` in `WorksheetPanel.tsx`.
    - Then `make ui-client`.
- [ ] **Step 3: Verify.**
  - `rg -n "PiiMasker|pii_columns|masks\(|data_error_message|DATA_ERROR_HINT|PiiColumnError|pii_column|maskedColumnNames|MASKED_HINT|\*\*\*|masked as" agent ui --glob '!**/node_modules/**' --glob '!**/dist/**'` prints nothing.
  - `make lint`, `make test`, `cd ui/frontend && npm test`, `uv run --env-file .env pytest -m warehouse agent ui/backend`, `make e2e`.
- [ ] **Step 4: Commit** `refactor: results are no longer masked or rewritten; the marts hide PII`.

---

### Task 4: The guard: a closed cast-type list; the resolver, PII, padding, parser and identifier rules go

**Model:** implementer `sonnet` · reviewer **`opus`** (the security boundary: re-derive that every deleted rule protected only PII, padding, a parser differential or the resolver's name matching)

**Files:**
- `agent/mcp-server/src/sherpa_mcp/{sql_guard,catalog,tools}.py`, `agent/mcp-server/tests/{test_sql_guard,test_catalog,test_examples,test_tools}.py`
- `ui/backend/src/sherpa_ui/readonly/{query_runner,bounded_result}.py`, `ui/backend/tests/readonly/test_query_runner.py`
- `ui/parity/sort_filter_cases.json`

**Interfaces — Produces:**
- `SqlGuard(visible_marts: frozenset[str], allowed_functions: frozenset[str] = ALLOWED_FUNCTIONS)`.
- `MartCatalog.visible_marts() -> frozenset[str]`, the renamed `visible_names()`; `mart_columns()` is deleted.
- `CAST_TYPES: Final[frozenset[exp.DataType.Type]]`, with the agent spec §4.2 reference, and `CAST_HINT: Final = "cast to smallint, integer, bigint, numeric, real, double precision, boolean, text, varchar, date, time, timestamp, timestamptz, interval or an array of these"`.

What `CAST_TYPES` admits, verified on sqlglot 30.24.0:

| Member | Postgres spellings |
|---|---|
| `SMALLINT` | `smallint`, `int2` |
| `INT` | `integer`, `int`, `int4` |
| `BIGINT` | `bigint`, `int8` |
| `DECIMAL` | `numeric`, `numeric(12,2)`, `decimal(5)` |
| `FLOAT` | `real`, `float4` |
| `DOUBLE` | `double precision`, `float8`, `float` |
| `BOOLEAN` | `boolean`, `bool` |
| `TEXT` | `text` |
| `VARCHAR` | `varchar`, `varchar(20)`, `character varying(5)` |
| `DATE` | `date` |
| `TIME` | `time`, `time(3)` |
| `TIMESTAMP` | `timestamp`, `timestamp(3)`, `timestamp without time zone` |
| `TIMESTAMPTZ` | `timestamptz`, `timestamp with time zone` |
| `INTERVAL` | `interval` |
| `ARRAY` | `text[]`, `int[]`, `numeric(12,2)[]`, `int[][]` |

The walk visits the element type too, so `jsonb[]` fails on `JSONB`.

- [ ] **Step 1: New tests first** (`test_sql_guard.py`):
  - **`TestCastTypes`:**
    - `test_the_cast_types_are_exactly_the_specs`: `CAST_TYPES` equals those 15 members, as a literal.
    - `test_postgres_spellings_of_the_listed_types_pass`, parametrized over every spelling in the table.
    - `test_other_types_are_rejected`, parametrized `(type, rendered)`, expecting exactly `[guard:cast] cast to type <rendered> is not allowed; <CAST_HINT literal>`. Rendered values, verified:

      | Type as written | Rendered |
      |---|---|
      | `json`, `jsonb`, `uuid`, `bytea` | `JSON`, `JSONB`, `UUID`, `BYTEA` |
      | `char(5)`, `character(3)`, `bpchar` | `CHAR(5)`, `CHAR(3)`, `BPCHAR` |
      | `bit(3)`, `varbit` | `BIT(3)`, `varbit` |
      | `regclass`, `pg_catalog.regclass` | `REGCLASS`, `pg_catalog.regclass` |
      | `name`, `money`, `inet`, `xml`, `oid` | `NAME`, `MONEY`, `INET`, `XML`, `OID` |
      | `my_domain`, `public.t`, `"char"` | `my_domain`, `public.t`, `"char"` |
      | `timetz` | `TIMETZ` |
      | `interval day` | `INTERVAL DAY` |
      | `jsonb[]` | `JSONB` |

    - `test_a_json_literal_is_rejected`: `select json '{}' from marts.fct_orders` gives `[guard:function] function cast is not allowed; rewrite it with the common aggregate, window, string, date and math functions`. sqlglot reads it as `ParseJSON`.
  - **`test_session_identity_values_pass`:** `select user, current_role, system_user from marts.fct_orders` is ok. `current_user` and `session_user` stay rejected (existing rows).
  - **`test_string_builders_are_rejected`,** parametrized over `repeat('a', 5)`, `lpad('a', 5)` and `rpad('a', 5, 'b')`: `[guard:function] function <name> is not allowed; rewrite it with the common aggregate, window, string, date and math functions`. It replaces `test_repeat_stays_rejected`.
  - **`test_forms_the_marts_make_harmless_pass`:** it pins that the guard has no PII rule. Each of these passes:
    - `select c from marts.dim_customers c`, `select c.*::text from marts.dim_customers c`;
    - `select c.row_to_json from marts.dim_customers c`; `select (c).email from marts.dim_customers c` is rejected by the restored `field` rule (`[guard:field]`);
    - `select U&"c" from marts.dim_customers c`, `select s.e from (select email from marts.dim_customers) s(e)`;
    - `select 1_000 from marts.fct_orders`, `select 1 as {"a" * 64} from marts.fct_orders`.
  - **`TestAttributeNotationReach`** (`integration`, next to `TestFunctionsThePostgresRoleCanRun`), `test_whole_row_functions_the_role_can_call_only_read_the_row`:
    - admin selects the distinct `proname` from `pg_proc` where the agent has `EXECUTE`, `prokind in ('f', 'a')`, `pronargs >= 1`, `pronargs - pronargdefaults <= 1` and `proargtypes[0]` is one of `record`, `anyelement`, `"any"`, `anycompatible`, `anynonarray` or `anycompatiblenonarray`;
    - it equals a literal copied from a first run. The functions alone, on Postgres 16.15: `any_out`, `anycompatible_out`, `anycompatiblenonarray_out`, `anyelement_out`, `anynonarray_out`, `concat`, `hash_record`, `json_build_array`, `json_build_object`, `jsonb_build_array`, `jsonb_build_object`, `num_nonnulls`, `num_nulls`, `pg_collation_for`, `pg_column_compression`, `pg_column_size`, `pg_typeof`, `quote_literal`, `quote_nullable`, `record_out`, `record_send`, `row_to_json`, `to_json`, `to_jsonb`; plus the aggregates the run adds, such as `array_agg`, `json_agg` and `count`.
    - Review each one: it may only read, format or aggregate the row.
  - **New hostile rows in `REJECTED_QUERIES`:**
    - `select * from marts.dim_customers_unmasked` gives `table marts.dim_customers_unmasked is not a visible mart`;
    - `select * from ops.pii_salt` gives `table ops.pii_salt is outside marts`.
  - **`test_tools.py`:**
    - (warehouse) `test_run_sql_refuses_whole_row_pii_leaks` becomes `test_whole_row_forms_return_only_hashes`:
      - every form in its list, except `to_json(c)` (still `[guard:function]`), runs through `tools.run_sql`, with `array_agg(c.*)` limited to `customer_id <= 3`;
      - `"@" not in json.dumps(result.rows)`, and a 32-character hex substring is present.
    - `TestGuardTracing::test_rejection_rule_comes_from_the_error_code` uses `select pg_sleep(1) from marts.fct_orders`, rule `function`;
    - `test_a_guard_refusal_never_reaches_explain` and `test_a_refused_query_never_reaches_the_database` refuse that same query.
  - Run. Expected: the cast, string-builder, harmless-form and `_unmasked` rows FAIL. The reach test passes, and must fail on a throwaway edit of its literal.
- [ ] **Step 2: Delete the tests of removed rules first.** The two tables `REJECTED_QUERIES` and `REMEDY_HINTS` stay apart: merged they would be larger.
  - **In the tables:**
    - delete every row whose rule is `whole_row`, `column`, `pad`, `number`, `type_length` or `identifier_length`, with `LONG_NAME`, `BYTE_LIMIT_NAME` and `WIDE_NAME`;
    - the `unicode_escape` rows in table and alias positions, and `select U&"!0063" uescape '!' …`, become `parse` rows; copy sqlglot's first line from a run;
    - the expression-position ones are covered by the harmless-form test;
    - the `cast` row of `REMEDY_HINTS` gets `CAST_HINT`'s literal.
  - **Delete:**
    - `TestEscapedIdentifierTokens`, `TestWholeRowReferences`, `TestQualifiedColumns`;
    - the two `test_pad_*`, the two `test_type_length_*` and the two number tests (`test_numbers_postgres_reads_differently_are_rejected`, `test_names_strings_and_comments_with_digits_and_underscores_pass`);
    - `test_accepts_casts_to_built_in_types`, now a duplicate of the spellings test.
  - **Adjust:**
    - remove `lpad`, `rpad` and `pad` from `test_the_allow_list_is_exactly_the_specs`, `test_accepts_every_allowed_function` and `TestFunctionsThePostgresRoleCanRun`'s set;
    - `MART_COLUMNS` becomes `VISIBLE_MARTS = frozenset({"fct_orders", "dim_customers", "fct_order_items"})`.
  - **Other files:**
    - `test_catalog.py`: delete `test_mart_columns_name_every_column_of_every_visible_mart`; `visible_names` becomes `visible_marts`.
    - `test_examples.py`: delete `_yaml_mart_columns` and `test_examples_yaml_passes_the_guard_on_the_build`, since `test_tools.py::test_get_examples_returns_k_and_every_example_runs` runs every example on the build. The default test uses `SqlGuard(frozenset(marts))`.
    - `test_tools.py`: delete `test_run_sql_reads_an_aggregate_column_by_the_name_postgres_gives_it`, which pinned the resolver's aggregate names.
- [ ] **Step 3: UI tests and parity cases the guard now refuses.**
  - `test_query_runner.py`:
    - `test_a_wide_padded_result_stops_at_its_character_budget` builds its cell with `replace(replace(replace(replace('b', 'b', 'bbbbbbbbbb'), 'b', 'bbbbbbbbbb'), 'b', 'bbbbbbbbbb'), 'b', 'bbbbbbbbbb')` and expects `("b" * 10_000,) * 10`;
    - `test_column_types_reported`: `'y'::char(2) as c` becomes `user as c` (type `name`, which is `text`), and `'{}'::jsonb as j` becomes `(select s.to_jsonb from (select 1 as k) s) as j` (`other`);
    - `test_values_are_json_safe` keeps a jsonb cell the same way, `(select s.to_jsonb from (select 1 as k) s)`, expecting `{"k": 1}`;
    - `test_error_in_the_wrapper_has_no_position` keeps its json column, now through attribute notation: `select o.to_json as j from marts.fct_orders o`, sorted by `j`, gives the same "could not identify an ordering operator for type json" message.
  - `ui/parity/sort_filter_cases.json`:
    - delete the `uuids` table and its cases, because no query that passes the guard can produce a uuid;
    - delete `jsons` and `jsonbs` too, because every one of their cases is server-route Postgres json semantics, already pinned by the runner tests above;
    - `texts_cut` builds `'a' * 10,000` with the same nested `replace` instead of `lpad` (rows unchanged);
    - the coverage test still passes, since `intervals` and `int_arrays` cover `other` (checked).
- [ ] **Step 4: Implement** (`sql_guard.py`).
  - **Delete:**
    - from `SqlGuard`: `_qualified_column_errors`, `_unresolved_columns`, `_outputs`, `_select_outputs`;
    - the functions `_number_errors`, `_escaped_identifier_errors`, `_is_word_char`, `_enclosing_scope`, `_find_source`, `_alias_columns`, `_not_a_column`, `_output_name`, `_is_small_literal`, `_type_params`, `_is_projection_item`, `_is_whole_row`, `_relations`, `_is_built_in_type`;
    - the constants `MAX_PAD_LENGTH`, `WHOLE_ROW_NAME_HINT`, `ESCAPED_IDENTIFIER_*`, `NUMBER_HINT`, `_ALIAS_LIST_HINT`, `_AGGREGATE_NAMES`, `_POSTGRES`, `MAX_IDENTIFIER_BYTES`;
    - the `pad`, `type_length`, `identifier_length` and both `whole_row` cases (the `field` rule stays: `exp.Dot()` outside a type name);
    - the number and escape pre-checks in `check()`, and the unused imports;
    - `lpad`, `rpad` and `pad` from `ALLOWED_FUNCTIONS` and from its comment.
  - **Keep:** `Dot(Func)`, `complexity`, and the function allow-list.
  - **Change:**
    - the cast case becomes `case exp.DataType() if node.this not in CAST_TYPES:`, with `CAST_HINT`;
    - `__init__(visible_marts, allowed_functions=…)`, and `_table_errors` checks `name not in self._visible_marts`;
    - the class docstring loses its masking mention.
  - **Callers:**
    - `catalog.py`: `visible_marts()`, and `mart_columns()` deleted;
    - `tools.py:_guard`: `SqlGuard(catalog.visible_marts())`, with its comment ("its marts");
    - `query_runner.py:_check_guard`: `SqlGuard(catalog.visible_marts())`;
    - `query_runner.py:68`: delete the dead `"bpchar": "text"`;
    - the lpad comments: `query_runner.py:82` reads "a batch of 10,000-character cells (the cell cap)", and `bounded_result.py:15-16` drops the lpad clause.
- [ ] **Step 5: Verify.**
  - `uv run pytest agent/mcp-server/tests/test_sql_guard.py -q` and `uv run --env-file .env pytest -m "integration and not slow" agent/mcp-server/tests/test_sql_guard.py`. Every kept hostile row passes unchanged.
  - `rg -n "_is_whole_row|whole_row|_qualified_column|\"column\"|_number_errors|unicode_escape|MAX_PAD_LENGTH|type_length|identifier_length|mart_columns|visible_names|_yaml_mart_columns" agent ui` prints nothing.
  - `make lint`, `make test`, `uv run --env-file .env pytest -m warehouse`, `cd ui/frontend && npm test`.
  - `wc -l agent/mcp-server/src/sherpa_mcp/sql_guard.py` is about 400.
- [ ] **Step 6: Commit** `refactor: the guard checks marts by name and casts by a closed list; its PII and padding rules go`.

---

### Task 5: Documentation

**Model:** implementer `sonnet` · reviewer `haiku`

Rewrite in place to the current state, with no "previously". The length rules of `CLAUDE.md` apply. Link the spec rather than repeat it.

- [ ] **Step 1: `CLAUDE.md`:**
  - **Conventions:** dbt naming gains `<mart>_unmasked`, the table behind a mart with PII, whose view is `<mart>` (data platform spec §6.5).
  - **Commands:**
    - the `make build` bullet: build a PII mart with its view (`--select <mart>_unmasked+`), because the table alone drops the view;
    - the direct repair is `dbt build --select dim_customers_unmasked+ --target prod --target-path target-prod`;
    - `make reset`'s `ops` also holds the generated PII salt, so a reset changes the hashes.
  - **dbt tests:** a singular test's failing rows hold only literals (`store_failures` keeps them).
  - **Python Guidelines:** "MCP server: `sql_guard`, `masking`, …" loses `masking`.
- [ ] **Step 2: `README.md`:** the guides list says "PII" instead of "masking".
- [ ] **Step 3: Guides**, each with its symptom → fix table updated:
  - `basic/using-the-mcp-server.md`:
    - "PII masking" becomes "PII": salted hashes from the mart; equal values hash equally; a comparison with a plain value matches nothing; `search_values` refuses PII columns; data errors are Postgres's text.
    - Known limitations: delete the `pg_stats` line, and replace `replace(lpad(...))` with "nested string functions such as `replace` over a literal".
    - Troubleshooting: drop "name columns instead of `select c`".
  - `basic/querying-the-marts.md`:
    - `email` is a hash and names are not published, so count or group by `email` and identify customers by `customer_id`;
    - add `agent_psql -c "select * from marts.dim_customers_unmasked"   # ERROR:  permission denied`.
  - `basic/building-the-warehouse.md`:
    - Hooks and grants: `ensure_pii_salt`, and the per-relation grants with the revoke;
    - the `+` selector warning;
    - rows: `relation "marts.dim_customers" does not exist` → the repair command above.
    - It is at 146 lines: trim to stay under about 150.
  - `basic/using-the-explorer.md`:
    - the PII badge means salted hashes;
    - delete the "Masked columns" bullet and the `***` item;
    - data errors show Postgres's message;
    - the `other` examples become "interval, json or array".
  - `basic/using-lineage.md`: one line. A mart view's PII columns read `partial`, since the salt is in `ops.pii_salt`, outside the project, and an `_unmasked` node has no "Open in Explorer".
  - `advanced/adding-or-changing-a-mart.md`:
    - new section "Add a mart with PII": the pair, `meta.pii`/`meta.masked`, `grants: {select: []}` (never `+select`), `mask_pii`, the two `-- depends_on` lines, `UNMASKED` in `test_reader_access.py`, and always building the table with its view (`+`). Which tests prove it: the singular test, `TestMaskedMarts`, `test_masked_columns_are_the_specs`.
    - Line 33: `meta.pii` points there; line 73: grants are per relation.
    - Stay under about 250 lines.
  - `advanced/extending-the-mcp-server.md`:
    - drop the `masking.py` and `test_masking.py` rows and the `PiiMasker` line;
    - the guard bullets: the closed cast list (`CAST_TYPES`, pinned by `test_the_cast_types_are_exactly_the_specs`), no `lpad`/`rpad`;
    - `meta.pii` becomes `meta.masked` on the view.
  - `advanced/extending-the-ui.md`, lines 112–123: the guard row loses "real columns"; the masking paragraph and `pii_column`/`maskedColumnNames` go.
- [ ] **Step 4: Specs:** only what implementation decided, for example a Spike 2 surprise in §6.5. Then `make lint`. Commit `docs: PII hidden by the marts (CLAUDE.md, README, guides)`.

---

### Task 6: Panel review of the whole branch

**Model:** every reviewer and the devil's advocate on **`opus`**, dispatched by the controller; fixes per their task's model.

- [ ] **Step 1: Reviewers, in parallel.** Each is a fresh subagent with its focus, `git diff main...HEAD`, this plan and the spec sections in the header. It reads code, runs read-only commands, makes no edits, and returns `Critical | Important | Minor` · `file:line` · what is wrong · why it matters · the fix.
  - **Data engineer:**
    - the pair's YAML, contract, `persist_docs`/`analyze` on the view, the moved unit test;
    - `grant_read_access`: the `_unmasked` revokes first, the table-type revoke, never raising, `run-operation`, dev vs prod/eval;
    - the singular test's Jinja and its `depends_on` check;
    - partial builds and the repair, `make reset`, `make eval-db`.
  - **Software engineer:**
    - the deletions are complete (`rg`) and no dead code is left;
    - `SqlGuard(visible_marts)` and its callers;
    - the sticky refusal, `BoundedResult(row_cap)`;
    - no shallow tests among the rewritten ones;
    - the line counts against the plan's estimate.
  - **Frontend engineer:**
    - the removed `masked` plumbing and the regenerated `schema.d.ts`;
    - the Explorer copy and the PII badge;
    - "Open in Explorer" hidden for `_unmasked`, and `martNames`;
    - `make e2e`.
  - **Security engineer.** Word the brief as a **defensive access-control review**: an earlier brief phrased around attacks was refused by the API's safeguards. Ask it to confirm that the access rules hold:
    - the reader roles' privileges on `_unmasked` relations and `ops.pii_salt`, in every schema, after every kind of build;
    - `PUBLIC` grants;
    - `pg_stats`;
    - the non-`security_invoker` view;
    - the salt's absence from every `target*` folder and `logs/`;
    - the containers' read-only mount of `data-platform/dbt`;
    - that the deleted guard rules guarded nothing the views and the cast list don't now cover (the attribute-notation list, `U&`, number forms, field selection, padding types);
    - `md5` with a 256-bit salt, as data platform spec §12 argues.
  - **AI engineer:**
    - the model-facing texts: guard hints, `CAST_HINT`, Postgres's raw data errors;
    - hashes in `describe_table` samples;
    - `search_values`' refusal;
    - the new `does_not_cover` line and the column descriptions.
  - **Analytics domain expert:**
    - what the hashed `email` still answers (distinct counts, joins on one salt, group by);
    - the normalisation before hashing;
    - NULL for deleted customers;
    - the descriptions the agent reads.
- [ ] **Step 2: Devil's advocate** (fresh, `opus`), given the diff and every finding: it challenges findings that are wrong or would over-engineer, and adds the failure modes the panel missed.
- [ ] **Step 3: Triage** every finding in one table: fix, defer or reject, with a one-line reason. A rejection cites the spec or a test. Deferring a Critical or Important finding needs the owner's OK.
- [ ] **Step 4: Fixes.** Each starts with a test that reproduces the problem, then goes through the usual implement → review loop. A design change also updates the spec and the guides. Re-verify with `make lint`, `make test`, the warehouse suite and `make e2e`. The triage table, the spike outcomes and Task 2's manual checks go in the PR description; the panel's report is not committed.
