# data-platform-2 — Core Marts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The consumption layer the agent will query: intermediate order, session and lead models, and the `dim_customers`, `dim_products`, `dim_promotions`, `fct_orders`, `fct_order_items`, `fct_returns` and `fct_refunds` marts. They are contract-enforced, fully documented, readable by `sherpa_agent` on prod, and also built into a frozen `sherpa_eval` database.

**Architecture:** Staging (data-platform-1) feeds intermediate models in `dbt/models/intermediate/`, which hold the business logic and are not exposed. Marts in `dbt/models/marts/<domain>/` are tables with enforced contracts, `persist_docs`, indexes and grants. Project hooks create `pg_trgm` in an `extensions` schema and grant the agent access to `marts` and `extensions`, on the prod and eval targets only. Metric definitions live once, in `metric_<name>` doc blocks.

**Tech Stack:** dbt Core 1.12.6 + dbt-postgres 1.11.0 (pinned), dbt_utils 1.4.1, **dbt_expectations 0.10.10 (`metaplane/dbt_expectations`, pulls `godatadriven/dbt_date`)**, Postgres 16, pytest + psycopg for the role checks.

**Spec:** `docs/superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md`. Read §3 (roles, schemas, targets), §6.4–§6.6 (intermediate models, marts, metric definitions), §7 (tests) and §8 data-platform-2 before starting. **`CLAUDE.md` is binding** (Working Style, Python Guidelines, the dbt conventions).

## Global Constraints

- **Incremental commits (CLAUDE.md).** Each task adds only the files, packages, config, Make targets, `.env` variables and `.gitignore` entries it uses. Every commit leaves `make build`, `make lint` and `make test` green.
- **Model naming:** `int_<entity>__<verb>`, `fct_`/`dim_`. YAML per directory: `_<dir>__models.yml`. **Every model and column has a description** (`make docs-check` stays green).
- **Intermediate models** live in schema `intermediate` (`dev_intermediate` on dev), default materialization `ephemeral`; the views and tables listed in spec §6.4 override it. They are never read by consumers.
- **Every mart:**
  - is a table with an enforced contract: every column has a `data_type`, and the PK has `primary_key` + `not_null` constraints
  - has `persist_docs` (relation + columns) and a post-hook `analyze {{ this }}`
  - carries `meta: {domain, grain, primary_key}`; PII columns carry `meta.pii: true`; business-term columns carry `meta.synonyms`
  - **casts every output column to its contract type** in its final `select`
- **Contract types:** ids `bigint`; money `numeric(12,2)`; quantities and counts `integer`; day counts `integer`; `*_date` `date`; `*_at` `timestamptz`; flags `boolean`; everything else `text`.
- **UTC everywhere:** a date from a timestamp is always `(<ts> at time zone 'UTC')::date`, never a bare `::date`. Text from a timestamp never depends on the connection's `TimeZone`.
- **Test accounts (R5)** and everything they did are absent from every mart and from `int_events__sessionized`. **Soft-deleted customers (R6)** stay, with `email`, `first_name`, `last_name` null.
- **`as_of_date`** = `max(order_date)` over `fct_orders`, never `now()`.
- **Cancelled orders** (spec §6.5): the amount columns keep their as-placed values; `net_revenue`, `cogs`, `returned_cogs`, `gross_margin` and the line `cogs` are 0.
- **Severity:** integrity tests `error`; distribution tests `warn`.

## Review Focus

1. **A web order whose purchase event is still pending (R2) or missing:** the order must still appear in `fct_orders`, with `marketing_channel = 'unknown'` and `session_id` null, not dropped by an inner join. Test: `assert_fct_orders_keeps_every_order` (Task 5).
2. **A customer with no non-cancelled order** (a prospect, or someone whose only order was cancelled): `order_count = 0`, segment `prospect`, `acquisition_channel = 'unknown'`, `first_order_at`/`days_since_last_order` null, `is_vip` false. No divide-by-zero in `avg_order_value`. Test: `assert_dim_customers_without_orders_are_prospects` (Task 7).
3. **Discount allocation edge cases:** a single-line order, a free-shipping row with `amount = 0`, two discounts on one order (R8), and an awkward split like 10.00 over three equal lines. Lines must sum exactly to the order's discount, and each allocation must stay within `[0, gross_amount]`. Tests: `column_sums_match` + `expression_is_true` (Task 2).
4. **Running `make build` on dev must not expose dev schemas to the agent:** `sherpa_agent` gets no `USAGE` on `dev_marts`. Test: `TestAgentAccess::test_cannot_read_dev_marts` (Task 8).
5. **Re-running `make eval-db` while `sherpa_eval` already exists:** it drops and rebuilds it with identical results. Verified in Task 9 Step 4.

---

## File Structure

