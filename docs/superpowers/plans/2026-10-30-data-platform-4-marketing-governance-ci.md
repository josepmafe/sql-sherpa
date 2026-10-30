# data-platform-4 — Marketing, Governance, CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the marketing marts (`dim_campaigns`, `fct_ad_spend_daily`, `fct_leads`), govern the project with groups, access and the `sherpa_agent` exposure, add `make slim`, and make GitHub Actions the repository's pull-request gate.

**Architecture:** the three marts are tables in the existing `marts/` folders, built from staging and other marts. dbt unit tests pin their logic, and singular tests reconcile them with the marts they summarise. Groups and access are folder configs in `dbt_project.yml`. A singular test reads dbt's `graph` and keeps the exposure equal to the public marts. CI starts the same compose Postgres you run locally, builds tiny data on the `dev` target with warnings as errors, then runs `make lint`, `make test` and Vitest.

**Tech Stack:** dbt Core 1.12 (groups, access, exposures, YAML selectors, `state:modified`, `--defer`, `--empty`, `--warn-error`, unit tests), dbt_utils, Postgres 16, GitHub Actions, uv, npm.

**Spec:** `docs/superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md`:
- §6.5 `dim_campaigns`, `fct_ad_spend_daily`, `fct_leads`
- §6.6 ROAS, CAC
- §7 (the new singular and unit tests)
- §8 data-platform-4 (the features, CI, acceptance)
- §9 (`dbt clone` excluded)
- §10 (`make slim`)

Also agent spec §4.1 (`get_context`) and §9 (CI), and UI spec §6.2 (`sherpa_home` comes with Home). **This plan's commit changes the specs**:
- **Removed:** `make clone` and the `ci` target.
- **Moved:** `sherpa_home` goes to the Home milestone, because an exposure is declared by the milestone that builds its consumer.
- **Revenue reconciliation:** it becomes a singular test instead of an analysis.
- **CI:** it runs the whole repository's checks with `--warn-error`.
- **Ad-spend attribution:** it goes by the session's day.
- **`fct_leads`:** it drops the always-null `utm_campaign`.

**`CLAUDE.md` is binding.**

**Base:** `main` **after ui-3 (column lineage) and the UI test audit have merged**. Rebase this branch onto `main` before Task 1. ui-3's layout already has an exposure band, so the first exposure lands in a layout that expects it. If the MCP test audit has merged by then, Task 4's tests follow its style: literal tuples, no substring checks.

**Model selection:** every task names its implementer and reviewer models. Dispatch exactly those. Fix rounds 4–5 go one tier up. The final whole-branch review is Task 9's panel.

## Global Constraints

- **Mart rules (spec §6.5):**
  - test accounts (R5) excluded
  - PII of deleted customers (R6) null
  - `contract: enforced` with `data_type` on every column, and `not_null` plus `primary_key` constraints on the key
  - every model and column described
  - `meta: {domain, grain, primary_key}`, with `meta.pii: true` on PII columns and `meta.synonyms` on business terms
- **Types:**
  - money is `numeric(12,2)` (EUR)
  - counts are `integer`, and `0` when there is nothing (never null)
  - timestamps are `timestamptz` (UTC) and dates are UTC dates
- **Tests:**
  - every FK in marts has a `relationships` test to the mart it names, and every enum column an `accepted_values` test
  - unit-test names are at most 63 characters
