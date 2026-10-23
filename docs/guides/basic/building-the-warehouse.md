# Building the warehouse

dbt turns the raw schemas the generator writes into tested, documented models. This guide shows how to run it, how to look at what it built, and what to do when a test fails. It assumes Postgres is up and holds data ([Using the generator](generator-cli.md)).

A few dbt terms first:

- A **model** is a `SELECT` in a `.sql` file under `dbt/models/`. dbt wraps it in `CREATE VIEW` or `CREATE TABLE` (its *materialization*) and runs the models in dependency order.
- A **source** declares a table dbt did not build, here the generator's `raw_*` tables. Models read it with `{{ source('shop', 'orders') }}`, other models with `{{ ref('stg_shop__orders') }}`. These two functions give dbt the dependency graph (the *DAG*).
- A **seed** is a small CSV in `dbt/seeds/` that dbt loads as a table, such as `country_codes`.
- A **data test** is a query that returns the rows breaking a rule (`unique`, `not_null`, `accepted_values`, `relationships`, ...). Zero rows means it passes.
- A **target** is one named connection in `dbt/profiles.yml`: `dev` (the default) and `prod`. Credentials come from `.env` through `env_var()`.

The project currently has the seeds, one staging view per raw table and `dim_date`. Outputs below are real and come from the 365-day dataset unless stated; timings vary by machine.

## Dev and prod

The same project builds into different schemas depending on the target (the `generate_schema_name` macro in `dbt/macros/`):

| | `dev` (default) | `prod` |
|---|---|---|
| Seeds | `dev_seeds` | `seeds` |
| Staging views | `dev_staging` | `staging` |
| Marts | `dev_marts` | `marts` |
| Artifacts (`manifest.json`, `catalog.json`, ...) | `dbt/target/` | `dbt/target-prod/` |
| Make targets | `make build`, `make docs` | `make build-prod`, `make docs-prod` |

Use `dev` while you work; `prod` is what consumers read. Prod commands always pass `--target-path target-prod`, so a dev run never overwrites the prod artifacts. After `make build` and `make build-prod` the database has both sets next to the raw schemas:

```
          List of schemas
     Name      |       Owner
---------------+-------------------
 dev_marts     | sherpa_admin
 dev_seeds     | sherpa_admin
 dev_staging   | sherpa_admin
 marts         | sherpa_admin
 public        | pg_database_owner
 raw_crm       | sherpa_admin
 raw_events    | sherpa_admin
 raw_marketing | sherpa_admin
 raw_meta      | sherpa_admin
 raw_shop      | sherpa_admin
 seeds         | sherpa_admin
 staging       | sherpa_admin
```

(`docker compose exec postgres psql -U sherpa_admin -d sherpa -c '\dn'`.)

## Building

```bash
make deps        # install the dbt packages (dbt_utils) into dbt/dbt_packages/
make build       # source freshness, then dbt build on dev
make build-prod  # the same on prod
```

`make deps` downloads the versions pinned in `dbt/package-lock.yml`:

```
cd dbt && DBT_PROFILES_DIR=. uv run --env-file ../.env dbt deps
08:05:42  Running with dbt=1.12.6
08:05:43  Installing dbt-labs/dbt_utils
08:05:44  Installed from version 1.4.1
08:05:44  Up to date!
```

`dbt build` runs seeds, models and tests together in DAG order, and skips whatever depends on a failure. `make build` took 28 seconds on a year of data:

```
cd dbt && DBT_PROFILES_DIR=. uv run --env-file ../.env dbt build
09:24:23  Running with dbt=1.12.6
09:24:23  Registered adapter: postgres=1.11.0
09:24:23  [WARNING]: Configuration paths exist in your dbt_project.yml file which do not apply to any resources.
There are 1 unused configuration paths:
- snapshots.sherpa
09:24:24  Found 22 models, 122 data tests, 2 seeds, 21 sources, 594 macros
09:24:24
09:24:24  Concurrency: 4 threads (target='dev')
09:24:24
09:24:24  1 of 146 START sql table model dev_marts.dim_date .............................. [RUN]
09:24:24  2 of 146 START sql view model dev_staging.stg_crm__email_sends ................. [RUN]
...
09:24:37  146 of 146 PASS unique_stg_shop__addresses_address_id ........................... [PASS in 0.07s]
09:24:39  135 of 146 PASS relationships_stg_events__events_customer_id__customer_id__ref_stg_shop__customers_  [PASS in 2.79s]
09:24:39
09:24:39  Finished running 2 seeds, 1 table model, 122 data tests, 21 view models in 0 hours 0 minutes and 14.89 seconds (14.89s).
09:24:39
09:24:39  Completed successfully
09:24:39
09:24:39  Done. PASS=146 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=146
```