```
dbt/packages.yml                                   # + dbt_expectations
dbt/dbt_project.yml                                # + intermediate/marts config, hooks
dbt/profiles.yml                                   # + eval target
dbt/macros/
  normalize_email.sql                              # normalize_email(column)
  revenue_excluding_tax.sql                        # revenue_excluding_tax(amount)
  customer_segment.sql                             # customer_segment(order_count, days_since_last_order)
  ensure_extensions.sql                            # on-run-start: extensions schema, pg_trgm, search_path
  create_trigram_index.sql                         # post-hook helper
  grant_agent_access.sql                           # on-run-end + run-operation
  tests/column_sums_match.sql                      # custom generic test
dbt/models/intermediate/
  orders/_orders__models.yml
  orders/int_order_items__discount_allocated.sql   # view
  orders/int_order_items__costed.sql               # ephemeral
  orders/int_orders__status_pivoted.sql            # ephemeral
  orders/int_orders__payments_summarized.sql       # ephemeral
  orders/int_orders__refunds_summarized.sql        # view, order grain
  orders/int_order_items__returns_summarized.sql   # view, line grain
  events/_events__models.yml
  events/int_events__sessionized.sql               # table
  identity/_identity__models.yml
  identity/int_identity__lead_to_customer.sql      # ephemeral
dbt/models/marts/
  _metrics.md                                      # metric_<name> doc blocks
  products/{_products__models.yml, dim_products.sql}
  sales/{_sales__models.yml, dim_promotions.sql, fct_orders.sql, fct_order_items.sql, fct_returns.sql, fct_refunds.sql}
  customers/{_customers__models.yml, dim_customers.sql}
  shared/_shared__models.yml                       # dim_date gets data_types (contract)
dbt/tests/assert_*.sql                             # singular tests (flat, like data-platform-1's)
tests/dbt/test_agent_access.py                     # pytest, marker `warehouse`
```

---

### Task 1: dbt_expectations and the `normalize_email` macro

**Adds:** `metaplane/dbt_expectations` `0.10.10` in `dbt/packages.yml` (re-lock with `make deps`, commit `package-lock.yml`); `dbt/macros/normalize_email.sql`.

**Interfaces — Produces:** `normalize_email(column)` → the SQL expression `lower(trim(<column>))`.

- [ ] **Step 1:** Add the package; run `make deps`. Expected: `dbt_expectations` and `dbt_date` installed, `package-lock.yml` updated.
- [ ] **Step 2:** In `_shop__models.yml` (`stg_shop__customers.email`) and `_crm__models.yml` (`stg_crm__leads.email`), add `dbt_expectations.expect_column_values_to_match_regex` with `regex: '^[^@\s]+@[^@\s]+\.[a-z]+$'`. In `_events__models.yml`, add `dbt_expectations.expect_row_values_to_have_recent_data` on `stg_events__events.received_at` (`datepart: day`, `interval: 3`, `severity: warn`). Run `make build`. Expected: PASS. The regex already holds, because staging normalizes inline today.
- [ ] **Step 3:** Write the macro. Replace the three inline `lower(trim(...email))` in `stg_shop__customers.sql` (both occurrences) and `stg_crm__leads.sql` with `{{ normalize_email('source.email') }}` (or the column in scope). `make build`: PASS, with identical row counts. `uv run dbt compile` output is unchanged apart from whitespace.
- [ ] **Step 4:** `make lint && make docs-check`. Commit `feat: dbt_expectations and normalize_email macro`.

---

### Task 2: Intermediate order models and `column_sums_match`

**Adds:**
- `intermediate` folder config in `dbt_project.yml` (`+schema: intermediate`, `+materialized: ephemeral`).
- `dev_intermediate, intermediate` in the Makefile's `DBT_SCHEMAS`.
- Macros `revenue_excluding_tax` and `tests/column_sums_match`.

**Interfaces — Produces:**
- `revenue_excluding_tax(amount)` → `round(<amount> / (1 + {{ var('tax_rate') }}), 2)`.
- Generic test `column_sums_match(model, column_name, compare_model, compare_column, group_by)`. It fails with one row per `group_by` value where `coalesce(Σ model.column_name, 0) <> coalesce(Σ compare_model.compare_column, 0)`, using a full outer join, so a group missing on one side counts as 0.
- `int_order_items__discount_allocated` (view): `order_item_id, order_id, variant_id, quantity, unit_price, gross_amount, allocated_discount, net_amount`.
  - `gross_amount = quantity × unit_price`.
  - **Algorithm:** `share = round(order_discount × gross_amount / Σ gross_amount over order, 2)`. The line with the largest `gross_amount` (tie → lowest `order_item_id`) gets `share + (order_discount − Σ share)`. `net_amount = gross_amount − allocated_discount`.
  - `order_discount` = Σ `stg_shop__order_discounts.amount` for the order, 0 if none.
