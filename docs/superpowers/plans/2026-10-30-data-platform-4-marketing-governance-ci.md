# data-platform-4 — Marketing, Governance, CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the marketing marts (`dim_campaigns`, `fct_ad_spend_daily`, and `fct_leads` as a PII pair), govern the project with groups, access and the `sherpa_agent` exposure, add `make slim`, and make GitHub Actions the repository's pull-request gate.

**Architecture:** the new marts are models in the existing `marts/` folders, built from staging and other marts. `fct_leads` follows the PII pattern of `dim_customers`: a `fct_leads_unmasked` table that no reader can query, and a `fct_leads` view that hashes `email`. dbt unit tests pin the logic, and singular tests reconcile the new marts with the marts they summarise. Groups and access are folder configs in `dbt_project.yml`. A singular test reads dbt's `graph` and keeps the exposure equal to the visible marts. CI starts the same compose Postgres you run locally, builds tiny data on the `dev` target with warnings as errors, then runs `make lint`, `make test` and Vitest.

**Tech Stack:** dbt Core 1.12 (groups, access, exposures, YAML selectors, `state:modified`, `--defer`, `--empty`, `--warn-error`, unit tests), dbt_utils, Postgres 16, GitHub Actions, uv, npm.

**Spec:** `docs/superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md`:
- §6.5: "PII masking", "Checks", `dim_campaigns`, `fct_ad_spend_daily`, `fct_leads`
- §6.6 ROAS, CAC
- §7 (the new singular and unit tests)
- §8: data-platform-4 (the features, CI, acceptance), "Added by ui-1" (the audit table's `message`), "Added by the PII masking change"
- §9 (`dbt clone` excluded)
- §10 (`make slim`)

Also agent spec §3 (visible marts), §4.1 (`get_context`), §4.2 and §9 (CI); UI spec §6.2 (`sherpa_home` comes with Home); and the guide `docs/guides/advanced/adding-or-changing-a-mart.md` §8 (the steps to add a PII mart).

**This plan's commit changes the specs:**
- **Removed:** `make clone` and the `ci` target.
- **Moved:** `sherpa_home` goes to the Home milestone, because an exposure is declared by the milestone that builds its consumer.
- **Revenue reconciliation:** it becomes a singular test instead of an analysis.
- **CI:** it runs the whole repository's checks with `--warn-error`.
- **Ad-spend attribution:** it goes by the session's day.
- **`fct_leads`:** it is the PII pair, without the always-null `utm_campaign`.
- **The masking checks** gain two rules (no model but the sibling view reads an `_unmasked` table; the hash is exactly `mask_pii` of the real value), and the audit table keeps only the first line of dbt's message. These are the items PR #20's review deferred to this milestone.

**`CLAUDE.md` is binding.**

**Base:** `main` at `dea6d76` (#20, PII masked views). ui-3, the UI test audit and the MCP test audit are merged. ui-3's layout has an exposure band, so the first exposure lands in a layout that expects it.

**Model selection:** every task names its implementer and reviewer models. Dispatch exactly those. Fix rounds 4–5 go one tier up. The final whole-branch review is Task 10's panel.

## Global Constraints

- **Mart rules (spec §6.5):**
  - test accounts (R5) excluded
  - PII of deleted customers (R6) null
  - `contract: enforced` with `data_type` on every column, and the key's `not_null` and `primary_key` constraints on the table
  - every model and column described
  - `meta: {domain, grain, primary_key}`, with `meta.synonyms` on business terms
- **A mart with PII is a pair (spec §6.5, guide §8):**
  - **`<mart>_unmasked`:** a table with `grants: {select: []}` (never `+select`), `meta.pii: true` on its PII columns, and no data tests
  - **`<mart>`:** a plain view (not `security_invoker`) with the table's columns in its order. Each PII column it keeps is `{{ mask_pii('<column>') }} as <column>`, with `meta.masked: true` and the pseudonymised description.
  - **No visible mart carries `meta.pii`.** The catalog refuses it.
  - **Reading the table:** only the sibling view refs `<mart>_unmasked`. Every other model refs the view, and joins on keys, never on a hash.
  - **Rebuilding:** build the table with its view: `--select <mart>_unmasked+`. The table alone drops the view.
  - **Singular tests:** one that reads an `_unmasked` table returns no value of its PII columns. From Task 6, it is in that table's group.
- **Types:**
  - money is `numeric(12,2)` (EUR)
  - counts are `integer`, and `0` when there is nothing (never null)
  - timestamps are `timestamptz` (UTC) and dates are UTC dates
- **Tests:**
  - every FK in marts has a `relationships` test to the mart it names, and every enum column an `accepted_values` test
  - unit-test names are at most 63 characters
- **dbt layout:** YAML per directory (`_<dir>__models.yml`). sqlfluff (dbt templater, Postgres) must pass, with the style of the existing mart SQL (CTEs, a `final` select with explicit casts).
- **SQL written for readers** (the guides' example queries) must pass the guard (agent spec §4.2):
  - casts only to `CAST_TYPES`
  - no `lpad`, `rpad` or `repeat`, and no `(expr).name`
  - no hash literal
  - no comparison, pattern, string function or sort on a hashed column
  - no customer names
- **Shared warehouse:**
  - tests only read `sherpa` and `sherpa_eval`
  - `make build-prod` and `make eval-db` are builds, not tests
  - ask the owner before `make eval-db` (it rebuilds the frozen eval database the agent's gold set reads, with a new salt) and before `make advance`
- **Public repository:** the workflow carries no secrets, no corporate host names and no proxy settings. Each GitHub action is pinned to a full commit SHA, with its version tag in a comment.
- **Make targets:** one new public target, `make slim`. No knobs or aliases.
- **Every commit** leaves `make lint`, `make test` and `cd ui/frontend && npm test` green. Commit as `Josep Martinez <57620224+josepmafe@users.noreply.github.com>`.

## Review Focus

1. **The empty run poisons the data build.** `dbt run --empty` leaves the incremental models existing but empty. A following plain `dbt build` then runs them incrementally: microbatch loads only today and the 7 days before, and the sessionizer's watermark is `-infinity` over no rows. So CI's 30 days would be part-loaded, and `assert_every_source_event_is_staged` fails. CI's data build needs `--full-refresh`. Test: the first real CI run (Task 8, Step 3) is green.
2. **A real email reaches a reader.** It has three ways out:
   - a `fct_leads` view that doesn't hash, or hashes without the salt
   - a model that reads `fct_leads_unmasked` and republishes the email
   - dbt's message of a constraint violation on the table: `DETAIL: Failing row contains (…, <email>, …)` goes into `ops.dbt_run_results.message`, which `sherpa_ui` reads

   Tests:
   - `test_audit_message_keeps_only_the_first_line` (Task 1)
   - the exact-hash and "reads an `_unmasked` table" arms of `assert_pii_marts_are_masked_views` (Task 4)
   - `TestMaskedMarts` with `fct_leads` registered (Task 4)
3. **Email-campaign acquisitions vanish from campaign totals.** Someone sums `fct_ad_spend_daily.new_customers` for "new customers by campaign" and misses the 51 customers email campaigns acquired. Tests:
   - `assert_ad_spend_attribution_is_complete` is scoped to paid campaigns (Task 3)
   - the `get_context` note on paid-only ad spend is asserted as a literal (Task 5)
4. **`make slim` against missing or matching state.**
   - Without `target-prod/manifest.json`, dbt fails deep inside deferral. Make must stop first and name `make build-prod`.
   - Right after `make build-prod` there must be nothing to build: an `env_var` in a config could mark every model modified.
   - Tests: Task 7's steps 4–5.
5. **A default-suite test that needs a gitignored artifact** (`target-prod/`, `target/`, a filled `node_modules/.cache`) passes on every laptop and fails in CI. Test: the first real CI run (Task 8, Step 3), on a runner with none of them.

---

## Environment (before Task 1)

The worktree has none of the gitignored files (`CLAUDE.md`, Environment notes):
1. Copy `.env` from the main checkout.
2. Run `UV_LINK_MODE=copy uv sync` and `cd ui/frontend && npm ci`.
3. Check `git merge-base --is-ancestor dea6d76 HEAD`. All checkouts share one warehouse, and a prod command (`make build-prod`, `make eval-db`, `make advance`, `pytest -m warehouse`) run from a branch without #20 replaces the masked view with a raw table and grants it to the readers.
4. Run `make build-prod` once.

If `make build-prod` stops at the source-freshness gate (data older than 72 h), ask the owner whether to run `make advance DAYS=<n>`. dp-4's warehouse tests need a current prod build either way.

## File Structure

```
data-platform/dbt/
  dbt_project.yml                          # + group/access folder configs
  selectors.yml                            # new: the `ci` selector
  macros/record_run_results.sql            # + _audit_message: the first line only
  models/_groups.yml                       # new: five groups, their owners
  models/marts/_exposures.yml              # new: sherpa_agent
  models/marts/_metrics.md                 # + metric_roas, metric_cac
  models/marts/marketing_web/dim_campaigns.sql, fct_ad_spend_daily.sql   # new
  models/marts/customers/fct_leads_unmasked.sql, fct_leads.sql           # new: the pair
  models/marts/{marketing_web,customers,sales,products}/_*__models.yml   # new marts, unit tests, FK targets, tags, access
  models/intermediate/identity/_identity__models.yml                     # per-model group/access
  tests/singular/assert_pii_marts_are_masked_views.sql                   # + fct_leads pair, two arms, group
  tests/singular/assert_ad_spend_attribution_is_complete.sql             # new
  tests/singular/assert_revenue_reconciles_with_raw.sql                  # new
  tests/singular/assert_agent_exposure_lists_every_visible_mart.sql      # new
  tests/singular/assert_deleted_customers_are_anonymized.sql, assert_no_test_accounts_in_marts.sql,
    assert_buyer_revenue_pareto.sql                                      # extended / grouped / tagged
data-platform/tests/dbt/test_reader_access.py   # MARTS, UNMASKED
data-platform/tests/dbt/test_macros.py          # new: the audit message
data-platform/tests/dbt/test_selectors.py       # new: the distribution tag
agent/mcp-server/src/sherpa_mcp/tools.py        # DOES_NOT_COVER, DOMAIN_SUBJECTS, NOTES
agent/mcp-server/tests/test_tools.py, test_catalog.py, fixtures/manifest.json
Makefile                                        # + slim
.github/workflows/ci.yml                        # new
```

---

### Task 1: The audit table keeps only the first line of dbt's message

**Model:** implementer `haiku` · reviewer `sonnet`

**Files:** `data-platform/dbt/macros/record_run_results.sql`, `data-platform/tests/dbt/test_macros.py`.

It comes first because `dim_customers_unmasked` already has `not_null` constraints, and Task 4 adds a second PII table. A constraint violation's message carries `DETAIL: Failing row contains (…)`, the row's real values included, and `sherpa_ui` reads `ops.dbt_run_results.message` (spec §8 "Added by ui-1").

- [ ] **Step 1: Test first.** `test_macros.py::test_audit_message_keeps_only_the_first_line` uses the session `dbt` fixture of `data-platform/tests/conftest.py`, with the scratch target path its other callers use. It runs `compile --inline` on three inputs:
  - **A violation message:** `{{ _audit_message('Database Error in model fct_leads_unmasked\n  null value in column "lead_id"\n  DETAIL:  Failing row contains (null, a@example.com).') }}` renders exactly `Database Error in model fct_leads_unmasked`.
  - **A long message:** `{{ _audit_message('x' * 2500) | length }}` renders `2000`.
  - **No message:** `{{ _audit_message(none) is none }}` renders `True`.

  Leave it unmarked if `compile --inline` runs without Postgres, otherwise mark it `integration`. Run it. Expected: FAIL, because the macro is undefined.
- [ ] **Step 2: Implement.** In `record_run_results.sql`, add the private macro `_audit_message(message)`: `none` stays `none`; otherwise it returns `(message | string).splitlines()[0][:2000]`. The `message` value of the insert becomes `_audit_text(_audit_message(result.message))`.
- [ ] **Step 3:** `make test`, `make lint`. Commit `fix: the run-results audit stores only the first line of dbt's message`.

---

### Task 2: `dim_campaigns`

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `models/marts/marketing_web/dim_campaigns.sql`, `models/marts/marketing_web/_marketing_web__models.yml`; the `relationships` targets in `models/marts/customers/_customers__models.yml:169` (the `dim_customers` view's `acquisition_campaign_id`), `models/marts/marketing_web/_marketing_web__models.yml:129` and `models/marts/sales/_sales__models.yml:44`; `data-platform/tests/dbt/test_reader_access.py` (`MARTS`).

**Interfaces — Produces:** the table `marts.dim_campaigns`, with PK `campaign_id bigint` and columns `campaign_name text`, `utm_campaign text`, `channel text`, `starts_at timestamptz`, `ends_at timestamptz` and `budget numeric(12,2)`. It has no PII, so it is a plain mart.

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

### Task 3: `fct_ad_spend_daily`, ROAS and CAC

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `models/marts/marketing_web/fct_ad_spend_daily.sql`, `_marketing_web__models.yml`, `models/marts/_metrics.md`, `tests/singular/assert_ad_spend_attribution_is_complete.sql`, `test_reader_access.py`.

**Interfaces:**
- **Consumes:** `dim_campaigns` (Task 2); `stg_marketing__ad_spend_daily`, `fct_sessions`, `fct_orders`.
- **Produces:** `marts.fct_ad_spend_daily`, with PK (`campaign_id`, `spend_date`) and no PII; and the doc blocks `metric_roas` and `metric_cac`.

- [ ] **Step 1: Unit tests first** (in `_marketing_web__models.yml`, `model: fct_ad_spend_daily`; inputs as dicts, ephemeral ones as `format: sql`). Each test names its inputs' rows and expects exact rows:
  - `fct_ad_spend_daily_order_after_midnight_counts_on_session_day`: a session of campaign 1 starts on 2025-03-01 at 23:50, and its order is placed on 2025-03-02 at 00:10, for €40.00 net. The 03-01 row has `sessions = 1`, `orders = 1` and `attributed_net_revenue = 40.00`; the 03-02 row has 0, 0 and 0.00.
  - `fct_ad_spend_daily_cancelled_order_adds_nothing`: two sessions of campaign 1 on one day. One has a cancelled order (€30.00 placed); the other a customer's first order (€25.00 net). Expected: `sessions = 2`, `orders = 1`, `attributed_net_revenue = 25.00` and `new_customers = 1`. A model that dropped orders altogether, or kept the cancelled one, fails it.
  - `fct_ad_spend_daily_two_campaigns_on_one_day_stay_apart`: campaigns 1 and 2 on the same day, with 2 and 1 sessions, plus an email campaign's session that day. Each paid row counts its own, and the email session is in no row.
  - `fct_ad_spend_daily_first_order_counts_one_new_customer`: one customer with a first order and a repeat order in campaign 1's sessions that day gives `orders = 2` and `new_customers = 1`.

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
  - Σ `new_customers` with the `dim_customers` rows (the view) whose `acquisition_campaign_id` is it

  It returns one row per campaign and measure that differ. The `new_customers` comparison derives from `dim_customers`, independently of the model's `is_first_order`. Check that it fails: change the model's join to `order_date` in a throwaway edit, run it, and see the campaign rows it returns. Then revert.
- [ ] **Step 6:** add `"fct_ad_spend_daily"` to `MARTS`. Then run `make build`, `make lint` and `make test`.
- [ ] **Step 7: Commit** `feat: fct_ad_spend_daily with session-day attribution, ROAS and CAC definitions`.

---

### Task 4: `fct_leads`, the second PII pair

**Model:** implementer `sonnet` · reviewer **`opus`**: this is the masking boundary, and the reviewer checks every exit of Review Focus 2.

**Files:**
- `models/marts/customers/fct_leads_unmasked.sql`, `models/marts/customers/fct_leads.sql`, `_customers__models.yml`
- `tests/singular/assert_pii_marts_are_masked_views.sql`, `assert_deleted_customers_are_anonymized.sql`, `assert_no_test_accounts_in_marts.sql`
- `data-platform/tests/dbt/test_reader_access.py`, `agent/mcp-server/tests/test_catalog.py`

**Interfaces:**
- **Consumes:**
  - `stg_crm__leads`, which has the real normalized `email`
  - `stg_crm__email_sends`
  - the `dim_customers` **view**, for `lead_id`, `customer_id`, `signup_at` and `is_deleted`; never `dim_customers_unmasked`
- **Produces:** `marts.fct_leads_unmasked` (table, PK `lead_id`, `email` real) and `marts.fct_leads` (view, `email` hashed).

- [ ] **Step 1: Unit tests first,** on `model: fct_leads_unmasked`:
  - `fct_leads_unconverted_lead_has_nulls_and_zero_counts`: no customer has the lead's `lead_id`, and it has no sends. Expected: `is_converted = false`; `converted_customer_id`, `converted_at` and `days_to_convert` null; the three counts `0`.
  - `fct_leads_converted_lead_counts_days_and_emails`: lead 1 is created on 2025-02-01 at 10:00, and its customer has `lead_id = 1` and `signup_at` 2025-02-11 09:00. Its three sends are opened on 2 and clicked on 1. Lead 2's customer signs up on the lead's own day, 2025-02-01 23:00. Expected:
    - lead 1: `days_to_convert = 10`; its `email` is kept; `emails_sent = 3`, `emails_opened = 2`, `emails_clicked = 1`
    - lead 2: `days_to_convert = 0` (the boundary of the `accepted_range`)
  - `fct_leads_deleted_customer_lead_has_no_email`: the customer has `is_deleted = true`. Expected: `email` null, `is_converted = true`.

  The inputs use made-up addresses (`a@example.com`).
- [ ] **Step 2: The table** `fct_leads_unmasked`:
  - **Join:** leads left join the `dim_customers` view on `dim_customers.lead_id = leads.lead_id`. That is the whole conversion rule (spec §6.5), so a test account never converts.
  - **Converted columns:** `is_converted` = a customer was found; `converted_at` = `signup_at`; `days_to_convert` = `signup_at::date - created_at::date`.
  - **`email`:** the lead's normalized email, null when the customer is deleted.
  - **Counts:** from `stg_crm__email_sends`, using `count(*)`, `count(opened_at)` and `count(clicked_at)`.
  - **Left out:** `utm_campaign` (spec §6.5).
- [ ] **Step 3: The view** `fct_leads`: `{{ config(materialized='view') }}`, the table's columns in its order, `{{ mask_pii('email') }} as email`, from `{{ ref('fct_leads_unmasked') }}`. Copy `dim_customers.sql`'s shape.
- [ ] **Step 4: YAML,** two entries, as `dim_customers_unmasked` and `dim_customers` are written.
  - **The table:**
    - `grants: {select: []}`
    - meta `domain: customers`, `grain: one row per CRM lead`, `primary_key: lead_id`
    - `lead_id` with `not_null` and `primary_key` constraints
    - `email` with `meta.pii: true`
    - no data tests
  - **The view:**
    - every column with `data_type`
    - the same meta
    - `email` with `meta.masked: true` and the description `Salted md5 hash (32 hex characters) of the lead's normalized email; null when the lead's customer is deleted. Equal emails hash equally, so counting, count(distinct ...), group by and joins work; comparing it with a plain value, a pattern (LIKE/ILIKE), a string function, length() or sorting gives meaningless results (some patterns match random rows). Identify leads by lead_id and their customers by converted_customer_id; questions about email domains cannot be answered.`
    - `lifecycle_stage` described as the CRM's stage, which can lag behind `is_converted`
    - synonyms: model `[leads, subscribers, prospects]`, `is_converted` `[converted, became a customer]`
  - **The view's tests:**
    - `unique` and `not_null` on `lead_id`
    - `accepted_values` on `lead_source` and on `lifecycle_stage`
    - `relationships` from `converted_customer_id` to `dim_customers` (where not null)
    - `dbt_utils.accepted_range` on `days_to_convert` with `min_value: 0`
    - `dbt_utils.expression_is_true` with `is_converted = (converted_customer_id is not null)`, `lifecycle_stage <> 'customer' or is_converted`, `emails_opened <= emails_sent` and `emails_clicked <= emails_sent`
- [ ] **Step 5: `assert_pii_marts_are_masked_views`.**
  - **Dependencies:** add `-- depends_on: {{ ref('fct_leads') }}` and `-- depends_on: {{ ref('fct_leads_unmasked') }}`.
  - **New arm (spec §6.5 "Checks"):** a node with `resource_type == 'model'` whose `depends_on.nodes` holds an `*_unmasked` model, and which is not that model's own sibling view (another `*_unmasked` model is no exception), returns the literal row (`<model>`, `'reads <table>; ref <view> instead'`). It is about 5 lines of Jinja.
  - **Exact hash:** arm (e) becomes "`t.<col> is not null and (v.<col> is null or v.<col> is distinct from {{ mask_pii('t.<col>') }})`", so an unsalted `md5(email)` fails it, and an empty salt still does.
  - **Prove each arm,** with throwaway edits that you revert:
    - a model `tmp_reads_leads` that selects from `fct_leads_unmasked` returns the new arm's row
    - a `fct_leads` view with `md5(email)` returns arm (e)'s row
    - the real build returns no rows
- [ ] **Step 6: Extend the two other singular tests.**
  - **`assert_deleted_customers_are_anonymized`:** also return the `lead_id` of each `fct_leads_unmasked` row whose converted customer is deleted and whose `email` is not null. Never the email itself.
  - **`assert_no_test_accounts_in_marts`:** add `fct_leads.converted_customer_id` to `mart_customers`.
  - Check that the first fails without the table's nulling (a throwaway edit), then revert.
- [ ] **Step 7: The registry** (guide §8, step 4).
  - **`test_reader_access.py`:** `"fct_leads"` goes in `MARTS`, and `"fct_leads_unmasked"` in `UNMASKED`.
  - **`test_catalog.py`:** the expected set of `test_masked_columns_are_the_specs` becomes `{("dim_customers", "email"), ("fct_leads", "email")}`.
  - **`test_catalog.py`, wording:** `PSEUDONYMISED_SENTENCE` speaks of customers, so split it into:
    - `HASH_SENTENCE` (from "Equal emails hash equally" to "(some patterns match random rows).") asserted on every masked column
    - a per-column dict of the sentence that names the subject, asserted on its column: `("dim_customers", "email")`: `"Identify customers by customer_id; questions about email domains cannot be answered."` and `("fct_leads", "email")`: `"Identify leads by lead_id and their customers by converted_customer_id; questions about email domains cannot be answered."`
- [ ] **Step 8: Verify.**
  - `dbt build --select fct_leads_unmasked+` (the pair, never the table alone), then `make build`, `make lint`, `make test`.
  - Then `make build-prod` and `uv run --env-file .env pytest -m warehouse data-platform/tests/dbt/test_reader_access.py agent/mcp-server/tests/test_catalog.py`. As each reader role, `TestMaskedMarts` finds no privilege on `fct_leads_unmasked` and only hashes through `fct_leads`.
- [ ] **Step 9: Commit** `feat: fct_leads as a masked pair; the masking test checks the exact hash and who reads an unmasked table`.

---

### Task 5: What the agent is told

**Model:** implementer `haiku` · reviewer `sonnet`

**Files:** `agent/mcp-server/src/sherpa_mcp/tools.py` (`DOES_NOT_COVER`, `DOMAIN_SUBJECTS`, `NOTES` and the comment above `DOES_NOT_COVER`), `agent/mcp-server/tests/test_tools.py` (its literal `DOES_NOT_COVER` and `NOTES` lists).

- [ ] **Step 1: Tests first.** Change the literal lists in `test_tools.py` (the MCP test audit's style):
  - **`DOES_NOT_COVER`:**
    - drop the campaign entry and the leads entry
    - the PII entry becomes `"email addresses, email domains and names of customers and leads: emails are available only as salted hashes and names are not in the marts"`
    - the other entries stay as they read
  - **`NOTES`:** append `"fct_ad_spend_daily covers paid campaigns only: email campaigns spend nothing, so their sessions, orders and new customers are in fct_sessions, fct_orders and dim_customers but not in fct_ad_spend_daily"`.

  `DOMAIN_SUBJECTS` is prompt copy with no spec list, so it gets no test. Run the tests. Expected: FAIL.
- [ ] **Step 2: Implement.**
  - **`DOES_NOT_COVER`:** make the same changes, and drop the comment's "change when data-platform-4" sentence.
  - **`DOMAIN_SUBJECTS`:**
    - `customers` gains "CRM leads and their conversion to customers"
    - `marketing_web` gains "campaigns, and daily ad spend of paid campaigns with their sessions, orders, revenue and new customers"
  - **`NOTES`:** add the note.
- [ ] **Step 3:** `make test`; `make lint`. Commit `feat: get_context covers campaigns, ad spend and leads`.

---

### Task 6: Groups, access, the exposure and the raw reconciliation

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:**
- `models/_groups.yml`, `dbt_project.yml`
- `models/intermediate/identity/_identity__models.yml`, `models/marts/customers/_customers__models.yml`, `models/marts/_exposures.yml`
- `tests/singular/assert_pii_marts_are_masked_views.sql`, `assert_deleted_customers_are_anonymized.sql`, `assert_agent_exposure_lists_every_visible_mart.sql`, `assert_revenue_reconciles_with_raw.sql`
- `agent/mcp-server/tests/fixtures/manifest.json`

- [ ] **Step 1: Groups.** Define five groups, each with an `owner: {name: …}`:
  - `sales`: "Sales analytics"
  - `customers`: "CRM analytics"
  - `products`: "Merchandising analytics"
  - `marketing_web`: "Marketing analytics"
  - `shared`: "Data platform"
- [ ] **Step 2: Access** (spec §8):
  - **Marts:** `marts/<domain>` gets `+group: <domain>` and `+access: public` in `dbt_project.yml`. `dim_customers_unmasked` and `fct_leads_unmasked` override it with `access: private` in their YAML entries: only their views read them.
  - **Intermediate:**
    - `intermediate/orders` gets `+group: sales` and `+access: private`
    - `intermediate/events` gets `+group: marketing_web` (access stays protected)
    - in `intermediate/identity`, per model in its YAML: `int_identity__lead_to_customer` is `group: customers`, `access: private`; `int_identity__anonymous_to_customer` is `group: marketing_web`
  - **Staging:** untouched.
  - **Singular tests:** dbt refuses a `private` ref from any node outside its group, tests included (`is_invalid_private_ref`). So the singular tests that ref an `_unmasked` table, `assert_pii_marts_are_masked_views` and `assert_deleted_customers_are_anonymized`, get `{{ config(group='customers') }}`.
  - **Verify:** run `dbt parse`. Then a throwaway model in `marts/marketing_web` that refs `int_orders__status_pivoted` must fail parse with dbt's private-access error. Quote that error in the task report, then delete the model.
- [ ] **Step 3: Exposure test first.** `assert_agent_exposure_lists_every_visible_mart`, at execution time (`{% if execute %}`), checks the visible marts, i.e. the `graph.nodes` with `resource_type == 'model'`, schema `marts` and a name not ending in `_unmasked` (spec §6.5, the rule `grant_read_access` uses). It returns one literal row (`unique_id`, `problem`) per:
  - visible mart missing from `graph.exposures['exposure.sherpa.sherpa_agent'].depends_on.nodes`: `'not in sherpa_agent'`
  - exposure dependency that isn't a visible mart: `'not a visible mart'`
  - visible mart whose `access` isn't `public`: `'visible but not public'`
  - `_unmasked` model whose `access` isn't `private`: `'unmasked but not private'`

  Otherwise it returns no rows (`select … where false`). Run it. Expected: an error, because the exposure doesn't exist yet.
- [ ] **Step 4: Exposure.** In `_exposures.yml`, `sherpa_agent`:
  - `type: application`, `owner: {name: Data platform}`
  - a description of the MCP server and the agent (CLI, the UI's Ask), citing data-platform spec §11
  - `depends_on`: `ref()` to the 13 visible marts, never an `_unmasked` table

  Check: the test passes. With one `ref` removed it returns that mart, and you revert.
- [ ] **Step 5: Reconciliation test.** `assert_revenue_reconciles_with_raw` reads the raw tables through `source()`, not through staging. It reads no PII, so it needs no group.
  - **Orders, per UTC day of `placed_at`:** the shop's non-cancelled orders of non-test customers (Σ `subtotal`, Σ `discount_total`, the order count) against non-cancelled `fct_orders` by `order_date` (Σ `gross_revenue`, Σ `discount_amount`, count).
  - **Refunds, per UTC day of `refunded_at`:** the raw `refunds` of non-test customers (Σ `amount`) against `fct_refunds` by `refund_date` (Σ `amount`).

  It returns (`day`, `measure`, `raw_value`, `mart_value`) where they differ. Check it against a throwaway change to `fct_orders.gross_revenue` (e.g. `+ 0.01`), then revert.
- [ ] **Step 6: Verify.**
  - `make build`, `make lint`, `make test`.
  - Then `make build-prod`, and `uv run --env-file .env pytest -m warehouse agent/mcp-server ui/backend data-platform/tests`. The MCP server's public-access rule is now active, and it must still list all 13 visible marts and no `_unmasked` table.
  - **The MCP fixture manifest** (`agent/mcp-server/tests/fixtures/manifest.json`): give its visible mart nodes `access: public`, and any `_unmasked` node `access: private`, as the real manifest now has. Then the default suite runs the rule prod runs. Then `make test`.
  - Open the dbt docs (`make docs`) and Lineage (`/lineage?select=+exposure:sherpa_agent`). The exposure has every visible mart upstream, in ui-3's exposure band. Lineage needs `make up` from this worktree, which replaces the running `ui` and `mcp` containers with this checkout's build; `make up` from the main checkout afterwards puts them back.
- [ ] **Step 7: Commit** `feat: groups and access per domain, the sherpa_agent exposure, raw reconciliation test`.

---

### Task 7: The `distribution` tag, the `ci` selector and `make slim`

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:**
- `data-platform/dbt/selectors.yml`
- the YAML of the distribution tests: the `dim_products` row-count test and the `fct_orders.item_count` mean test, plus every other `dbt_expectations.expect_table_row_count_to_be_between` or `expect_column_mean_to_be_between` that `grep` finds
- `tests/singular/assert_buyer_revenue_pareto.sql`
- `data-platform/tests/dbt/test_selectors.py`
- `Makefile`

- [ ] **Step 1: Test first.** `test_selectors.py::test_distribution_tag_is_exactly_the_distribution_checks` uses the session `dbt` fixture, with the scratch target path, so `target/` is untouched:
  - `dbt ls --resource-type test --output name` gives every test
  - `dbt ls --select tag:distribution --output name` gives the tagged ones

  The tagged set must equal the tests whose names start with `dbt_expectations_expect_table_row_count_to_be_between` or `dbt_expectations_expect_column_mean_to_be_between`, plus `assert_buyer_revenue_pareto`, which is the spec §8 definition. It must also be non-empty. It is marked like `test_macros.py` (Task 1). Run it. Expected: FAIL, because nothing is tagged yet.
- [ ] **Step 2: Tag.** Add `config: {tags: [distribution]}` on each of those tests, and `tags=['distribution']` in the Pareto test's `config()`.
- [ ] **Step 3: Selector.** In `selectors.yml`, `ci` = everything except `tag:distribution`, with a `description` saying why (spec §8). Check that `dbt ls --selector ci --resource-type test` leaves exactly the tagged tests out.
- [ ] **Step 4: `make slim`.**
  - **Recipe:** `$(DBT) build --select state:modified+ --defer --state target-prod`, on dev. The `+` also keeps a changed `_unmasked` table with its view.
  - **Start date:** add `slim` to the line that exports `SHERPA_SIMULATION_START_DATE` (`build build-prod slim: export …`).
  - **Guard:** before dbt runs, `$(if $(wildcard data-platform/dbt/target-prod/manifest.json),,$(error make slim defers to prod: run make build-prod first))`.
  - **Makefile:** add it to `.PHONY`, with a one-line comment.
- [ ] **Step 5: Verify `make slim`** (Review Focus 4), recording each output's summary line in the report:
  - **(a) Matching state:** right after `make build-prod`, `make slim` builds nothing. If it selects nodes, find the config that differs between the prod manifest and dev, and report it before changing anything.
  - **(b) One change:** after a comment change in `dim_campaigns.sql`, it builds exactly what `dbt ls --select dim_campaigns+` lists (`dim_campaigns`, `fct_ad_spend_daily` and the tests on them and on the FKs that point at it). Revert the change.
  - **(c) Missing state:** with `target-prod/manifest.json` renamed (this worktree's only), it stops with the guard's message. Restore the file.
- [ ] **Step 6:** `make lint`, `make test`. Commit `feat: distribution tag and ci selector; make slim builds what changed, deferring to prod`.

---

### Task 8: GitHub Actions

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `.github/workflows/ci.yml`.

- [ ] **Step 1: Workflow** `ci`, on `pull_request` to `main`:
  - **Settings:**
    - `permissions: contents: read`
    - `concurrency` per ref with `cancel-in-progress: true`
    - one job `checks` on `ubuntu-24.04` with `timeout-minutes: 30`
  - **Steps, in order:**
    1. **Toolchains:** `actions/checkout`; `astral-sh/setup-uv` with Python 3.12; `actions/setup-node` with Node 22 and the npm cache on `ui/frontend/package-lock.json`. Each `uses:` is pinned to a full commit SHA, its tag in a comment.
    2. **Environment:** `cp .env.example .env`. The `change-me` passwords are fine for a throwaway container. The PII salt needs nothing: `ensure_pii_salt` generates it in the runner's Postgres on the first dbt run.
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

### Task 9: Documentation

**Model:** implementer `sonnet` · reviewer `haiku`

- [ ] **Step 1: `CLAUDE.md`.**
  - **Source of truth:**
    - data-platform-4 joins the done plans
    - the current plan line, and sequencing
  - **Commands:**
    - `make slim` under dbt (needs `make build-prod`)
    - a CI line under Checks: what the gate runs; warehouse tests, Playwright and `live` stay local
  - **Conventions:**
    - a new model joins its domain's group: a visible mart is `public`, an `_unmasked` table and an intermediate used by one group are `private`
    - a singular test that refs an `_unmasked` table is in its group
    - a new visible mart goes into the `sherpa_agent` exposure and `MARTS`
  - **Tests:** a generator-distribution test is tagged `distribution`; nothing else is.
- [ ] **Step 2: `README.md`.**
  - the status line
  - Stack: GitHub Actions is no longer *planned*
  - Develop: `make slim`, and that CI runs on every pull request
  - layout: `.github/`
  - the marts sentence names the marketing marts
- [ ] **Step 3: Guides** (short: basic ≤ ~150 lines, advanced ≤ ~250; link, don't repeat; one symptom → fix table each). Every example query follows Global Constraints "SQL written for readers".
  - **`basic/querying-the-marts.md`:**
    - **Campaigns and ad spend:** two copy-pasteable queries, ROAS by channel last month and CAC by campaign.
    - **Leads:** the lead-to-customer conversion rate by `lead_source`, counted by `lead_id`, not by email.
    - **Traps:** email campaigns aren't in `fct_ad_spend_daily`; clicks aren't sessions (R11); `lifecycle_stage` lags `is_converted`; `fct_leads.email` is a hash like `dim_customers.email`.
  - **`basic/building-the-warehouse.md`:** a `make slim` section: when to use it, the "run `make build-prod` first" error, and nothing to build after a fresh prod build.
  - **`advanced/adding-or-changing-a-mart.md`:**
    - groups and access for a new model
    - the exposure and `MARTS`
    - when to tag a test `distribution`
    - in §8 (PII mart): the table is `private`, a singular test that refs it gets the table's group, and no other model may read it (the masking test's new arm)
  - **New `advanced/ci.md`** (CI is a new thing people extend; under ~100 lines):
    - what the gate runs and why (link spec §8)
    - reproducing a failing step locally: `make lint` and `make test` as usual, and the dbt step as `dbt build --full-refresh --selector ci --warn-error` on dev
    - adding a check
    - `--warn-error`, and why the `distribution` tag exists
  - Link it from the README's guide list.
  - **`basic/using-the-mcp-server.md`:** update its "what it can't answer" text if it repeats `does_not_cover`.
- [ ] **Step 4:** run `make lint`, then commit `docs: marketing marts, governance, make slim and CI`.

---

### Task 10: Panel review

**Model:** every reviewer and the devil's advocate on `opus`, dispatched by the controller (`CLAUDE.md`). Fixes go through the usual loop.

- [ ] **Step 1: Reviewers**, in parallel. Each gets `git diff main...HEAD`, this plan, its focus and the spec sections named:
  - **Data engineer** (spec §6.5, §7, §8):
    - the new marts' SQL, contracts, tests and unit tests
    - groups and access against the refs (would a future mart need a `private` model? a PII mart in another domain needs the masking test in two groups)
    - the singular tests' use of `graph`
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
    - `test_macros.py` and `test_selectors.py`
    - whether the CI's commands match what `make build`, `make lint` and `make test` do locally
  - **Security engineer** (spec §6.5 "PII masking" and "Checks", agent spec §4.2):
    - every exit of Review Focus 2 (the `fct_leads` view, the new arm, the exact hash, the audit message)
    - what the reader roles can now read (`TestMaskedMarts`, `test_reader_access.py`)
    - the workflow on a public repository (fork pull requests, token permissions, nothing secret)
  - **AI engineer** (agent spec §4.1, §4.2):
    - the new `does_not_cover`, `DOMAIN_SUBJECTS` and `NOTES` texts
    - the marts' synonyms and descriptions, including `fct_leads.email`'s
    - all of it from the view of an agent answering "CAC by channel", "how many leads converted" or "how many leads use gmail?" (unanswerable)
- [ ] **Step 2: Devil's advocate.** Give it the diff and every finding. It challenges wrong or over-built findings, and adds the failures the panel missed: what passes CI and still breaks prod, and which test restates the code.
- [ ] **Step 3: Triage and fix.** Triage every finding (fix, defer or reject), then fix and re-verify:
  - `make lint`, `make test`, `npm test`
  - `make build-prod`, then `pytest -m warehouse`, after asking the owner to run `make eval-db`, because `test_reader_access` covers the eval database too, and the rebuild makes the eval database's new salt
  - CI green

  The triage table goes in the PR description.

---

## Notes for later agent plans (owner notes, not tasks)

- **agent-design-2 (gold set and evals):**
  - **`unanswerable` cases:** "how many customers use gmail?", "customers whose last name starts with A?", "most common first name?"
  - **One answered case over hashes:** `count(distinct email)`.
  - **No hash literal:** no gold SQL holds a 32-character hex literal, because an `eval-db` rebuild makes a new salt.
  - **The guard applies:** gold SQL and `examples.yaml` entries respect the guard (casts to `CAST_TYPES` only, no `lpad`/`rpad`/`repeat`, no `(expr).name`).
- **agent-design-3:** the fabricated-number guard must not read the digit runs inside a hash as numbers.
- **agent-design-4 (gold set batch 2, after this milestone):** lead questions count by `lead_id` and join customers through `converted_customer_id`, never through the hashed email.