The warning is expected: `dbt_project.yml` configures a schema for snapshots and the project has no snapshots. `make build-prod` prints the same with `target='prod'` and schemas without the prefix (`marts.dim_date`).

`make seed` loads only the seeds, which is handy after editing a CSV:

```
09:27:12  1 of 2 OK loaded seed file dev_seeds.channel_mapping ........................... [INSERT 5 in 0.16s]
09:27:12  2 of 2 OK loaded seed file dev_seeds.country_codes ............................. [INSERT 18 in 0.16s]
```

### Source freshness

`dbt build` does not check whether the raw data is recent, so `make build` runs `dbt source freshness` first. Four source tables declare a `freshness` block in their `_<source>__sources.yml`: dbt compares the newest `loaded_at_field` with the current time.

| Source table | Field | Warn after | Error after |
|---|---|---|---|
| `shop.orders` | `updated_at` | 36 h | 72 h |
| `events.events` | `received_at` | 36 h | 72 h |
| `crm.leads` | `updated_at` | 36 h | 72 h |
| `marketing.ad_spend_daily` | `spend_date` | 48 h | never |

On data that reaches yesterday every check passes:

```
09:24:18  1 of 4 PASS freshness of crm.leads ............................................. [PASS in 0.05s]
09:24:18  4 of 4 PASS freshness of shop.orders ........................................... [PASS in 0.05s]
09:24:18  3 of 4 PASS freshness of marketing.ad_spend_daily .............................. [PASS in 0.07s]
09:24:18  2 of 4 PASS freshness of events.events ......................................... [PASS in 0.11s]
```

Warnings let the build continue; an error stops `make build` before `dbt build`. On a dataset whose last day was three weeks ago:

```
09:32:28  4 of 4 ERROR STALE freshness of shop.orders .................................... [ERROR STALE in 0.09s]
09:32:28  1 of 4 ERROR STALE freshness of crm.leads ...................................... [ERROR STALE in 0.11s]
09:32:28  3 of 4 WARN freshness of marketing.ad_spend_daily .............................. [WARN in 0.11s]
09:32:28  2 of 4 ERROR STALE freshness of events.events .................................. [ERROR STALE in 0.11s]
...
make: *** [Makefile:46: build] Error 1
```

The fix is to top the data up to yesterday (`make generate DAYS=<n>`, see the generator guide), not to loosen the thresholds.

## Running dbt directly

The Make targets wrap one command line. To run any other dbt command, use the same one from the `dbt/` folder:

```bash
cd dbt
DBT_PROFILES_DIR=. uv run --env-file ../.env dbt <command>
```

`DBT_PROFILES_DIR=.` points dbt at `dbt/profiles.yml` and `--env-file ../.env` supplies the `SHERPA_*` variables it reads. The examples below run from `dbt/` with that prefix, shortened to `dbt`. Add `--target prod --target-path target-prod` to run on prod. `dbt debug` checks the setup and the connection; it ends with:

```
09:27:18    Connection test: [OK connection ok]

09:27:18  All checks passed!
```

### Selecting what to run

`--select` (`-s`) takes model names, folders, sources, tags and graph operators. `dbt ls` lists what a selection matches without running anything:

```bash
dbt ls --select staging.shop --resource-type model   # every model in models/staging/shop
dbt ls --select +stg_shop__customers                 # the model and everything upstream of it
dbt ls --select source:events+                       # everything downstream of the events source
```