- `int_order_items__costed` (ephemeral): `order_item_id, unit_cost_at_sale` = the variant's current `cost_price`. The model comment says data-platform-3 switches it to the snapshot.
- `int_orders__status_pivoted` (ephemeral): `order_id, paid_at, shipped_at, delivered_at, cancelled_at`, each the first `changed_at` of that status.
- `int_orders__payments_summarized` (ephemeral): `order_id, captured_amount` (Σ `captured`), `payment_count`, `failed_payment_count`, `primary_payment_method` (the method of the largest captured payment; tie → lowest `payment_id`).
- `int_orders__refunds_summarized` (view), one row per order with refunds: `order_id`, `refunded_amount` (Σ all refunds), `refunded_revenue` (Σ `revenue_excluding_tax(amount)` for reasons `return`/`goodwill`, 0 for `cancellation`).
- `int_order_items__returns_summarized` (view), one row per order line with received returns: `order_item_id`, `returned_quantity` and `restockable_returned_quantity` (from `return_items` of returns with status `received`), `refunded_revenue = round(returned_quantity × net_amount / quantity, 2)` (using `int_order_items__discount_allocated`).

  These are two models because order-level and line-level refund figures have different grains. Mixing them in one model would make every test and join grain-conditional. The spec's §6.4 row is updated to match.