- **dbt layout:** YAML per directory (`_<dir>__models.yml`). sqlfluff (dbt templater, Postgres) must pass, with the style of the existing mart SQL (CTEs, a `final` select with explicit casts).
- **Shared warehouse:**
  - tests only read `sherpa` and `sherpa_eval`
  - `make build-prod` and `make eval-db` are builds, not tests
  - ask the owner before `make eval-db` (it rebuilds the frozen eval database the agent's gold set reads) and before `make advance`
- **Public repository:** the workflow carries no secrets, no corporate host names and no proxy settings. Each GitHub action is pinned to a full commit SHA, with its version tag in a comment.
- **Make targets:** one new public target, `make slim`. No knobs or aliases.
- **Every commit** leaves `make lint`, `make test` and `cd ui/frontend && npm test` green. Commit as `Josep Martinez <57620224+josepmafe@users.noreply.github.com>`.

## Review Focus

1. **The empty run poisons the data build.** `dbt run --empty` leaves the incremental models existing but empty. A following plain `dbt build` then runs them incrementally: microbatch loads only today and the 7 days before, and the sessionizer's watermark is `-infinity` over no rows. So CI's 30 days would be part-loaded, and `assert_every_source_event_is_staged` fails. CI's data build needs `--full-refresh`. Test: the first real CI run (Task 7, Step 3) is green.
2. **A deleted customer's email leaks through `fct_leads`.** `dim_customers` nulls it, but the CRM still has the lead's email. Tests: the unit test `fct_leads_deleted_customer_lead_has_no_email` and the extended `assert_deleted_customers_are_anonymized` (Task 3).
3. **Email-campaign acquisitions vanish from campaign totals.** Someone sums `fct_ad_spend_daily.new_customers` for "new customers by campaign" and misses the 51 customers email campaigns acquired. Tests:
   - `assert_ad_spend_attribution_is_complete` is scoped to paid campaigns (Task 2)
   - the `get_context` note on paid-only ad spend is asserted as a literal (Task 4)
4. **`make slim` against missing or matching state.**
   - Without `target-prod/manifest.json`, dbt fails deep inside deferral. Make must stop first and name `make build-prod`.
   - Right after `make build-prod` there must be nothing to build: an `env_var` in a config could mark every model modified.
   - Tests: Task 6, Steps 4–5.
5. **A default-suite test that needs a gitignored artifact** (`target-prod/`, `target/`, a filled `node_modules/.cache`) passes on every laptop and fails in CI. Test: the first real CI run (Task 7, Step 3), on a runner with none of them.

---

## Environment (before Task 1)

The worktree has none of the gitignored files (`CLAUDE.md`, Environment notes):
1. Copy `.env` from the main checkout.
2. Run `UV_LINK_MODE=copy uv sync` and `cd ui/frontend && npm ci`.
3. Run `make build-prod` once.

If `make build-prod` stops at the source-freshness gate (data older than 72 h), ask the owner whether to run `make advance DAYS=<n>`. dp-4's warehouse tests need a current prod build either way.

## File Structure

```
data-platform/dbt/
  dbt_project.yml                          # + group/access folder configs
  selectors.yml                            # new: the `ci` selector
  models/_groups.yml                       # new: five groups, their owners
  models/marts/_exposures.yml              # new: sherpa_agent
  models/marts/_metrics.md                 # + metric_roas, metric_cac
  models/marts/marketing_web/dim_campaigns.sql, fct_ad_spend_daily.sql   # new
  models/marts/customers/fct_leads.sql                                   # new
  models/marts/{marketing_web,customers,sales,products}/_*__models.yml   # new marts, unit tests, FK targets, tags
  models/intermediate/identity/_identity__models.yml                     # per-model group/access
  tests/singular/assert_ad_spend_attribution_is_complete.sql             # new
  tests/singular/assert_revenue_reconciles_with_raw.sql                  # new
  tests/singular/assert_agent_exposure_lists_every_public_mart.sql       # new
  tests/singular/assert_deleted_customers_are_anonymized.sql, assert_no_test_accounts_in_marts.sql,
    assert_buyer_revenue_pareto.sql                                      # extended / tagged
data-platform/tests/dbt/test_reader_access.py   # MARTS + the three marts
data-platform/tests/dbt/test_selectors.py       # new: the distribution tag
agent/mcp-server/src/sherpa_mcp/tools.py        # DOES_NOT_COVER, DOMAIN_SUBJECTS, NOTES
agent/mcp-server/tests/test_tools.py
Makefile                                        # + slim
.github/workflows/ci.yml                        # new
```

---

### Task 1: `dim_campaigns`

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `models/marts/marketing_web/dim_campaigns.sql`, `models/marts/marketing_web/_marketing_web__models.yml`; the `relationships` targets in `models/marts/customers/_customers__models.yml:123`, `models/marts/marketing_web/_marketing_web__models.yml:129`, `models/marts/sales/_sales__models.yml:44`; `data-platform/tests/dbt/test_reader_access.py` (`MARTS`).

**Interfaces — Produces:** the relation `marts.dim_campaigns`, with PK `campaign_id bigint` and columns `campaign_name text`, `utm_campaign text`, `channel text`, `starts_at timestamptz`, `ends_at timestamptz` and `budget numeric(12,2)`.

- [ ] **Step 1: Model and YAML.**
  - **Model:** `dim_campaigns` selects `stg_marketing__campaigns` with the spec's column names and explicit casts.
  - **YAML:**
    - meta `domain: marketing_web`, `grain: one row per marketing campaign`, `primary_key: campaign_id`
    - synonyms: model `[campaigns, marketing campaigns]`, `budget` `[planned spend]`
    - `channel`: `accepted_values` `[paid_search, paid_social, email, affiliate, display]`, and its description says email campaigns have no ad spend
    - `utm_campaign`: `unique` and `not_null`
- [ ] **Step 2: Mart-to-mart FKs.** `dim_customers.acquisition_campaign_id`, `fct_sessions.campaign_id` and `dim_promotions.campaign_id` point their `relationships` at `ref('dim_campaigns')` instead of staging.
- [ ] **Step 3:** add `"dim_campaigns"` to `MARTS` in `test_reader_access.py` (sorted).
- [ ] **Step 4: Verify.** `make build` (all green), `make lint`, `make test`.
- [ ] **Step 5: Commit** `feat: dim_campaigns, and the campaign FKs of the marts point at it`.

---

### Task 2: `fct_ad_spend_daily`, ROAS and CAC

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `models/marts/marketing_web/fct_ad_spend_daily.sql`, `_marketing_web__models.yml`, `models/marts/_metrics.md`, `tests/singular/assert_ad_spend_attribution_is_complete.sql`, `test_reader_access.py`.

**Interfaces:**
- **Consumes:** `dim_campaigns` (Task 1); `stg_marketing__ad_spend_daily`, `fct_sessions`, `fct_orders`.
- **Produces:** `marts.fct_ad_spend_daily`, with PK (`campaign_id`, `spend_date`), and the doc blocks `metric_roas` and `metric_cac`.

- [ ] **Step 1: Unit tests first** (in `_marketing_web__models.yml`, `model: fct_ad_spend_daily`; inputs as dicts, ephemeral ones as `format: sql`). Each test names its inputs' rows and expects exact rows:
  - `fct_ad_spend_daily_order_after_midnight_counts_on_session_day`: a session of campaign 1 starts on 2025-03-01 at 23:50, and its order is placed on 2025-03-02 at 00:10, for €40.00 net. The 03-01 row has `sessions = 1`, `orders = 1` and `attributed_net_revenue = 40.00`; the 03-02 row has 0, 0 and 0.00.
  - `fct_ad_spend_daily_cancelled_order_adds_nothing`: two sessions of campaign 1 on one day. One has a cancelled order (€30.00 placed); the other a customer's first order (€25.00 net). Expected: `sessions = 2`, `orders = 1`, `attributed_net_revenue = 25.00` and `new_customers = 1`. A model that dropped orders altogether, or kept the cancelled one, fails it.
  - `fct_ad_spend_daily_two_campaigns_on_one_day_stay_apart`: campaigns 1 and 2 on the same day, with 2 and 1 sessions. Each row counts its own.
  - `fct_ad_spend_daily_first_order_counts_one_new_customer`: one customer with a first order and a repeat order in campaign 1's sessions that day gives `orders = 2` and `new_customers = 1`.
  - An email campaign's session on that day appears in no row: add it to the inputs of the two-campaigns test.

  Run `dbt test --select fct_ad_spend_daily,test_type:unit`. Expected: compilation fails, because the model does not exist yet.
- [ ] **Step 2: Model.**
  - **Rows:** one per `stg_marketing__ad_spend_daily` row, with `channel` from `dim_campaigns`.
  - **Counts:** they come from `fct_sessions` grouped by (`campaign_id`, `session_date`):
    - `sessions`: the count of those sessions
    - `orders` and `attributed_net_revenue`: the non-cancelled `fct_orders` joined on `session_id`
    - `new_customers`: those orders with `is_first_order`
  - **Missing activity:** `coalesce(…, 0)` where a spend day has none.
  - **Types:** `spend` and `attributed_net_revenue` are `numeric(12,2)`; `impressions` and `clicks` keep staging's integer type; the counts are `integer`.
- [ ] **Step 3: YAML.**
  - **Meta:** `domain: marketing_web`; `grain: one row per paid campaign and day with spend`; `primary_key: [campaign_id, spend_date]`.
  - **Contract:** a model-level `primary_key` constraint on both columns, plus `dbt_utils.unique_combination_of_columns` (spec §7).
  - **Tests:** `relationships` from `campaign_id` to `dim_campaigns`; `dbt_utils.expression_is_true` with `new_customers <= orders`.
  - **Descriptions:**
    - the model: paid campaigns only (email campaigns have no rows), every column belongs to the campaign's sessions that started on `spend_date`, and `clicks` are the platform's, not tracked sessions (R11)
    - `attributed_net_revenue`: `{{ doc('metric_roas') }}`
    - `new_customers` and `spend`: `{{ doc('metric_cac') }}`
  - **Synonyms:**
    - model `[ad spend, marketing spend, paid media]`
    - `spend` `[cost, ad cost, media spend]`
    - `attributed_net_revenue` `[attributed revenue, campaign revenue]`
    - `new_customers` `[acquisitions, new buyers]`
- [ ] **Step 4: Doc blocks.** Add `metric_roas` and `metric_cac` in `_metrics.md`, worded from spec §6.6, each saying paid campaigns only.
- [ ] **Step 5: Singular test** `assert_ad_spend_attribution_is_complete`. Per paid campaign (`channel <> 'email'`), compare:
  - Σ `sessions` with the count of its `fct_sessions`
  - Σ `orders` and Σ `attributed_net_revenue` with its sessions' non-cancelled `fct_orders`
  - Σ `new_customers` with the `dim_customers` rows whose `acquisition_campaign_id` is it

  It returns one row per campaign and measure that differ. The `new_customers` comparison derives from `dim_customers`, independently of the model's `is_first_order`. Check that it fails: change the model's join to `order_date` in a throwaway edit, run it, and see the campaign rows it returns. Then revert.
- [ ] **Step 6:** add `"fct_ad_spend_daily"` to `MARTS`. Then run `make build`, `make lint` and `make test`.
- [ ] **Step 7: Commit** `feat: fct_ad_spend_daily with session-day attribution, ROAS and CAC definitions`.

---

### Task 3: `fct_leads`

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `models/marts/customers/fct_leads.sql`, `_customers__models.yml`, `tests/singular/assert_deleted_customers_are_anonymized.sql`, `tests/singular/assert_no_test_accounts_in_marts.sql`, `test_reader_access.py`.

**Interfaces:**
- **Consumes:** `stg_crm__leads`, `stg_crm__email_sends`, `dim_customers` (`lead_id`, `customer_id`, `signup_at`, `is_deleted`).
- **Produces:** `marts.fct_leads`, with PK `lead_id`.

- [ ] **Step 1: Unit tests first:**
  - `fct_leads_unconverted_lead_has_nulls_and_zero_counts`: no customer has the lead's `lead_id`, and it has no sends. Expected: `is_converted = false`; `converted_customer_id`, `converted_at` and `days_to_convert` null; the three counts `0`.
  - `fct_leads_converted_lead_counts_days_and_emails`: lead 1 is created on 2025-02-01 at 10:00, and its customer has `lead_id = 1` and `signup_at` 2025-02-11 09:00. Its three sends are opened on 2 and clicked on 1. Lead 2's customer signs up on the lead's own day, 2025-02-01 23:00. Expected:
    - lead 1: `days_to_convert = 10`; its `email` is kept; `emails_sent = 3`, `emails_opened = 2`, `emails_clicked = 1`
    - lead 2: `days_to_convert = 0` (the boundary of the `accepted_range`)
  - `fct_leads_deleted_customer_lead_has_no_email`: the customer has `is_deleted = true`. Expected: `email` null, `is_converted = true`.
- [ ] **Step 2: Model.**
  - **Join:** leads left join `dim_customers` on `dim_customers.lead_id = leads.lead_id`. That is the whole conversion rule (spec §6.5), so a test account never converts.
  - **Converted columns:** `is_converted` = a customer was found; `converted_at` = `signup_at`; `days_to_convert` = `signup_at::date - created_at::date`.
  - **`email`:** null when the customer is deleted.
  - **Counts:** from `stg_crm__email_sends`, using `count(*)`, `count(opened_at)` and `count(clicked_at)`.
  - **Left out:** `utm_campaign` (spec §6.5).
- [ ] **Step 3: YAML.**
  - **Meta:** `domain: customers`; `grain: one row per CRM lead`; `primary_key: lead_id`; `email` gets `meta.pii: true`.
  - **Column tests:**
    - `accepted_values` on `lead_source` and on `lifecycle_stage`
    - `relationships` from `converted_customer_id` to `dim_customers` (where not null)
    - `dbt_utils.accepted_range` on `days_to_convert` with `min_value: 0`
  - **Expression tests** (`dbt_utils.expression_is_true`):
    - `is_converted = (converted_customer_id is not null)`
    - `lifecycle_stage <> 'customer' or is_converted`
    - `emails_opened <= emails_sent`
    - `emails_clicked <= emails_sent`
  - **Descriptions:** `lifecycle_stage` says the CRM's stage can lag behind `is_converted`.
  - **Synonyms:** model `[leads, subscribers, prospects]`, `is_converted` `[converted, became a customer]`.
- [ ] **Step 4: Extend the two singular tests.**
  - **`assert_deleted_customers_are_anonymized`:** also return the `fct_leads` rows whose converted customer is deleted and whose `email` is not null.
  - **`assert_no_test_accounts_in_marts`:** add `fct_leads.converted_customer_id` to `mart_customers`.
  - Check that the first fails without the model's nulling (a throwaway edit), then revert.
- [ ] **Step 5:** add `"fct_leads"` to `MARTS`. Then run `make build`, `make lint` and `make test`.
- [ ] **Step 6: Commit** `feat: fct_leads, converted through dim_customers, deleted customers anonymized`.

---

### Task 4: What the agent is told

**Model:** implementer `haiku` · reviewer `sonnet`

**Files:** `agent/mcp-server/src/sherpa_mcp/tools.py` (`DOES_NOT_COVER`, `DOMAIN_SUBJECTS`, `NOTES` and the comment above `DOES_NOT_COVER`), `agent/mcp-server/tests/test_tools.py`.

- [ ] **Step 1: Tests first,** in the MCP test audit's style: whole literal tuples, no substring checks.
  - **If the audit's literal tests of `does_not_cover` and `notes` exist:** change their expected tuples.
  - **Otherwise:** replace `test_does_not_cover_campaign_entry_says_sessions_carry_the_utm_slug` with these two:
    - `test_does_not_cover_is_the_specs_list`: `DOES_NOT_COVER` equals a literal tuple, the inventory, reviews, currencies and app-visits entries as they read today (agent spec §4.1)
    - `test_notes_are_the_specs_list`: `NOTES` equals a literal tuple, today's eight notes plus `"fct_ad_spend_daily covers paid campaigns only: email campaigns spend nothing, so their sessions, orders and new customers are in fct_sessions, fct_orders and dim_customers but not in fct_ad_spend_daily"`

  `DOMAIN_SUBJECTS` is prompt copy with no spec list, so it gets no test. Run the tests. Expected: FAIL.
- [ ] **Step 2: Implement.**
  - **`DOES_NOT_COVER`:** drop the campaign and lead entries, and the comment's "change when data-platform-4" sentence.
  - **`DOMAIN_SUBJECTS`:**
    - `customers` gains "CRM leads and their conversion to customers"
    - `marketing_web` gains "campaigns, and daily ad spend of paid campaigns with their sessions, orders, revenue and new customers"
  - **`NOTES`:** add the note.
- [ ] **Step 3:** `make test`; `make lint`. Commit `feat: get_context covers campaigns, ad spend and leads`.

---

### Task 5: Groups, access, the exposure and the raw reconciliation

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `models/_groups.yml`, `dbt_project.yml`, `models/intermediate/identity/_identity__models.yml`, `models/marts/_exposures.yml`, `tests/singular/assert_agent_exposure_lists_every_public_mart.sql`, `tests/singular/assert_revenue_reconciles_with_raw.sql`.

- [ ] **Step 1: Groups.** Define five groups, each with an `owner: {name: …}`:
  - `sales`: "Sales analytics"
  - `customers`: "CRM analytics"
  - `products`: "Merchandising analytics"
  - `marketing_web`: "Marketing analytics"
  - `shared`: "Data platform"
- [ ] **Step 2: Access** (spec §8), as folder configs in `dbt_project.yml`:
  - **Marts:** `marts/<domain>` gets `+group: <domain>` and `+access: public`.
  - **Intermediate:**
    - `intermediate/orders` gets `+group: sales` and `+access: private`
    - `intermediate/events` gets `+group: marketing_web` (access stays protected)
    - in `intermediate/identity`, per model in its YAML: `int_identity__lead_to_customer` is `group: customers`, `access: private`; `int_identity__anonymous_to_customer` is `group: marketing_web`
  - **Staging:** untouched.
  - **Verify:** run `dbt parse`. Then a throwaway model in `marts/marketing_web` that refs `int_orders__status_pivoted` must fail parse with dbt's private-access error. Quote that error in the task report, then delete the model.
- [ ] **Step 3: Exposure test first.** `assert_agent_exposure_lists_every_public_mart`, at execution time (`{% if execute %}`), compares two sets:
  - the `unique_id`s of `graph.nodes` with `resource_type == 'model'` and `access == 'public'`
  - `graph.exposures['exposure.sherpa.sherpa_agent'].depends_on.nodes`

  It returns one row (`unique_id`, `problem`) per mart missing from the exposure (`'not in sherpa_agent'`) and per exposure dependency that isn't a public model (`'not a public mart'`), or no rows (`select … where false`). Run it. Expected: an error, because the exposure doesn't exist yet.
- [ ] **Step 4: Exposure.** In `_exposures.yml`, `sherpa_agent`:
  - `type: application`, `owner: {name: Data platform}`
  - a description of the MCP server and the agent (CLI, the UI's Ask), citing data-platform spec §11
  - `depends_on`: `ref()` to all 13 marts

  Check: the test passes. With one `ref` removed it returns that mart, and you revert.
- [ ] **Step 5: Reconciliation test.** `assert_revenue_reconciles_with_raw` reads the raw tables through `source()`, not through staging:
  - **Orders, per UTC day of `placed_at`:** the shop's non-cancelled orders of non-test customers (Σ `subtotal`, Σ `discount_total`, the order count) against non-cancelled `fct_orders` by `order_date` (Σ `gross_revenue`, Σ `discount_amount`, count).
  - **Refunds, per UTC day of `refunded_at`:** the raw `refunds` of non-test customers (Σ `amount`) against `fct_refunds` by `refund_date` (Σ `amount`).

  It returns (`day`, `measure`, `raw_value`, `mart_value`) where they differ. Check it against a throwaway change to `fct_orders.gross_revenue` (e.g. `+ 0.01`), then revert.
- [ ] **Step 6: Verify.**
  - `make build`, `make lint`, `make test`.
  - Then `make build-prod`, and `uv run --env-file .env pytest -m warehouse agent/mcp-server ui/backend data-platform/tests`: the MCP server's public-access rule is now active and must still list all 13 marts.
  - **The MCP fixture manifest** (`agent/mcp-server/tests/fixtures/manifest.json`): give its mart nodes `access: public`, as the real manifest now has, so the default suite runs the rule prod runs. Then `make test`.
  - Open the dbt docs (`make docs`) and Lineage (`/lineage?select=+exposure:sherpa_agent`). The exposure has every mart upstream, in ui-3's exposure band. Lineage needs `make up` from this worktree, which replaces the running `ui` and `mcp` containers with this checkout's build; `make up` from the main checkout afterwards puts them back.
- [ ] **Step 7: Commit** `feat: groups and access per domain, the sherpa_agent exposure, raw reconciliation test`.

---

### Task 6: The `distribution` tag, the `ci` selector and `make slim`

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:**
- `data-platform/dbt/selectors.yml`
- the YAML of the distribution tests: `_products__models.yml:14`, `_sales__models.yml:208`, and every other `dbt_expectations.expect_table_row_count_to_be_between` or `expect_column_mean_to_be_between` that `grep` finds
- `tests/singular/assert_buyer_revenue_pareto.sql`
- `data-platform/tests/dbt/test_selectors.py`
- `Makefile`

- [ ] **Step 1: Test first.** `test_selectors.py::test_distribution_tag_is_exactly_the_distribution_checks` uses the session `dbt` fixture of `data-platform/tests/conftest.py`, with the scratch target path the other dbt tests use, so `target/` is untouched:
  - `dbt ls --resource-type test --output name` gives every test
  - `dbt ls --select tag:distribution --output name` gives the tagged ones

  The tagged set must equal the tests whose names start with `dbt_expectations_expect_table_row_count_to_be_between` or `dbt_expectations_expect_column_mean_to_be_between`, plus `assert_buyer_revenue_pareto`, which is the spec §8 definition. It must also be non-empty. Unmarked: it needs `.env`, not Postgres. Run it. Expected: FAIL, because nothing is tagged yet.
- [ ] **Step 2: Tag.** Add `config: {tags: [distribution]}` on each of those tests, and `tags=['distribution']` in the Pareto test's `config()`.
- [ ] **Step 3: Selector.** In `selectors.yml`, `ci` = everything except `tag:distribution`, with a `description` saying why (spec §8). Check that `dbt ls --selector ci --resource-type test` leaves exactly the tagged tests out.
- [ ] **Step 4: `make slim`.**
  - **Recipe:** `$(DBT) build --select state:modified+ --defer --state target-prod`, on dev.
  - **Start date:** add `slim` to the line that exports `SHERPA_SIMULATION_START_DATE` (`build build-prod slim: export …`).
  - **Guard:** before dbt runs, `$(if $(wildcard data-platform/dbt/target-prod/manifest.json),,$(error make slim defers to prod: run make build-prod first))`.
  - **Makefile:** add it to `.PHONY`, with a one-line comment.
- [ ] **Step 5: Verify `make slim`** (Review Focus 4), recording each output's summary line in the report:
  - **(a) Matching state:** right after `make build-prod`, `make slim` builds nothing. If it selects nodes, find the config that differs between the prod manifest and dev, and report it before changing anything.
  - **(b) One change:** after a comment change in `dim_campaigns.sql`, it builds exactly what `dbt ls --select dim_campaigns+` lists (`dim_campaigns`, `fct_ad_spend_daily` and the tests on them and on the FKs that point at it). Revert the change.
  - **(c) Missing state:** with `target-prod/manifest.json` renamed (this worktree's only), it stops with the guard's message. Restore the file.
- [ ] **Step 6:** `make lint`, `make test`. Commit `feat: distribution tag and ci selector; make slim builds what changed, deferring to prod`.

---

### Task 7: GitHub Actions

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `.github/workflows/ci.yml`.

- [ ] **Step 1: Workflow** `ci`, on `pull_request` to `main`:
  - **Settings:**
    - `permissions: contents: read`
    - `concurrency` per ref with `cancel-in-progress: true`
    - one job `checks` on `ubuntu-24.04` with `timeout-minutes: 30`
  - **Steps, in order:**
    1. **Toolchains:** `actions/checkout`; `astral-sh/setup-uv` with Python 3.12; `actions/setup-node` with Node 22 and the npm cache on `ui/frontend/package-lock.json`. Each `uses:` is pinned to a full commit SHA, its tag in a comment.
    2. **Environment:** `cp .env.example .env`. The `change-me` passwords are fine for a throwaway container.
    3. **Postgres:** `docker compose up -d --wait postgres`. The init script creates the roles, as on a new local volume.
    4. **Dependencies:** `uv sync --locked`; `cd ui/frontend && npm ci`.
    5. **Data:** `uv run --env-file .env python data-platform/scripts/backfill.py --days 30 --scale 0.05`. Then write `SHERPA_SIMULATION_START_DATE=$(uv run sherpa-generate start-date)` to `$GITHUB_ENV`.
    6. **dbt:** from `data-platform/dbt` with `DBT_PROFILES_DIR=.` and `uv run --env-file ../../.env dbt`, run `deps`, `seed`, `run --empty`, then `build --full-refresh --selector ci --warn-error` (Review Focus 1).
    7. **Checks:** `make lint`, `make test`, `cd ui/frontend && npm test`.
- [ ] **Step 2: Commit** `ci: pull-request gate (tiny-data dbt build with warnings as errors, lint, tests)`.
- [ ] **Step 3: First real run** (Review Focus 1 and 5). Ask the owner, then push the branch and open the pull request as a draft. The runner is a fresh checkout with no gitignored file, so this run is the test: a local copy of it would check the same things less faithfully.
  - CI must be green; fix and push until it is.
  - A warning that only tiny data raises is reported to the owner, not silenced.
  - Report the job's duration.
  - Remind the owner to make `checks` a required status check on `main`. That is their GitHub setting.

---

### Task 8: Documentation

**Model:** implementer `sonnet` · reviewer `haiku`

- [ ] **Step 1: `CLAUDE.md`.**
  - **Source of truth:**
    - data-platform-4 joins the done plans
    - the current plan line, and sequencing
  - **Commands:**
    - `make slim` under dbt (needs `make build-prod`)
    - a CI line under Checks: what the gate runs; warehouse tests, Playwright and `live` stay local
  - **Conventions:**
    - a new model joins its domain's group: a mart is `public`; an intermediate used by one group is `private`
    - a new mart goes into the `sherpa_agent` exposure and `MARTS`
  - **Tests:** a generator-distribution test is tagged `distribution`; nothing else is.
- [ ] **Step 2: `README.md`.**
  - the status line
  - Stack: GitHub Actions is no longer *planned*
  - Develop: `make slim`, and that CI runs on every pull request
  - layout: `.github/`
  - the marts sentence names the marketing marts
- [ ] **Step 3: Guides** (short: basic ≤ ~150 lines, advanced ≤ ~250; link, don't repeat; one symptom → fix table each).
  - **`basic/querying-the-marts.md`:**
    - **Campaigns and ad spend:** two copy-pasteable queries, ROAS by channel last month and CAC by campaign.
    - **Leads:** the lead-to-customer conversion rate by `lead_source`.
    - **Traps:** email campaigns aren't in `fct_ad_spend_daily`; clicks aren't sessions (R11); `lifecycle_stage` lags `is_converted`.
  - **`basic/building-the-warehouse.md`:** a `make slim` section: when to use it, the "run `make build-prod` first" error, and nothing to build after a fresh prod build.
  - **`advanced/adding-or-changing-a-mart.md`:** groups and access for a new model, the exposure and `MARTS`, and when to tag a test `distribution`.
  - **New `advanced/ci.md`** (CI is a new thing people extend; under ~100 lines):
    - what the gate runs and why (link spec §8)
    - reproducing a failing step locally: `make lint` and `make test` as usual, and the dbt step as `dbt build --full-refresh --selector ci --warn-error` on dev
    - adding a check
    - `--warn-error`, and why the `distribution` tag exists
  - Link it from the README's guide list.
  - **`basic/using-the-mcp-server.md`:** update its "what it can't answer" text if it repeats `does_not_cover`.
- [ ] **Step 4:** run `make lint`, then commit `docs: marketing marts, governance, make slim and CI`.

---

### Task 9: Panel review

**Model:** every reviewer and the devil's advocate on `opus`, dispatched by the controller (`CLAUDE.md`). Fixes go through the usual loop.

- [ ] **Step 1: Reviewers**, in parallel. Each gets `git diff main...HEAD`, this plan, its focus and the spec sections named:
  - **Data engineer** (spec §6.5, §7, §8):
    - the three marts' SQL, contracts, tests and unit tests
    - groups and access against the refs (would a future mart need a `private` model?)
    - the exposure test's use of `graph`
    - `make slim`'s state comparison
    - CI's dbt sequence
  - **Analytics domain expert** (spec §6.5, §6.6):
    - attribution by session day
    - CAC and ROAS on paid campaigns only
    - the conversion rule against the CRM's `lifecycle_stage`
    - whether the guides' example queries answer what they claim
  - **Software engineer** (`CLAUDE.md`):
    - the workflow (pinning, permissions, caching, duration, flakiness)
    - the Makefile target
    - `test_selectors.py`
    - whether the CI's commands match what `make build`, `make lint` and `make test` do locally
  - **Security engineer:**
    - `fct_leads.email` and R6
    - what the MCP server and UI roles can now read (`test_reader_access.py`)
    - the workflow on a public repository (fork pull requests, token permissions, nothing secret)
  - **AI engineer** (agent spec §4.1): the new `does_not_cover`, `DOMAIN_SUBJECTS` and `NOTES` texts, and the marts' synonyms and descriptions, from the view of an agent answering "CAC by channel" or "how many leads converted".
- [ ] **Step 2: Devil's advocate.** Give it the diff and every finding. It challenges wrong or over-built findings, and adds the failures the panel missed: what passes CI and still breaks prod, and which test restates the code.
- [ ] **Step 3: Triage and fix.** Triage every finding (fix, defer or reject), then fix and re-verify:
  - `make lint`, `make test`, `npm test`
  - `make build-prod`, then `pytest -m warehouse`, after asking the owner to run `make eval-db`, because `test_reader_access` covers the eval database too
  - CI green

  The triage table goes in the PR description.