```
$ dbt --quiet ls --select +stg_shop__customers
sherpa.staging.shop.stg_shop__customers
sherpa.country_codes
source:sherpa.shop.customers
sherpa.accepted_values_country_codes_region__DACH__Western_Europe__Southern_Europe__Benelux__UK_Ireland
...
sherpa.staging.shop.unique_stg_shop__customers_customer_id
```

Upstream of a staging model are its source and the seeds it joins; the tests attached to them come along. `--quiet` drops the log lines.

To rebuild one model and run its tests:

```bash
dbt build --select stg_shop__payments
```

```
09:27:42  1 of 8 START sql view model dev_staging.stg_shop__payments ..................... [RUN]
09:27:42  1 of 8 OK created sql view model dev_staging.stg_shop__payments ................ [CREATE VIEW in 0.11s]
09:27:42  2 of 8 START test accepted_values_stg_shop__payments_method__card__paypal__gift_card  [RUN]
...
09:27:42  7 of 8 PASS relationships_stg_shop__refunds_payment_id__payment_id__ref_stg_shop__payments_  [PASS in 0.08s]
09:27:42  8 of 8 PASS unique_stg_shop__payments_payment_id ............................... [PASS in 0.07s]
09:27:42
09:27:42  Done. PASS=8 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=8
```

`dbt run` only builds models and `dbt test` only runs tests; both take the same `--select`. A test on another model that references this one (here the `relationships` test of `stg_shop__refunds`) runs too.

One source's freshness: `dbt source freshness --select source:events`.

### Previewing with `dbt show`

`dbt show` runs a model's query and prints the first rows without creating anything:

```bash
dbt show --select stg_shop__customers --limit 3
```

```
Previewing node 'stg_shop__customers':
| customer_id | first_name | last_name | marketing_opt_in |            signup_at |           updated_at | ... |
| ----------- | ---------- | --------- | ---------------- | -------------------- | -------------------- | --- |
|           1 | Heinz      | Stolze    |            False | 2025-10-23 19:11:... | 2025-10-23 19:11:... | ... |
|           2 | Diana      | Cook      |             True | 2025-10-23 19:06:... | 2025-10-23 19:06:... | ... |
|           3 | Maurice    | Berthelot |             True | 2025-10-23 12:45:... | 2025-10-23 12:45:... | ... |
```

`--inline` runs any query and resolves `ref()` and `source()`. The 18 raw country values (see the generator guide) come out as 8 clean codes:

```bash
dbt show --inline "select country_code, count(*) as customers from {{ ref('stg_shop__customers') }} group by 1 order by 2 desc" --limit 10
```

```
Previewing inline node:
| country_code | customers |
| ------------ | --------- |
| DE           |      1500 |
| FR           |       988 |
| ES           |       730 |
| IT           |       613 |
| NL           |       402 |
| GB           |       382 |
| AT           |       203 |
| BE           |       133 |
```

### Seeing the SQL with `dbt compile`

`dbt compile` renders the Jinja into the SQL dbt would run, without running it:

```bash
dbt compile --select stg_shop__payments
```

```
Compiled node 'stg_shop__payments' is:
with

source as (

    select * from "sherpa"."raw_shop"."payments"

),
...
```

Every compiled model and test is also written to `dbt/target/compiled/sherpa/...`, and the statements dbt actually ran (with the `CREATE VIEW` around them) to `dbt/target/run/sherpa/...`.

## Documentation and the lineage graph

```bash
make docs
```

`make docs` runs `dbt docs generate` (which writes `manifest.json`, `catalog.json` and `index.html` to `dbt/target/`) and then `dbt docs serve`, a local web server that opens a browser tab:

```
08:09:31  Running with dbt=1.12.6
Serving docs at 8080
To access from your browser, navigate to: http://localhost:8080

Press Ctrl+C to exit.
```

Stop it with Ctrl+C. `make docs-prod` does the same for prod from `dbt/target-prod/`.

The landing page is the `__overview__` doc block in `dbt/models/overview.md`. On the left, the **Project** tab groups the nodes by folder and the **Database** tab by schema. A model page shows its description, its columns with their tests, the SQL (source and compiled) and what it depends on.