- [ ] **Step 1: Write the tests first**, in `_orders__models.yml`:
  - **PKs:** unique + not_null on `int_order_items__discount_allocated.order_item_id`.
  - **Allocation sums:** `column_sums_match` on `allocated_discount` with `compare_model: ref('stg_shop__order_discounts'), compare_column: amount, group_by: order_id`.
  - **Allocation bounds:** `dbt_utils.expression_is_true` with `allocated_discount >= 0 and allocated_discount <= gross_amount`.
  - **Pivot, payments and refunds summaries:** unique + not_null `order_id` on `int_orders__refunds_summarized` (the ephemeral pivot and payments models get no data tests, since ephemeral models can't be tested directly; their columns are tested in `fct_orders`); unique + not_null `order_item_id` on `int_order_items__returns_summarized`.
  - **Returns never exceed the line:** `dbt_utils.expression_is_true` on `int_order_items__returns_summarized`: `restockable_returned_quantity <= returned_quantity`.

  Also write a singular test `dbt/tests/assert_revenue_excluding_tax.sql` that evaluates the macro over a literal `values` list and returns mismatches: 120.00→100.00, 64.80→54.00, 1.00→0.83, 0.01→0.01, 0.00→0.00.
- [ ] **Step 2:** `uv run dbt build --select intermediate.orders assert_revenue_excluding_tax` (via `cd dbt && DBT_PROFILES_DIR=. uv run --env-file ../.env dbt …`). Expected: FAIL, because the models don't exist yet.
- [ ] **Step 3:** Write the macros and the six models (CTE style like staging: `<input>` CTEs → logic CTEs → final `select`). Exclude nothing here; marts filter test accounts.
- [ ] **Step 4:** Re-run. Expected: PASS. `make docs-check` PASS (intermediate columns described). `make lint` PASS.
- [ ] **Step 5:** Commit `feat: intermediate order models, revenue_excluding_tax and column_sums_match`.

---

### Task 3: Sessionization and lead matching

**Adds:** `dbt/models/intermediate/events/`, `dbt/models/intermediate/identity/`.

**Interfaces — Produces:**
- `int_events__sessionized` (**table**), one row per event of a non-test session. Columns:
  - `event_id, anonymous_id, customer_id, event_type, occurred_at, received_at, order_id`
  - `session_id, session_start_at, session_date` (UTC date of `session_start_at`)
  - `session_utm_source, session_utm_medium, session_utm_campaign, session_referrer`
  - `marketing_channel`, `max_received_at` (the session's)
- **Rules** (spec §6.4):
  - A new session starts when the gap to the previous event of the same `anonymous_id` is **greater than** `var('session_timeout_minutes')` minutes.
  - `session_id = md5(anonymous_id || '|' || (extract(epoch from session_start_at) * 1000000)::bigint::text)`.
  - `marketing_channel` comes from the session's first event, mapped in this order: `channel_mapping` on (utm_source, utm_medium); else `unknown` if `utm_source` is not null; else `organic` when the referrer host matches `google|bing|duckduckgo`; else `referral` if the referrer is not null; else `direct`.
  - A session is excluded when any of its events has the `customer_id` of a test account (`stg_shop__customers.is_test_account`).
- `int_identity__lead_to_customer` (ephemeral): `customer_id, lead_id, lead_source`. It joins `stg_shop__customers.email` and `stg_crm__leads.email` (both already normalized); the earliest lead per customer wins (`created_at`, tie → lowest `lead_id`).

- [ ] **Step 1: Write the tests first:**
  - **YAML:** unique + not_null `event_id`; not_null `session_id`, `session_start_at`, `marketing_channel`; `accepted_values` on `marketing_channel` = `direct, organic, paid_search, paid_social, email, affiliate, referral, unknown`.
  - **Singular `assert_sessions_follow_inactivity_rule.sql`:** returns events where, ordered per `anonymous_id` by `occurred_at`, the gap to the previous event is ≤ the timeout but the `session_id` changed, or > the timeout but the `session_id` stayed the same.
  - **Singular `assert_sessions_do_not_overlap.sql`:** per `anonymous_id`, no two sessions whose `[first event, last event]` ranges intersect.
  - **Singular `assert_session_id_matches_definition.sql`:** returns events whose `session_id` differs from the md5 expression above, recomputed in the test from `anonymous_id` and `session_start_at`. It pins down the definition, so a later edit can't silently switch to a time-zone-dependent text cast. The model gets a one-line comment saying why it uses `extract(epoch …)`.
- [ ] **Step 2:** Run `uv run dbt build --select intermediate.events intermediate.identity assert_sessions_follow_inactivity_rule assert_sessions_do_not_overlap assert_session_id_matches_definition`. Expected: FAIL (no models).
- [ ] **Step 3:** Implement. Window functions: `lag(occurred_at) over (partition by anonymous_id order by occurred_at, event_id)`, then a running sum of new-session flags as the session number.
- [ ] **Step 4:** Re-run. Expected: PASS. Then explore once: `uv run dbt show --inline "select marketing_channel, count(distinct session_id) from {{ ref('int_events__sessionized') }} group by 1 order by 2 desc"`. Expected: roughly the generator's channel mix (direct ≈ organic ≈ 25%, paid_search ≈ 18%, …). `unknown` should be 0.
- [ ] **Step 5:** `make docs-check && make lint`. Commit `feat: sessionized events and lead-to-customer matching`.

---

### Task 4: Mart conventions on `dim_products` and `dim_promotions`, plus extensions and the trigram indexes

**Adds:**
- `dbt_project.yml`, marts config:
  - `+contract: {enforced: true}`, `+persist_docs: {relation: true, columns: true}`, `+post-hook: "analyze {{ this }}"`
  - `on-run-start: "{{ ensure_extensions() }}"`
- Macros `ensure_extensions`, `create_trigram_index`.
- `dim_date` gets `data_type` on every column, since contracts now apply to the whole marts folder.

**Interfaces — Produces:**
- `ensure_extensions()`, as SQL run by the hook:
  - `create schema if not exists extensions`
  - `create extension if not exists pg_trgm with schema extensions`
  - `alter database "{{ target.dbname }}" set search_path = "$user", public, extensions`
- `create_trigram_index(column)` → `drop index if exists {{ this.schema }}.<name>; create index <name> on {{ this }} using gin ({{ column }} extensions.gin_trgm_ops)` with `<name>` = `{{ this.identifier }}_{{ column }}_trgm`. The drop comes first because the previous build's `__dbt_backup` table still holds an index of that name while the post-hook runs (`if not exists` would skip and the backup drop would remove it).
- `dim_products` (`marts/products/`): columns per spec §6.5.
  - Category hierarchy from `stg_shop__categories` via two self-joins from the product's leaf: a depth-3 leaf gives l1/l2/l3; a depth-2 leaf gives l1/l2 with `l3` null.
  - `current_list_price`/`current_cost_price` from the variant.
  - `meta: {domain: products, grain: one row per product variant, primary_key: variant_id}`; `synonyms` on `product_name` (`[product, item]`), `category_l1` (`[department, category]`), `brand` (`[make, label]`).
  - Post-hooks: `create_trigram_index` for `product_name`, `brand`, `category_l1`, `category_l2`, `category_l3`.
- `dim_promotions` (`marts/sales/`): columns per spec §6.5.
  - `coupon_code_count` = codes of the promotion.
  - `redemption_count` and `total_discount_amount` over `stg_shop__order_discounts` rows of **non-test** orders (cancelled orders included: a redemption happened).
  - `meta: {domain: sales, grain: one row per promotion, primary_key: promotion_id}`.

- [ ] **Step 1: Write the YAML first** (descriptions, `data_type` per Global Constraints, constraints, tests):
  - **`dim_products`:**
    - `variant_id` unique + not_null; `sku` unique; `product_id` not_null; `category_l1` accepted_values `Footwear, Apparel, Accessories, Equipment`
    - `dbt_expectations.expect_table_row_count_to_be_between` `min_value: 1275, max_value: 1725`, `severity: warn` (spec §5.2: ~1,500 variants ±15%)
  - **`dim_promotions`:** `promotion_id` unique + not_null; `promotion_type` accepted_values `percent, fixed, free_shipping`; `campaign_id` relationships → `ref('stg_marketing__campaigns')` (`where: "campaign_id is not null"`).
- [ ] **Step 2:** `uv run dbt build --select dim_products dim_promotions dim_date`. Expected: FAIL (no models; `dim_date` contract errors until its types are declared).
- [ ] **Step 3:** Implement the macros, the two models and the `dim_date` types. Re-run. Expected: PASS.
- [ ] **Step 4: Verify the side effects** with `docker compose exec postgres psql -U sherpa_admin -d sherpa`:
  - `\dx` lists `pg_trgm` in schema `extensions`.
  - `\di dev_marts.*trgm*` lists 5 indexes.
  - `select obj_description('dev_marts.dim_products'::regclass)` returns the model description (persist_docs).
  - `explain select * from dev_marts.dim_products where product_name % 'runing shoe'` uses the trigram index (`Bitmap Index Scan`).
- [ ] **Step 5: Contract check (spec acceptance, manual, once):**
  1. Temporarily change `dim_promotions.value`'s `data_type` to `boolean`.
  2. `uv run dbt build --select dim_promotions`. Expected: a contract error naming the column.
  3. Revert.
- [ ] **Step 6:** `make docs-check && make lint`. Commit `feat: mart conventions, pg_trgm extensions, dim_products and dim_promotions`.

---

### Task 5: `fct_orders` and `fct_order_items`, plus the metric doc blocks

**Adds:** `dbt/models/marts/_metrics.md` with doc blocks `metric_gross_revenue`, `metric_discount_amount`, `metric_refunded_revenue`, `metric_net_revenue`, `metric_cogs`, `metric_gross_margin`, `metric_aov`, `metric_refund_rate`, plus a `cancelled_orders` doc block (spec §6.5 rule). The text comes from spec §6.6, written for a reader of the warehouse. Metrics whose marts don't exist yet (conversion rate, ROAS, CAC) are not written.

**Interfaces — Consumes:** Task 2 and 3 models, `dim_products`.

**Produces:**
- `fct_orders` (`marts/sales/`): columns exactly per spec §6.5.
  - Non-test orders only (join `stg_shop__customers`, `not is_test_account`).
  - `order_date = (placed_at at time zone 'UTC')::date`.
  - `current_status = order_status`; `is_cancelled = current_status = 'cancelled'`.
  - Session columns: `session_id` and `marketing_channel` from `int_events__sessionized` via the purchase event (`event_type = 'purchase' and order_id = …`). `app` for app orders; `unknown` + null session for web orders without one (Review Focus 1).
  - `ship_country_code` from the shipping address.
  - **Amounts:** `item_count = Σ quantity`; `gross_revenue = Σ gross_amount`; `discount_amount = Σ allocated_discount`; `shipping_amount`, `tax_amount`, `order_total` from staging; `captured_amount`; `refunded_amount`, `refunded_revenue` from `int_orders__refunds_summarized` (0 when the order has no refund).
  - **Outcomes,** each 0 when cancelled:
    - `net_revenue = gross_revenue − discount_amount − refunded_revenue`
    - `cogs = Σ quantity × unit_cost_at_sale`
    - `returned_cogs = Σ restockable_returned_quantity × unit_cost_at_sale` (from `int_order_items__returns_summarized`)
    - `gross_margin = net_revenue − (cogs − returned_cogs)`
  - **Flags and durations:** `has_discount` = any discount row with `amount > 0` (spec §6.6); `has_return` = any return with status ≠ `rejected`; `days_to_deliver` = UTC delivered date − order date, null if not delivered.
  - `config(indexes=[{columns: [order_date]}, {columns: [customer_id]}])`.
  - `meta: {domain: sales, grain: one row per order, primary_key: order_id}`.
  - **Synonyms:** `net_revenue: [revenue, net sales]`, `gross_revenue: [gross revenue, turnover]`, `order_total: [basket total]`, `current_status: [status, order status]`.
  - The descriptions of the revenue, cogs and margin columns use `{{ doc('metric_…') }}`.
- `fct_order_items` (`marts/sales/`): columns per spec §6.5.
  - Line `cogs` is 0 when cancelled; `returned_quantity` and line `refunded_revenue` from `int_order_items__returns_summarized` (0 when the line has no received return).
  - `config(indexes=[{columns: [order_id]}, {columns: [variant_id]}])`.
  - `meta: {domain: sales, grain: one row per order line, primary_key: order_item_id}`.

- [ ] **Step 1: Write the YAML first:**
  - **Keys and relationships:** PKs unique + not_null; `fct_orders.order_date` relationships → `ref('dim_date').date_day`; `fct_order_items.order_id` → `fct_orders`; `fct_order_items.variant_id` → `dim_products`. `fct_orders.customer_id` → `dim_customers` is added in Task 7.
  - **Accepted values:** `current_status` (`placed, paid, shipped, delivered, cancelled`), `sales_channel` (`web, app`), `marketing_channel` (the 8 channels + `unknown`).
  - **Gross ≥ net:** `dbt_expectations.expect_column_pair_values_A_to_be_greater_than_B` on (`gross_revenue`, `net_revenue`, `or_equal: true`), and the same on the lines (`gross_amount`, `net_amount`).
  - **Order and line sums agree:** `column_sums_match` from `fct_order_items` to `fct_orders` by `order_id` for `gross_amount`→`gross_revenue`, `allocated_discount`→`discount_amount` and `cogs`→`cogs`.
  - **Distribution:** `dbt_expectations.expect_column_mean_to_be_between` on `fct_orders.item_count`, `1.9`–`2.4`, `severity: warn`. That's 1.75 lines × 1.225 units per line ≈ 2.14, from the generator constants.
  - **Singular tests:**
    - `assert_captured_payments_equal_order_total.sql`: non-cancelled orders with `paid_at` set and `captured_amount <> order_total`.
    - `assert_fct_orders_keeps_every_order.sql`: returns a row when `count(fct_orders)` ≠ count of non-test staging orders, or when any web order without a session isn't `unknown` (Review Focus 1).
    - `assert_line_refunds_within_order_refunds.sql`: orders where Σ line `refunded_revenue` > order `refunded_revenue` + 0.01 × refunded lines. Line refunds are rounded per line, so they may exceed the order figure by at most a cent per line.
    - `assert_cancelled_orders_have_no_outcomes.sql`: cancelled orders with any non-zero outcome column.
- [ ] **Step 2:** `uv run dbt build --select +fct_order_items assert_captured_payments_equal_order_total assert_fct_orders_keeps_every_order assert_line_refunds_within_order_refunds assert_cancelled_orders_have_no_outcomes`. Expected: FAIL (no models).
- [ ] **Step 3:** Implement the two models and `_metrics.md`. Re-run. Expected: PASS.
- [ ] **Step 4:** Sanity checks with `dbt show --inline` against the generator's design:
  - share of `has_discount` in 0.18–0.32
  - share of `app` orders in 0.28–0.42
  - share of `unknown` below 0.01
  - `Σ net_revenue / count(*)` over non-cancelled orders (AOV) is positive and plausible for the catalog prices

  Note the values in the commit message.
- [ ] **Step 5:** `make docs-check && make lint`. Commit `feat: fct_orders, fct_order_items and metric doc blocks`.

---

### Task 6: `fct_returns` and `fct_refunds`

**Interfaces — Produces** (`marts/sales/`), columns per spec §6.5:
- `fct_returns`:
  - `reason`/`status` from staging; `returned_item_quantity` and `restockable_quantity` = Σ over its return items.
  - `refund_id`, `refunded_amount` from the refund with this `return_id` (null if none yet).
  - `config(indexes=[{columns: [order_id]}])`; `meta.grain: one row per return`.
- `fct_refunds`:
  - `refund_date = (refunded_at at time zone 'UTC')::date`
  - `revenue_amount = revenue_excluding_tax(amount)` for `return`/`goodwill`, 0 for `cancellation`
  - `days_since_order = refund_date − fct_orders.order_date`
  - `config(indexes=[{columns: [order_id]}])`; `meta.grain: one row per refund`; `synonyms` on `amount` (`[refund, money returned]`)

Both: non-test orders only.

- [ ] **Step 1: YAML first:**
  - **Keys and relationships:** PKs; `order_id` → `fct_orders`; `fct_refunds.return_id` → `fct_returns` (`where: "return_id is not null"`); `fct_returns.refund_id` unique and → `fct_refunds` (`where` not null).
  - **Accepted values:** `reason`, `status`, `refund_reason` with the StrEnum values.
  - **Revenue agrees with the orders:** `column_sums_match` from `fct_refunds.revenue_amount` to `fct_orders.refunded_revenue` by `order_id`. Both apply the same macro to the same rows, so they must match exactly.
  - **Refund timing:** `dbt_utils.expression_is_true` on `fct_refunds`: `days_since_order >= 0`.
- [ ] **Step 2:** Run (FAIL) → implement → run (PASS) → `make docs-check && make lint`.
- [ ] **Step 3:** Commit `feat: fct_returns and fct_refunds`.

---

### Task 7: `dim_customers`

**Adds:** macro `customer_segment(order_count, days_since_last_order)`; the `fct_orders.customer_id → dim_customers` relationships test.

**Interfaces — Produces:**
- `customer_segment(order_count, days_since_last_order)` → a `case` expression:
  - `order_count = 0` → `prospect`
  - `days ≤ 90 and order_count = 1` → `new`
  - `days ≤ 90` (≥ 2 orders) → `active`
  - `days ≤ 180` → `at_risk`
  - else → `churned`
- `dim_customers` (`marts/customers/`): columns per spec §6.5. Non-test customers.
  - **Deleted customers:** `email`, `first_name` and `last_name` null when `is_deleted` (`meta.pii: true` on all three).
  - **Order aggregates** come from `fct_orders`, non-cancelled orders only: `first_order_at`, `last_order_at`, `order_count`, the `lifetime_*` revenues, and `avg_order_value = lifetime_net_revenue / nullif(order_count, 0)`.
  - **Recency:** `days_since_last_order = as_of_date − last order date`, where `as_of_date = (select max(order_date) from fct_orders)`.
  - **Acquisition:** `acquisition_channel` = `marketing_channel` of the first non-cancelled order, `unknown` if none. `acquisition_campaign_id` = `stg_marketing__campaigns.campaign_id` whose `utm_campaign` equals that order's session `session_utm_campaign`, null otherwise.
  - `lead_id`, `lead_source` from `int_identity__lead_to_customer`; `country_name`, `region` from the `countries` seed.
  - **VIP:** `is_vip` is true for buyers (`order_count ≥ 1`) whose rank by `lifetime_net_revenue` desc (tie → lowest `customer_id`) is ≤ `ceil(0.10 × buyer count)`.
  - `meta: {domain: customers, grain: one row per customer, primary_key: customer_id}`.
  - **Synonyms:** `customer_segment: [lifecycle stage, segment]`, `is_vip: [vip]`, `lifetime_net_revenue: [lifetime value, ltv, clv]`.

- [ ] **Step 1: Tests first:**
  - **YAML:** PK; `customer_segment` accepted_values (`prospect, new, active, at_risk, churned`); `acquisition_channel` accepted_values (9 values); `lead_id` → `ref('stg_crm__leads')` (`where` not null); and in `_sales__models.yml`, `fct_orders.customer_id` → `ref('dim_customers')`.
  - **Singular `assert_customer_segment_boundaries.sql`:** the macro over literals. (0, null)→prospect, (1, 0)→new, (1, 90)→new, (2, 90)→active, (1, 91)→at_risk, (5, 180)→at_risk, (1, 181)→churned.
  - **Singular `assert_dim_customers_without_orders_are_prospects.sql`:** rows where `order_count = 0` but the segment isn't `prospect`, `first_order_at` isn't null, `acquisition_channel` isn't `unknown`, or `is_vip` (Review Focus 2).
  - **Singular `assert_deleted_customers_are_anonymized.sql`.**
  - **Singular `assert_no_test_accounts_in_marts.sql`:** customer ids of test accounts appearing in `dim_customers` or `fct_orders`.
  - **Singular `assert_buyer_revenue_pareto.sql`** (`config(severity='warn')`): returns a row when the top 20% of buyers by `lifetime_net_revenue` hold less than 55% or more than 65% of the total (spec §7).
- [ ] **Step 2:** Run (FAIL) → implement → run (PASS).
- [ ] **Step 3:** `make build` (the whole project, freshness included): PASS. `make docs-check && make lint`. Commit `feat: dim_customers with segments, VIPs and acquisition`.

---

### Task 8: Agent access — grants, hook, run-operation, and the role test

**Adds:**
- `dbt_project.yml`: marts `+grants: {select: ["{{ env_var('SHERPA_AGENT_USER') }}"]}`; `on-run-end: "{{ grant_agent_access() }}"`.
- Macro `grant_agent_access`.
- Makefile target `grant-agent` (`$(DBT) run-operation grant_agent_access $(PROD)`).
- Pytest marker `warehouse` ("needs `make build-prod`"), excluded by default: `addopts = '-m "not slow and not warehouse"'`.
- `tests/dbt/test_agent_access.py`.

**Interfaces — Produces:** `grant_agent_access()` executes, via `run_query` when `execute`, and returns `''`, so it works both as a hook and as a run-operation.
- On targets `prod` and `eval` it grants the agent `usage on schema marts, extensions` and `select on all tables in schema marts`.
- On any other target it only logs `skipped on <target>`.

Table grants are on every target through the `grants` config, but without schema `USAGE` they give nothing on `dev_marts`.

- [ ] **Step 1: Write failing tests** in `tests/dbt/test_agent_access.py`, `class TestAgentAccess`. Mark them `warehouse`; connect as `SHERPA_AGENT_USER`.
  - `test_reads_every_mart`: `select count(*)` succeeds on all 8 `marts.*` tables.
  - `test_cannot_read_staging_intermediate_seeds_or_raw`: each of `staging.stg_shop__orders`, `seeds.countries`, `raw_shop.orders` raises `InsufficientPrivilege`. Intermediate is ephemeral or a view, so test `intermediate.int_order_items__discount_allocated`.
  - `test_cannot_read_dev_marts`: `dev_marts.fct_orders` raises `InsufficientPrivilege` (Review Focus 4).
  - `test_can_use_trigram_similarity_unqualified`: `select similarity('running', 'runing') > 0.3` returns true.
  - `test_reads_persisted_comments`: `obj_description('marts.fct_orders'::regclass)` is not null.
- [ ] **Step 2:** `make build-prod && uv run --env-file .env pytest -m warehouse -v`. Expected: FAIL (no grants yet).
- [ ] **Step 3:** Implement the grants config, the macro and the hook. `make build-prod`, then re-run the tests. Expected: PASS. `make build` (dev), then re-run. Expected: still PASS, including `test_cannot_read_dev_marts`.
- [ ] **Step 4:** `make grant-agent`. Expected: the run-operation logs its grants and exits 0.
- [ ] **Step 5:** `make lint && make test`. Commit `feat: agent read access to marts on prod and eval`.

---

### Task 9: The eval target and `make eval-db`

**Adds:**
- `profiles.yml` target `eval`: like `prod`, with `dbname: "{{ env_var('SHERPA_EVAL_DB_NAME') }}"`.
- `.env.example`: `SHERPA_EVAL_DB_NAME=sherpa_eval`.
- `.gitignore`: `dbt/target-eval/`; `clean-targets` += `target-eval`.
- Makefile: `EVAL = --target eval --target-path target-eval` and target `eval-db`.

**Interfaces — Produces:** `make eval-db` (spec §3), in order:
1. `docker compose exec -T postgres psql -U "$$SHERPA_ADMIN_USER" -d "$$POSTGRES_DB" -c 'DROP DATABASE IF EXISTS sherpa_eval WITH (FORCE)' -c 'CREATE DATABASE sherpa_eval'` (admin has `CREATEDB` and owns what it creates).
2. `SHERPA_DB_NAME=$(SHERPA_EVAL_DB_NAME) uv run sherpa-generate generate --seed 42 --start-date 2025-01-01 --days 365`.
3. `$(DBT) build $(EVAL)`. No freshness: the frozen data is old on purpose.
4. `$(DBT) docs generate $(EVAL)`.

- [ ] **Step 1:** Implement, then run `make eval-db`. Expected: completes (~5 min). The on-run-end hook grants the agent access in `sherpa_eval`.
- [ ] **Step 2: Verify** as admin on `sherpa_eval`:
  - `select max(order_date) from marts.fct_orders` → `2025-12-31`
  - `dbt/target-eval/manifest.json` and `catalog.json` exist
  - as `sherpa_agent`: `psql -d sherpa_eval -c 'select count(*) from marts.dim_customers'` succeeds
- [ ] **Step 3:** Record a fingerprint: `select count(*), md5(string_agg(order_id::text || net_revenue::text, ',' order by order_id)) from marts.fct_orders`.
- [ ] **Step 4:** Run `make eval-db` again (Review Focus 5). Expected: the same fingerprint.
- [ ] **Step 5:** Commit `feat: frozen eval database (make eval-db)`.

---

### Task 10: Documentation and how-to guides

**Files:**
- Create `docs/guides/basic/querying-the-marts.md`, `docs/guides/advanced/adding-or-changing-a-mart.md`.
- Modify `docs/guides/basic/building-the-warehouse.md`, `docs/guides/advanced/extending-the-source-data-model.md`, `dbt/models/overview.md`, `README.md`, `CLAUDE.md`.

All docs describe the current state only. Run every command in a guide once while writing it, and show its real output.

- [ ] **Step 1: `basic/querying-the-marts.md`:** what each mart answers, with copy-paste SQL.
  - **The marts:** each one's grain, in one line.
  - **Which revenue to use:** gross vs net vs refunded revenue, with example queries (net revenue by month, top 10 products by net revenue, refund rate by category, AOV by channel, customers by segment, VIP share).
  - **Traps:**
    - cancelled orders (filter `not is_cancelled` for metrics)
    - refunds by order date (`fct_orders`) vs refund date (`fct_refunds`)
    - `unknown` channel
    - `as_of_date`, not today
    - line vs order grain (don't sum order columns after joining lines)
  - **Fuzzy search:** with `%` / `similarity()` on `dim_products`.
  - **Connecting as `sherpa_agent`:** what it can and cannot see.
- [ ] **Step 2: `advanced/adding-or-changing-a-mart.md`:**
  - **Adding a column to a mart, end to end:** SQL with the cast, then YAML (`data_type`, description, `meta`, tests). Explain what the contract error looks like when you forget the type.
  - **Adding a new mart:** folder, `meta`, PK constraint, indexes, grants (automatic), docs-check.
  - **Changing a metric definition:** the doc block, the macro, the singular tests that pin it, and why every consumer sees it.
  - **Adding a trigram index.**
  - **Writing a custom generic test,** using `column_sums_match` as the example.
  - **When to use ephemeral vs view vs table** for intermediate models.
- [ ] **Step 3: Update `basic/building-the-warehouse.md`:**
  - the intermediate layer, hooks and `make grant-agent`
  - `make eval-db`: what it is for and how long it takes
  - the `warehouse` pytest marker
  - reading a contract failure and a `column_sums_match` failure

  **Update `advanced/extending-the-source-data-model.md`:** its last step points to `adding-or-changing-a-mart.md`, for carrying a new source column into the marts.
- [ ] **Step 4: `dbt/models/overview.md`:** the layers list gains Intermediate and the marts by domain.
- [ ] **Step 5: `README.md`:**
  - status: data-platform-2 done, data-platform-3 next
  - Quickstart: add `make eval-db` as optional
  - the dbt line in the layout mentions intermediate and marts
  - Guides list: the two new guides
- [ ] **Step 6: `CLAUDE.md`:**
  - Commands: `make eval-db`, `make grant-agent`
  - Tests › Markers: `warehouse`
  - current plan → data-platform-3 to be planned
- [ ] **Step 7:** Check every command, path, model, macro and Make target named in the touched docs exists (`grep` + `ls` + `uv run dbt ls`). Expected: no dangling references.
- [ ] **Step 8:** Commit `docs: guides, README and CLAUDE.md for data-platform-2`.