The **lineage graph** button (bottom right) draws the DAG: sources on the left, flowing into the staging views, with the seeds feeding the models that join them (`country_codes` into `stg_shop__customers` and `stg_shop__addresses`). `dim_date` stands alone because it reads nothing, and so does the `channel_mapping` seed, which no model reads. The `--select` box at the bottom takes the same syntax as the CLI: `+stg_shop__customers` shows the model with its source and seed. Click a node to open its page.

`make docs-check` regenerates the docs quietly and fails if a staging model or column has no description; it prints nothing when everything is documented.

## When a test fails

To see what a failure looks like, break the R4 cleanup in `dbt/models/staging/shop/stg_shop__customers.sql` by keeping the raw country instead of the seed's code:

```sql
        source.country_code as country_code,   -- was: coalesce(country_codes.code, source.country_code)
```

```bash
dbt build --select stg_shop__customers
```

```
09:29:36  7 of 9 FAIL 162 relationships_stg_shop__customers_country_code__code__ref_country_codes_  [FAIL 162 in 0.08s]
...
09:29:37  Completed with 1 error, 0 partial successes, and 0 warnings:
09:29:37
09:29:37  [ERROR]: in test relationships_stg_shop__customers_country_code__code__ref_country_codes_ (models\staging\shop\_shop__models.yml)
09:29:37    Got 162 results, configured to fail if != 0
09:29:37
09:29:37    compiled code at target\compiled\sherpa\models\staging\shop\_shop__models.yml\relationships_stg_shop__custom_0d6747d12cdbae399e0f2f5695c1d0a9.sql
09:29:37
09:29:37  Done. PASS=8 WARN=0 ERROR=1 SKIP=0 NO-OP=0 REUSED=0 TOTAL=9
```

The test name says what broke: `relationships` from `stg_shop__customers.country_code` to `country_codes.code`, with 162 rows that have no match. `dbt build` still created the view (a model is built before its tests), but anything downstream of a failed test would be skipped.

To investigate, open the compiled test it points to. It is a plain query you can paste into psql:

```sql
with child as (
    select country_code as from_field
    from "sherpa"."dev_staging"."stg_shop__customers"
    where country_code is not null
),

parent as (
    select code as to_field
    from "sherpa"."dev_seeds"."country_codes"
)

select
    from_field

from child
left join parent
    on child.from_field = parent.to_field

where parent.to_field is null
```

Or let dbt keep the failing rows in a table with `--store-failures`:

```bash
dbt test --select stg_shop__customers --store-failures
```

```
09:29:44    See test failures:
  --------------------------------------------------------------------------------------------------------------
  select * from "sherpa"."dev_dbt_test__audit"."relationships_stg_shop__custom_0d6747d12cdbae399e0f2f5695c1d0a9"
  --------------------------------------------------------------------------------------------------------------
```

```sql
SELECT from_field, count(*)
FROM dev_dbt_test__audit.relationships_stg_shop__custom_0d6747d12cdbae399e0f2f5695c1d0a9
GROUP BY 1 ORDER BY 2 DESC;
```

```
 from_field | count
------------+-------
 de         |    55
 fr         |    36
 es         |    20
 it         |    16
 nl         |    13
 at         |     8
 uk         |     5
 UK         |     4
 be         |     3
 gb         |     2
```

Exactly the noisy codes of R4, so the seed join is missing. Restore the line, rerun `dbt build --select stg_shop__customers` (`PASS=9`), and drop the audit schema (`make reset` also removes it):

```bash
docker compose exec postgres psql -U sherpa_admin -d sherpa -c "DROP SCHEMA dev_dbt_test__audit CASCADE"
```

If something goes wrong with dbt itself rather than the data, `dbt debug` checks the connection, and `--no-partial-parse` forces dbt to re-read every file when it seems to ignore an edit.

## Next

- [Using the generator](generator-cli.md): make or refresh the data dbt reads.
- [Extending the source data model](../advanced/extending-the-source-data-model.md): add a column or table end to end, including its staging model and tests.
