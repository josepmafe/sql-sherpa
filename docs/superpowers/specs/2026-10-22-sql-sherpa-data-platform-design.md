# SQL Sherpa — Data Platform Spec: (Generator + dbt)

**Status:** Approved · **Date:** 2026-10-22 · **Companion spec:** Agent Spec (`2026-10-22-sql-sherpa-agent-design.md`)

---

## 1. Intent

SQL Sherpa is a **learning project** with two goals:

1. Get hands-on, production-like experience with **dbt**.
2. Build a first **text-to-SQL agent** (the agent spec) on top of the resulting warehouse.

This spec covers the data platform: a synthetic e-commerce source system, loaded into local Postgres, and transformed by dbt into documented, tested, LLM-friendly marts. The agent spec consumes those marts.

Even though it is a toy project, the output must be **production-like**: a complete, explicitly specified data model, and dbt features used the way practitioners use them — each with a real purpose, introduced incrementally.

### Goals
- A realistic e-commerce source model spanning shop, web tracking, marketing and CRM systems.
- A dbt project that uses **every dbt Core feature that works on Postgres**, each where it has a genuine job, introduced across milestones data-platform-1–data-platform-5.
- Marts that are fully documented (table, column, metric definitions), contract-enforced, and designed so that common business questions need **at most two joins**.
- A deterministic synthetic data generator that simulates time passing, so freshness, snapshots and incremental models have something real to do.

### Non-goals
- Inventory/warehouse management, product reviews, multi-currency (everything is EUR).
- BI dashboards. The UI's Home screen (UI spec) is the only dashboard, and it is declared as a dbt exposure (data-platform-4).
- Streaming / real-time ingestion.
- Scale or performance beyond a laptop.
- dbt Cloud features (see §9).

### Anti-goals (would make this a failure even if it technically works)
- The generator becomes a project of its own. It is a simple day-by-day simulation with a fixed list of realism rules; it does not grow beyond that.
- dbt features used only to tick a box. Every feature in §8 states its purpose.
- Marts that leak raw-system quirks (casing, duplicates, test accounts) to consumers.

---

## 2. Constraints & Tooling

| Concern | Decision |
|---|---|
| Language | Python 3.12, managed with **uv** (single `pyproject.toml`, dependency groups `generator`, `dbt`, `dev`; the agent spec adds its own groups) |
| Database | **Postgres 16** in Docker Compose (`postgres:16-alpine`), named volume |
| dbt | **dbt Core** latest stable 1.x at implementation time (≥ 1.10), with `dbt-postgres`; versions pinned in `pyproject.toml`; packages pinned in `packages.yml` |
| dbt packages | `dbt-labs/dbt_utils`; **`metaplane/dbt_expectations`** (maintained fork of the archived calogica package — verify exact hub name when pinning) |
| Lint / format | `ruff` (Python), `sqlfluff` with the dbt templater (SQL), wired through `pre-commit` |
| Task runner | `Makefile` (targets listed in §10) |
| Secrets | `.env` (gitignored) + `.env.example`; `profiles.yml` is committed and reads everything via `env_var()` |
| Repo | Will live on GitHub; CI via GitHub Actions (milestone data-platform-4) |

### Repository layout

```
sql-sherpa/
├── docker-compose.yml  .env.example  Makefile  pyproject.toml
├── data-platform/                   # everything this spec builds
│   ├── postgres/init/00_roles.sh    #   roles (.sh so it can read passwords from the environment)
│   ├── generator/                   #   package: sherpa_generator
│   │   ├── src/sherpa_generator/
│   │   │   ├── cli.py, settings.py
│   │   │   ├── records/        # Pydantic models of every raw table + StrEnums (the §4 source contract)
│   │   │   ├── database/       # connection, raw schema DDL, COPY writer for records
│   │   │   ├── simulation/     # day loop, state, deterministic randomness, primary keys,
│   │   │   │                   #   per-day changes, store snapshot, realism rules R1–R12 (§4.4)
│   │   │   └── behaviour/      # one simulator class per business area (catalog, traffic, CRM,
│   │   │                       #   orders, after-sales, ad spend, …)
│   │   └── tests/              # mirrors the package layout
│   ├── dbt/                         #   dbt project: `sherpa`
│   │   ├── dbt_project.yml  profiles.yml  packages.yml  selectors.yml
│   │   ├── models/{staging,intermediate,marts}/
│   │   └── snapshots/  seeds/  macros/  tests/  analyses/
│   ├── scripts/                     #   helper scripts (the dbt docs-coverage check)
│   └── tests/                       #   tests across generator and dbt (seeds, agent access to the marts, scripts)
├── agent/                           # the agent spec
├── ui/                              # the UI spec
├── .github/workflows/ci.yml
└── docs/                            # specs, plans, guides
```

---

## 3. Database Topology

### Roles (created by `data-platform/postgres/init/00_roles.sh`)

The init script is a shell script (not `.sql`) because the Postgres image runs `.sql` init files without environment substitution; the script calls `psql` with passwords taken from the container environment (`.env`).

| Role | Privileges | Used by |
|---|---|---|
| `sherpa_admin` | Owner of database `sherpa` | Generator, dbt |
| `sherpa_agent` | `LOGIN`, no privileges at creation. Receives `USAGE` on `marts` and `SELECT` on mart relations **via dbt** (`grants` + hook, §8). Role default `default_transaction_read_only = on`, `statement_timeout = 5s`. Like every role but the owner it cannot create temporary tables, large objects (`EXECUTE` on `lo_create`, `lo_creat`, `lo_from_bytea`, `lo_open`, `lowrite`, `lo_put` is revoked from `PUBLIC` in the main database and in `template1`, so the eval database inherits it) or connect to the `postgres` database. | The agent's MCP server |
| `sherpa_ui` (ui-1, created by the idempotent roles script that `make roles` re-runs on existing volumes) | Like `sherpa_agent`: `LOGIN`, `USAGE` on `marts` and `extensions` and `SELECT` on mart relations via the same dbt `grants` and hook, the same `PUBLIC` revokes. In addition, `USAGE` on `ops` and `SELECT` on `ops.dbt_run_results`, for the UI's build status (§8, "Added by ui-1"). Its connections set `statement_timeout = 30s` (UI spec §5). | The UI backend |

Passwords come from `.env`.

### Schemas

| Schema | Owner of contents | Content |
|---|---|---|
| `raw_shop`, `raw_events`, `raw_marketing`, `raw_crm` | Generator | Source systems (§4) |
| `raw_meta` | Generator | `simulation_state` (§5.3) |
| `seeds`, `staging`, `intermediate`, `snapshots`, `marts`, `ops`, `test_failures` | dbt | dbt-managed (prod target) |
| `extensions` | dbt (`on-run-start` hook) | Postgres extensions (`pg_trgm`). Kept out of `public`, which the init script locks down, so `sherpa_agent` can be granted `USAGE` on it alone. The database's `search_path` includes it, so `similarity()` and `%` resolve unqualified. |

**Schema naming:** a custom `generate_schema_name` macro uses the custom schema name verbatim on the `prod` target and prefixes it with `dev_` on the `dev` target (e.g. `dev_marts`). Nodes without a custom schema use the target's schema: `dev_public` on `dev`, `public` on `prod`. **Exception: snapshots always write to `snapshots`** regardless of target — a snapshot is captured history that cannot be rebuilt, so there is exactly one copy, and dev and prod models both read it. `dev` is the default target; the agent reads the `prod` `marts` schema.

**Artifacts:** prod commands use `--target-path target-prod`, so `data-platform/dbt/target-prod/` always holds the prod `manifest.json` / `catalog.json` (the agent interface and `--state` for slim builds) and dev runs never overwrite it. Dev runs use the default `data-platform/dbt/target/`.

**Additional targets:**
- `eval` (data-platform-2): like `prod`, but on database `SHERPA_EVAL_DB_NAME` (default `sherpa_eval`) with `--target-path target-eval`. `make eval-db` builds it reproducibly: it creates the database and revokes all privileges on its `public` schema and `TEMPORARY` on the database from `PUBLIC` (as the init script does for the main database; the large-object revokes come from `template1`), runs the generator with `--seed 42 --start-date 2025-01-01 --days 365`, then runs `dbt build` and `dbt docs generate`. This gives the agent's evals a frozen dataset with `as_of_date` = 2025-12-31.
- `agent` (agent-design-9): like `prod`, but with `sherpa_agent` credentials. It is used only for read-only compilation, namely MetricFlow's `--explain`, so it is added to `profiles.yml` by the milestone that first runs it.

**Dates and time zones:** every timestamp is converted to UTC in staging; every `*_date` column (`order_date`, `session_date`, `refund_date`, `spend_date`, `as_of_date`) is a UTC calendar date.

---

## 4. Source Data Model (what the generator writes)

21 tables across 4 source systems. All monetary amounts are `numeric(12,2)` EUR. All shop timestamps are `timestamptz` in UTC. IDs are `bigint` unless stated.

### 4.1 `raw_shop` (Shopify-like OLTP)

| Table | Columns | Notes |
|---|---|---|
| `customers` | id, email, first_name, last_name, country_code, marketing_opt_in (bool), is_test_account (bool), created_at, updated_at, deleted_at (nullable) | Signup = `created_at`. Soft delete sets `deleted_at` and nulls nothing (GDPR anonymization happens in dbt). |
| `addresses` | id, customer_id, type (`shipping`\|`billing`), country_code, city, postal_code, is_default | 1–3 per customer |
| `categories` | id, parent_id (nullable), name | 2–3 levels, e.g. Footwear › Running › Trail |
| `products` | id, category_id (leaf), name, brand, is_active, created_at, updated_at | |
| `product_variants` | id, product_id, sku, size, color, cost_price, list_price, created_at, updated_at | Prices mutate over time (R10); `updated_at` bumps on every change |
| `orders` | id, order_number (text, `SS-000123`), customer_id, shipping_address_id, status (`placed`\|`paid`\|`shipped`\|`delivered`\|`cancelled`), subtotal, discount_total, shipping_amount, tax_amount, order_total, sales_channel (`web`\|`app`), placed_at, updated_at | `status` = current status. `order_total = subtotal − discount_total + shipping_amount + tax_amount` |
| `order_items` | id, order_id, variant_id, quantity, unit_price | `unit_price` = variant list price at time of order, net of tax, before discounts |
| `order_status_history` | id, order_id, status, changed_at | One row per transition, including `placed` |
| `payments` | id, order_id, method (`card`\|`paypal`\|`gift_card`), amount, status (`authorized`\|`captured`\|`failed`), created_at | Multiple rows per order possible (R7) |
| `shipments` | id, order_id, carrier, shipping_cost, shipped_at, delivered_at (nullable) | `shipping_cost` = cost to the shop, not to the customer |
| `promotions` | id, name, type (`percent`\|`fixed`\|`free_shipping`), value, min_order_value, campaign_id (nullable, refers to `raw_marketing.campaigns`), starts_at, ends_at | |
| `coupon_codes` | id, promotion_id, code, max_redemptions (nullable), is_single_use | |
| `order_discounts` | id, order_id, promotion_id, coupon_code_id (nullable), amount | Order-level only; allocation to lines is dbt's job |
| `returns` | id, order_id, reason (`size`\|`damaged`\|`not_as_described`\|`changed_mind`), status (`requested`\|`approved`\|`received`\|`rejected`), requested_at, updated_at | |
| `return_items` | id, return_id, order_item_id, quantity, is_restockable | |
| `refunds` | id, order_id, return_id (nullable), payment_id, amount, reason (`return`\|`cancellation`\|`goodwill`), refunded_at | `amount` is what was paid back, **including tax** |

### 4.2 `raw_events` (Segment-like tracker)

| Table | Columns | Notes |
|---|---|---|
| `events` | event_id (uuid text), anonymous_id (text), user_id (text, nullable — customer id as string), event_type (`page_view`\|`product_view`\|`add_to_cart`\|`remove_from_cart`\|`begin_checkout`\|`purchase`\|`signup`\|`login`), occurred_at (**text**, ISO-8601 with client UTC offset), received_at (timestamptz), context (jsonb: `device_type`, `utm_source`, `utm_medium`, `utm_campaign`, `referrer`), properties (jsonb: `page_path`, `product_id`, `variant_id`, `order_id`, `cart_value`) | `device_type` is `desktop`, `mobile` or `tablet`. `cart_value` is a JSON string (e.g. `"20.00"`) inside the jsonb payload: on `add_to_cart` and `begin_checkout` the price of the carted item, on `purchase` the subtotal of the order it created. No session id. Staging parses text timestamps and extracts JSON. |

### 4.3 `raw_marketing` and `raw_crm`

| Table | Columns | Notes |
|---|---|---|
| `raw_marketing.campaigns` | id, name, utm_campaign (slug), channel (`paid_search`\|`paid_social`\|`email`\|`affiliate`\|`display`), starts_at, ends_at, budget | |
| `raw_marketing.ad_spend_daily` | spend_date (date), campaign_id, spend, impressions, clicks | Paid channels only |
| `raw_crm.leads` | id, email, source (`newsletter`\|`popup`\|`giveaway`\|`referral`), utm_campaign (nullable), lifecycle_stage (`subscriber`\|`lead`\|`mql`\|`customer`), created_at, updated_at | **No FK to customers** — matched on normalized email in dbt. `utm_campaign` is always NULL: leads carry a source, not a campaign |
| `raw_crm.email_sends` | id, lead_id, campaign_id (nullable), sent_at, opened_at (nullable), clicked_at (nullable) | |

### 4.4 Realism rules (fixed list — the generator implements exactly these)

| # | Rule | Rate / shape | dbt must… |
|---|---|---|---|
| R1 | Duplicate events (same `event_id`, and a `received_at` on the same UTC day as the original) | 1% of events | dedupe in staging |
| R2 | Late-arriving events (`received_at` 1–3 days after `occurred_at`) | 2% of events | use `received_at` for incremental filtering with lookback |
| R3 | Email casing / whitespace noise (customers and leads) | 10% | normalize `lower(trim(email))` |
| R4 | Country code noise (`uk`, lowercase, `UK` for `GB`) | 3% of customers/addresses | normalize to ISO alpha-2 via seed |
| R5 | Internal test accounts (`@sqlsherpa-test.com`) that place orders | 0.5% of customers | exclude from all marts |
| R6 | Soft-deleted customers (GDPR) | 1% of customers, 30–199 days after signup; a deletion takes effect at the end of its day (`deleted_at` = the day's last microsecond), so no order is placed and no event occurs after `deleted_at` | keep, anonymize PII in marts |
| R7 | Failed payment attempts; split payments (gift card + card) | 3% failed then retried; 5% split | sum only `captured` |
| R8 | Multiple discounts per order (promotion + coupon) | 3% of orders | allocate the sum |
| R9 | Partial returns; refunds without returns (goodwill, cancellation) | see §5.2 | separate returns from refunds |
| R10 | Price changes over time | 0.3% of variants change `list_price` (±5–20%) per day; 0.1% change `cost_price` | cost at time of sale via snapshot |
| R11 | Ad-platform clicks ≠ tracked sessions | clicks = paid sessions × U(0.85, 1.15) | don't treat them as equal |
| R12 | App orders have no web events | 35% of orders are `app` | sessions and orders don't fully reconcile |

Plus baseline distributions: weekly cycle (weekends +20%), seasonality (Black Friday week ×3, December ×1.6, January ×0.7), and a Pareto-like customer value distribution (lognormal propensity drives repeat purchases; the top 20% of buyers produce roughly 55–65% of net revenue). Black Friday is the day after the fourth Thursday of November; its week runs Monday to Sunday. These distribution targets are soft: the data aims to be plausible rather than to hit exact numbers.

---

## 5. Generator

### 5.1 Shape

A **day-by-day simulation**. One command:

```
sherpa-generate generate --days N [--start-date YYYY-MM-DD] [--scale S] [--seed K]
```

- **First run** (empty `raw_meta.simulation_state`): runs setup (catalog, campaigns, promotions) then simulates `N` days ending yesterday, unless `--start-date` is given. Output depends only on seed, scale and start date (for the range the catalog covers, §5.2 Setup).
- **Later runs:** continue from the day after `last_simulated_date` for `N` days, **never beyond yesterday** (wall clock), so source freshness behaves realistically. If there is nothing left to simulate, exit 0 with a message.
- `sherpa-generate reset` drops and recreates the `raw_*` schemas. `sherpa-generate status` prints the simulated range and row counts.
- Each simulated day is written in **one transaction** (all-or-nothing); `simulation_state` updates in the same transaction. A crash mid-day leaves the database at the end of the previous day.
- **Determinism:** the RNG for day *d* is `random.Random` / `numpy.random.default_rng` seeded with a stable integer derived from `hashlib.sha256(f"{seed}:{d.isoformat()}")` — never Python's built-in `hash()`, which varies per process. **The generator assigns all primary keys itself** from counters stored in `simulation_state` (not Postgres sequences, which would leak values on rolled-back days). Given the same seed, start date and scale, two fresh runs produce identical data. Lifecycle progression for open orders/returns is derived by querying the database, so resumed runs equal one long run.
- **Late events (R2):** an event is written in the batch of the simulated day that contains its `received_at`. Events generated on day *d* with a later `received_at` go to `raw_meta.pending_events` (same transaction) and are flushed when that day is simulated. Events pending past yesterday stay pending — no row ever has a `received_at` in the future.
- Bulk writes use `COPY` via psycopg 3. Plain Python + Faker; no vectorization requirement.

### 5.2 Daily simulation (scale = 1.0)

Per simulated day (before seasonality multipliers):

1. **Catalog drift:** apply R10 price changes; activate/deactivate ~0.1% of products.
2. **Leads:** ~16 new leads; email sends for active campaigns and a weekly newsletter (Tuesdays) to all `subscriber`+ leads; open 25%, click 3% of sends.
3. **Sessions:** ~400 sessions, channel mix: direct 25%, organic 25%, paid_search 18%, paid_social 12%, email 10%, affiliate 5%, referral 5%. Each anonymous visitor has 1–3 devices (anonymous_ids). Funnel per session: `page_view` → 0–6 `product_view` (70% view ≥1) → `add_to_cart` of one viewed variant (30% of viewing sessions) → `begin_checkout` (45% of carts) → `purchase` (70% of a returning customer's checkouts; new visitors see step 4). ~38% of sessions come from existing customers, chosen by propensity. Overall web session conversion ≈ 3.1%.
4. **Signups:** ~12/day, by new visitors at checkout; only 20% of them buy in that session, so many accounts never order and most orders are existing customers' repeat purchases. ~35% of signups match an existing lead's email (with R3 noise); events after `signup`/`login` carry `user_id`.
5. **Orders:** every `purchase` event creates a `web` order and carries its `order_id` and subtotal (as `cart_value`); `app` orders are added so they make up 35% of orders (R12). Repeat purchases follow each customer's propensity. 1–4 distinct lines per order (50% one line, 30% two, 15% three, 5% four): a web order's first line is the variant its session carted and the other lines are drawn by popularity; app order lines are all drawn by popularity. Discounts per active promotions (25% of orders discounted). Shipping €4.95, free above €75 or with `free_shipping`. Tax 20% on `subtotal − discount_total`.
6. **Lifecycle progression of open orders:** `placed → paid` same day (R7 failures retried; 2% never paid → `cancelled` next day); `paid → shipped` after 1–3 days; `shipped → delivered` after 2–5 days; 3% of paid orders cancelled before shipping (refund reason `cancellation`).
7. **Returns & refunds:** 12% of delivered orders get a return within 30 days (footwear 2× more likely, reason `size`); 1–all lines, partial quantities allowed; 90% approved; refund on `received` 3–10 days after request, booked on the order's largest captured payment (on a split payment a refund can exceed that single payment; an order's refunds never total more than the amount captured), amount = returned lines' net value (after allocated discount) incl. tax. 1% of delivered orders get a goodwill refund (one outcome draw per order, so an order gets at most one of a return or a goodwill refund) of 5–20% of `(subtotal − discount_total + tax_amount)` — i.e. never refunding shipping. Cancellation refunds return the full captured amount (incl. shipping).
8. **Ad spend:** one row per active paid campaign, with R11.

**Expected totals for 365 days at scale 1.0 (±15%):** ~5k customers, ~8k orders, ~150k sessions, ~600k events, ~6k leads. These volume, rate and distribution numbers are approximate targets for plausible synthetic data, not exact goals. `--scale` multiplies all daily volumes. Generation of 365 days completes in under 5 minutes on a laptop.

**Setup (once):** 4 L1 categories (Footwear, Apparel, Accessories, Equipment), ~12 L2, ~25 L3; ~300 products across ~25 brands; ~1,500 variants; cost = 30–60% of list price. ~20 promotions over the year (including a Black Friday sitewide 20%) and ~15 campaigns. The catalog (campaigns, promotions, coupon codes) is built once, on the first run, from the start date up to the earlier of yesterday and 365 days after it (`CATALOG_HORIZON_DAYS`), whatever `--days` is; days simulated after that have no active campaigns, promotions or ad spend.

### 5.3 State

- `raw_meta.simulation_state(id=1, seed, scale, start_date, last_simulated_date, next_ids jsonb, updated_at)` — `next_ids` holds the per-table PK counters.
- `raw_meta.pending_events` — same columns as `raw_events.events`; late events awaiting their `received_at` day.

Changing `seed` or `scale` against existing state is an error (use `reset`).

### 5.4 Acceptance
- Unit tests for each realism rule (rate within tolerance on a 30-day run at scale 0.2).
- Two fresh 30-day runs with the same parameters produce identical per-table row counts and identical `md5` of ordered primary keys.
- Killing the process mid-day leaves `last_simulated_date` and data consistent (verified by a test that raises inside the day transaction).

**Known simplifications** that later milestones have to account for:
- Ad spend is budget-driven, so cost per click is not realistic; ROAS and CAC (data-platform-4) reflect the budgets, not click prices.
- `remove_from_cart` events are never generated.
- All coupon codes are unlimited: none is single-use.

---

## 6. dbt Transformation Layers

Naming: `stg_<source>__<entity>`, `int_<entity>__<verb>`, `fct_` / `dim_`. YAML per directory (`_<dir>__models.yml`, `_<dir>__sources.yml`).

### 6.1 Seeds
- `countries` (code, name, region, is_eu) — one row per country; `code` is the canonical ISO alpha-2 code and the primary key.
- `country_code_variants` (raw_value, code) — one row per raw country value the shop can emit (each canonical code, its lowercase form, `UK`/`uk` for `GB`), mapped to its `countries.code` (R4).
- `channel_mapping` (utm_source, utm_medium, marketing_channel) — maps raw UTMs to the 8 channels: `direct`, `organic`, `paid_search`, `paid_social`, `email`, `affiliate`, `referral`, `app`.

### 6.2 Staging (views, 1:1 with source)
Rename to consistent `<entity>_id`, cast types, normalize (R3, R4), parse JSON/text timestamps (events), no joins except to seeds. Exception: **`stg_events__events` is incremental using the `microbatch` strategy** (`event_time: received_at`, `batch_size: day`, `lookback: 3`, `begin` = simulation start date via var), deduping R1 with `row_number()` over `event_id` inside each batch. This is correct because the generator guarantees duplicates share the original's `received_at` day (R1). Microbatch has no `unique_key`; the `unique` test on `event_id` guards the assumption.

### 6.3 Snapshot
`snap_product_variants` (YAML-defined), `timestamp` strategy on `updated_at`, `hard_deletes: invalidate`. Because the timestamp strategy uses `updated_at` as `dbt_valid_from`, simulated time is preserved. **From data-platform-3, `make backfill` runs the generator in 7-day chunks and `dbt snapshot` after each chunk**, so price history during backfill is captured. In data-platform-1 and data-platform-2, `make backfill` runs the generator once and takes no snapshot: list-price and cost history exists only as the current values in raw `product_variants`, so a data-platform-2 mart that needs the cost at time of sale uses the current cost until the snapshot exists. Two changes to the same variant within one 7-day chunk are rare (~25 variant-weeks per year at scale 1.0); the intermediate one is lost, which is harmless. Known limitations, documented: an order placed before the first captured version uses the earliest version's cost. Backfill and advance always snapshot with `--target prod` (snapshots live in the single `snapshots` schema, §3).

### 6.4 Intermediate (not exposed to consumers)

| Model | Materialization | Logic |
|---|---|---|
| `int_identity__anonymous_to_customer` | **incremental, `merge`** on `anonymous_id` | anonymous_id → customer_id (most recent identifying event wins; new identifying events update the mapping) |
| `int_identity__lead_to_customer` | ephemeral (data-platform-2) | lead ↔ customer on normalized email, earliest lead wins |
| `int_events__sessionized` | **table** in data-platform-2; **incremental, `delete+insert`** on `session_date` from data-platform-3 | One row per event, carrying its session's columns. 30-minute inactivity rule (var `session_timeout_minutes`): a gap **greater than** the timeout starts a new session, per anonymous_id. `session_id = md5(anonymous_id || '|' || epoch microseconds of session_start_at)`, so it does not depend on the connection's time zone. Sessions containing an event of a test account (R5) are excluded. The session's first event decides `marketing_channel`: a UTM source/medium pair in the `channel_mapping` seed maps through it; a UTM pair missing from the seed is `unknown`; with no UTMs, a search-engine referrer (google, bing, duckduckgo) is `organic`, any other referrer `referral`, and no referrer `direct`. Each row stores its session's `max_received_at`. **On incremental runs (data-platform-3):** watermark = `max(max_received_at)` from `{{ this }}`; cutoff day = `date_trunc('day', min(occurred_at) of events with received_at > watermark) − 1 day`; recompute every session with `session_date ≥ cutoff day` from all events with `occurred_at ≥ cutoff day − 1 day` (so sessions spanning midnight into the cutoff are seen whole) |
| `int_order_items__discount_allocated` | view | Allocate `Σ order_discounts.amount` pro-rata to lines by gross amount; round to cents; put the rounding remainder on the largest line so lines sum exactly to the order discount |
| `int_order_items__costed` | ephemeral | Unit cost at sale. In data-platform-2 this is the variant's current `cost_price`; from data-platform-3 it comes from `snap_product_variants` where `placed_at` ∈ [valid_from, valid_to) |
| `int_orders__status_pivoted` | ephemeral | first `paid_at`, `shipped_at`, `delivered_at`, `cancelled_at` from status history |
| `int_orders__payments_summarized` | ephemeral | per order: captured amount, payment count, failed attempts, primary method |
| `int_orders__refunds_summarized` | view | per order: refunded amount, refunded revenue (per §6.6 rules) |
| `int_order_items__returns_summarized` | view | per order line with a received return: returned quantity, restockable returned quantity, line refunded revenue |

Models with unit tests (§7) are views, not ephemeral, so they can be unit-tested directly.

### 6.5 Marts (tables, contracts enforced, fully documented)

**Global rules:** test accounts (R5) and all their orders/events are excluded everywhere. Soft-deleted customers (R6) are kept with `email`, `first_name`, `last_name` set to null and `is_deleted = true`, so facts keep referential integrity. **`as_of_date`** = `max(order_date)` over `fct_orders` (non-test orders, UTC dates; computed from the same CTE inside models that need it). All "days since" columns use it, never `now()`, so results are deterministic.

**Cancelled orders.** Amount columns describe the order as placed and keep their values when it is cancelled: `gross_revenue`, `discount_amount`, `shipping_amount`, `tax_amount`, `order_total`, and at line level `gross_amount`, `allocated_discount`, `net_amount`. Outcome columns are 0 for a cancelled order, because nothing was sold: `net_revenue`, `cogs`, `returned_cogs`, `gross_margin` (and the line's `cogs`). Metrics over "non-cancelled orders" filter on `is_cancelled`.

**Marketing channel of an order.** A web order takes the `marketing_channel` and `session_id` of the session containing its `purchase` event; an app order has `marketing_channel = 'app'` and no session. A web order whose purchase event has not arrived yet (late delivery, R2) has `marketing_channel = 'unknown'` and no session until the event lands. Order-level channel columns therefore hold the 8 channels of §6.1 plus `unknown`.

**Domains** (`meta.domain`, consumed by the agent): `sales`, `customers`, `products`, `marketing_web`, `shared`.

**Mart metadata:**
- Every mart carries `meta: {domain, grain, primary_key}` (data-platform-1 on `dim_date`, data-platform-2 on the other marts). `primary_key` is a column name or a list of column names.
- Columns holding PII carry `meta.pii: true`.
- Columns and models that carry business terms carry `meta.synonyms`, a list of the words people use for them, e.g. `net_revenue: ["revenue", "net sales"]`, `customer_segment: ["lifecycle stage"]`.

#### Dimensions

**`dim_customers`** — 1 row per customer · PK `customer_id` · domain `customers`
customer_id, email (pii), first_name (pii), last_name (pii), country_code, country_name, region, signup_at, marketing_opt_in, is_deleted, lead_id, lead_source, acquisition_channel, acquisition_campaign_id, first_order_at, last_order_at, order_count, lifetime_gross_revenue, lifetime_net_revenue, lifetime_refunded_revenue, avg_order_value, days_since_last_order, customer_segment, is_vip
- Order aggregates are computed from `fct_orders` (mart-to-mart ref), not from intermediate order models.
- `acquisition_campaign_id` = campaign of the first non-cancelled order's session (null if none).
- `acquisition_channel` = `marketing_channel` of the customer's first non-cancelled order (`app` for app orders; `unknown` if no orders).
- `customer_segment`: `prospect` (0 orders), `new` (1 order, last ≤ 90 days ago), `active` (≥ 2 orders, last ≤ 90 days), `at_risk` (last order 91–180 days), `churned` (> 180 days).
- `is_vip` = in the top 10% of buyers by `lifetime_net_revenue`.
- Counts and revenue use non-cancelled orders only.

**`dim_products`** — 1 row per variant · PK `variant_id` · domain `products`
variant_id, sku, product_id, product_name, brand, size, color, category_l1, category_l2, category_l3 (null when the leaf is L2), is_active, current_list_price, current_cost_price, product_created_at

**`dim_promotions`** — 1 row per promotion · PK `promotion_id` · domain `sales`
promotion_id, promotion_name, promotion_type, value, min_order_value, campaign_id, starts_at, ends_at, coupon_code_count, redemption_count, total_discount_amount

**`dim_campaigns`** — 1 row per campaign · PK `campaign_id` · domain `marketing_web`
campaign_id, campaign_name, utm_campaign, channel, starts_at, ends_at, budget

**`dim_date`** — 1 row per day · PK `date_day` · domain `shared`
date_day, week_start, month_start, quarter, year, day_of_week_name, is_weekend, is_black_friday_week (Monday to Sunday of the week containing Black Friday, §4.4). Range: static vars `date_spine_start` (default `2024-01-01`) and `date_spine_end` (default `2028-12-31`) passed to `dbt_utils.date_spine` — static so the spine never depends on data at compile time. Also serves as the MetricFlow time spine (data-platform-5).

#### Facts

**`fct_orders`** — 1 row per order (cancelled orders included, flagged) · PK `order_id` · domain `sales`
order_id, order_number, customer_id, order_date, placed_at, paid_at, shipped_at, delivered_at, cancelled_at, current_status, is_cancelled, customer_order_number, is_first_order, sales_channel, marketing_channel, session_id, ship_country_code, item_count, gross_revenue, discount_amount, shipping_amount, tax_amount, order_total, captured_amount, primary_payment_method, payment_count, failed_payment_count, refunded_amount, refunded_revenue, net_revenue, cogs, returned_cogs, gross_margin, has_discount, has_return, days_to_deliver
- `customer_order_number` numbers the customer's non-cancelled orders by `placed_at`, then `order_id` (1 = first); null for cancelled orders. `is_first_order` = `customer_order_number = 1` (false for cancelled orders). They agree with `dim_customers`: each customer with `order_count > 0` has exactly one first order, placed at `first_order_at`, and the highest number equals `order_count` (singular test `assert_first_orders_match_dim_customers`).
- `primary_payment_method` is the method of the largest captured payment (lowest `payment_id` on a tie), `card`, `paypal` or `gift_card`; null when nothing was captured. `payment_count` counts every payment attempt and `failed_payment_count` the failed ones (0 when none).

**`fct_order_items`** — 1 row per order line · PK `order_item_id` · domain `sales`
order_item_id, order_id, customer_id, variant_id, product_id, order_date, is_cancelled, quantity, unit_price, gross_amount, allocated_discount, net_amount, unit_cost_at_sale, cogs, returned_quantity, restockable_returned_quantity, refunded_revenue, returned_cogs, gross_margin
- Line-level `refunded_revenue` covers return refunds only; goodwill and cancellation refunds exist only at order level. It is the line's allocated `net_amount` pro-rated by its received returned quantity, `round(returned_quantity × net_amount / quantity, 2)`. The order-level `fct_orders.refunded_revenue` (from the refund amounts, §6.6) is authoritative. Because each line is rounded on its own, the lines' sum can exceed it by at most 0.01 per refunded line: `Σ lines.refunded_revenue ≤ fct_orders.refunded_revenue + 0.01 × (lines with refunded_revenue > 0)` (singular test `assert_line_refunds_within_order_refunds`).
- `returned_cogs` = `restockable_returned_quantity × unit_cost_at_sale` and `gross_margin` = `net_amount − refunded_revenue − (cogs − returned_cogs)`, both 0 for a cancelled order: the order-level definitions (§6.6) applied to the line, for margin by product, brand or category. The lines' `returned_cogs` add up exactly to the order's (`column_sums_match`). Their `gross_margin` uses the line `refunded_revenue`, so per order the lines add up to `fct_orders.gross_margin` plus the order's goodwill refunded revenue, within 0.01 per refunded line (singular test `assert_line_margins_match_order_margins`).

**`fct_order_discounts`** — 1 row per discount applied to an order · PK `order_discount_id` · domain `sales`
order_discount_id, order_id, customer_id, order_date, promotion_id, coupon_code, promotion_type, amount, is_cancelled
- The bridge from orders to promotions, for orders in `fct_orders` (non-test, cancelled included and flagged). `coupon_code` is the code entered, null when the promotion applied automatically; `amount` is 0.00 for `free_shipping` discounts (§6.6 "has a discount").
- An order uses a promotion at most once (`unique_combination_of_columns` on `order_id`, `promotion_id`). Per order, Σ `amount` = `fct_orders.discount_amount` (`column_sums_match`). Every discount comes from a promotion, so per promotion the row count and Σ `amount` equal `dim_promotions.redemption_count` and `total_discount_amount` (singular test `assert_order_discounts_match_promotions`).

**`fct_returns`** — 1 row per return · PK `return_id` · domain `sales`
return_id, order_id, customer_id, reason, status, requested_at, returned_item_quantity, restockable_quantity, refund_id, refunded_amount

**`fct_refunds`** — 1 row per refund · PK `refund_id` · domain `sales`
refund_id, order_id, customer_id, return_id, refund_reason, refunded_at, refund_date, amount, revenue_amount, days_since_order

**`fct_sessions`** — 1 row per session · PK `session_id` · domain `marketing_web`
session_id, anonymous_id, customer_id (stitched, nullable), session_date, session_start_at, session_end_at, duration_seconds, event_count, page_view_count, landing_page_path, device_type, utm_source, utm_medium, utm_campaign, marketing_channel, campaign_id, is_first_session, viewed_product, added_to_cart, began_checkout, purchased, order_id

**`fct_ad_spend_daily`** — 1 row per campaign × day · PK (`campaign_id`, `spend_date`) · domain `marketing_web`
spend_date, campaign_id, channel, spend, impressions, clicks, sessions, orders, attributed_net_revenue, new_customers
- Attribution is **last-touch session**: an order is attributed to the campaign of the session in which `purchase` occurred.
- `new_customers` = customers whose `acquisition_campaign_id` is this campaign and whose first order falls on `spend_date`. Campaign-level, so it sums correctly across campaigns and channels.

**`fct_leads`** — 1 row per lead · PK `lead_id` · domain `customers`
lead_id, email (pii), lead_source, utm_campaign, lifecycle_stage, created_at, is_converted, converted_customer_id, converted_at, days_to_convert, emails_sent, emails_opened, emails_clicked

### 6.6 Metric definitions (single source of truth: doc blocks named `metric_<name>`, e.g. `metric_net_revenue`, reused in YAML)

| Metric | Definition |
|---|---|
| gross_revenue | Σ `quantity × unit_price` over **non-cancelled** orders. Net of tax; excludes shipping. |
| discount_amount | Σ order discounts (allocated at line level) |
| refunded_revenue | For reasons `return` and `goodwill`: refund `amount / (1 + tax_rate)` (tax portion removed; var `tax_rate`, default 0.20; neither reason ever refunds shipping, §5.2). For reason `cancellation`: **0** — the order is cancelled, so its revenue is already excluded and the refund only reverses the payment. `fct_refunds.amount` still shows every refund. **Attributed to the order's date** in `fct_orders`; to the refund's date in `fct_refunds`. `fct_order_items.refunded_revenue` is a pro-rated, per-line-rounded split of return refunds only (§6.5); the order-level figure is authoritative. |
| net_revenue | gross_revenue − discount_amount − refunded_revenue, over non-cancelled orders. For cancelled orders, `fct_orders.net_revenue` = 0. |
| cogs / gross_margin | cogs = Σ quantity × unit cost at sale; returned_cogs = restockable returned qty × unit cost at sale; gross_margin = net_revenue − (cogs − returned_cogs). `fct_order_items` carries the same per line, with the line's `net_amount − refunded_revenue` as its net revenue (§6.5). |
| AOV | net_revenue / count of non-cancelled orders |
| conversion_rate | sessions with `purchased` / all sessions |
| refund_rate | refunded_revenue / gross_revenue |
| ROAS | attributed_net_revenue / spend |
| CAC | Σ `fct_ad_spend_daily.spend` / Σ `fct_ad_spend_daily.new_customers`, for any grouping of campaigns, channels and dates |

**Has a discount.** An order has a discount when it has at least one `order_discounts` row with `amount > 0`. Rows of a `free_shipping` promotion have `amount = 0.00` (shipping is waived), so counting rows would overstate the discounted share.

Precision: amounts `numeric(12,2)`; rates computed as `numeric` and rounded only at presentation.

---

## 7. Testing Strategy (inside dbt)

| Kind | Where / what |
|---|---|
| Generic (built-in) | `unique` + `not_null` on every PK; `relationships` for every FK in marts; `accepted_values` on every enum column (the agent reuses these as grounding facts) |
| `dbt_utils` | `unique_combination_of_columns` (fct_ad_spend_daily; `order_id` × `promotion_id` in fct_order_discounts), `expression_is_true`, `accepted_range` |
| `dbt_expectations` | `expect_column_pair_values_A_to_be_greater_than_B` (gross ≥ net), `expect_column_values_to_match_regex` (email in staging), `expect_row_values_to_have_recent_data` (events, severity warn), `expect_table_row_count_to_be_between` and `expect_column_mean_to_be_between` (generator distributions, severity warn) |
| Custom generic | `column_sums_match(compare_model, compare_column, group_by)` — e.g. allocated line discounts sum to order discounts |
| Singular | sessions do not overlap per anonymous_id; captured payments = `order_total` for paid, non-cancelled orders; Pareto check (top 20% of buyers hold 55–65% of net revenue, warn otherwise); line margins, order discounts and first orders reconcile with `fct_orders`, `dim_promotions` and `dim_customers` (§6.5) |
| Unit tests | discount allocation rounding remainder; sessionization (29 vs 31 min gap; session across midnight; late event inserted into an existing session); `customer_segment` boundaries (90/91/180/181 days); refunded revenue tax removal |
| Freshness | `raw_events.events` (`received_at`), `raw_shop.orders` (`updated_at`), `raw_crm.leads` (`updated_at`): warn 36h, error 72h; `ad_spend_daily` on `spend_date::timestamp`: warn 48h. `dbt build` does not check freshness, so `make build` runs `dbt source freshness` first. |

Severity: key/integrity tests `error`; distribution and freshness tests `warn`. `store_failures` enabled for singular and custom tests, into the `test_failures` schema.

---

## 8. dbt Feature Roadmap (milestones)

Each milestone has one implementation plan, `docs/superpowers/plans/<date>-data-platform-<n>-<name>.md`. Each milestone ends with `dbt build` green on a fresh database and its acceptance criteria met. **Every plan's last task is a documentation task.** It updates `CLAUDE.md` and `README.md` wherever the milestone changed what they describe (commands, layout, status, conventions). It also writes or updates how-to guides in `docs/guides/basic/` (using what was built, with easy and complete examples) and `docs/guides/advanced/` (extending or modifying it). The guides list per milestone is part of the plan.

### data-platform-1 — Foundation
**Builds:** Docker Compose, roles, generator (full §5), dbt project, sources, seeds, all staging models, `dim_date`.
**dbt features:** project config, `profiles.yml` with `env_var()` and `dev`/`prod` targets, custom `generate_schema_name`, `--target-path` for prod artifacts, **sources + freshness** (`dbt source freshness`), **seeds** (with `column_types`), view materialization, **generic tests**, `dbt_utils`, `vars` (`tax_rate`, `session_timeout_minutes`, `date_spine_start/end`), `meta` on `dim_date` (`domain`, `grain`, `primary_key`), `dbt docs generate/serve`, a `__overview__` doc block. CLI: `dbt debug`, `dbt parse`, `dbt compile`, `dbt ls` (selection syntax), **`dbt show`** (preview a model's output without materializing it).
**Acceptance:** `make up && make backfill DAYS=365 && make deps && make build` succeeds, including `dbt source freshness` with no errors; every staging model and column has a description.

### data-platform-2 — Core marts
**Builds:** intermediate order models, `int_events__sessionized` (as a table), `int_identity__lead_to_customer`, `dim_customers`, `dim_products`, `dim_promotions`, `fct_orders`, `fct_order_items`, `fct_order_discounts`, `fct_returns`, `fct_refunds`. Sessionization and lead matching come here rather than later because `fct_orders` and `dim_customers` need their columns, and the agent's interface must be complete at the end of this milestone.
**dbt features:** ephemeral/table materializations, **macros & Jinja** (`revenue_excluding_tax`, `customer_segment`, `create_trigram_index`, `normalize_email`, `validate_identifier`), **custom generic test**, **singular tests**, **dbt_expectations**, **model contracts** (enforced, with `primary_key` and `not_null` constraints; FKs as `relationships` tests, not DB constraints), **doc blocks** for metrics (`metric_<name>`), `meta` extended to the other marts (`domain`, `grain`, `primary_key`, `pii`, `synonyms`), the `eval` target with `make eval-db` (§3), **`persist_docs`** (relations + columns) on marts, Postgres **`indexes`** config, **`grants`** (`select` to `sherpa_agent` and `sherpa_ui`) on marts, **hooks**: `on-run-start` creates the `extensions` schema and `pg_trgm` in it (a trusted extension in PG 16, so the database owner can create it) and puts `extensions` on the database's `search_path`; `on-run-end` grants `USAGE` on `marts` and `extensions` to `sherpa_agent` and `sherpa_ui` on the prod and eval targets only; post-hook `ANALYZE` on marts. B-tree indexes via the `indexes` config. GIN trigram indexes on `dim_products.product_name`, `brand`, `category_l1/l2/l3` via a **`create_trigram_index(column)` macro called from a post-hook** (`DROP INDEX IF EXISTS <schema>.<name>` then `CREATE INDEX <name> … USING gin (col gin_trgm_ops)`; the drop removes the same-named index on dbt's `__dbt_backup` table, which still exists while post-hooks run), because the `indexes` config has no operator-class option. **`dbt run-operation`**: a `grant_read_access` macro that re-applies the agent and UI read grants on demand (same macro as the on-run-end hook).
**Cost at sale:** uses the variant's current cost until data-platform-3's snapshot exists (§6.3, §6.4).
**Acceptance:** all mart tests pass; `sherpa_agent` can `SELECT` from marts and no other application schema (system catalogs are readable by every role; the agent's SQL guard blocks them); contracts reject a deliberately wrong column type (verified once, manually).

### data-platform-3 — Time
**Builds:** `advance` workflow, snapshot (and `int_order_items__costed` switching to it), `int_events__sessionized` converted from a table to incremental, `fct_sessions`.
**dbt features:** **snapshot** (YAML), all three **incremental strategies** with a purpose each — `microbatch` (`stg_events__events`, incl. `--event-time-start/--event-time-end` for targeted backfills), `delete+insert` (`int_events__sessionized`), `merge` (`int_identity__anonymous_to_customer`) — `is_incremental()`, `{{ this }}`, `--full-refresh`, **unit tests**, `store_failures`, warn/error severity, **`dbt retry`** (resume a failed build from the failure point), `fct_sessions`.
**Acceptance:** after `make advance DAYS=3 && make build`, an incremental run gives the same `fct_sessions` as `--full-refresh` (verified by an `analyses/` comparison query returning zero diff rows); late events from R2 end up in the correct sessions.

### data-platform-4 — Marketing, governance, CI
**Builds:** `int_identity__anonymous_to_customer`, `fct_leads`, `fct_ad_spend_daily`, `dim_campaigns`, GitHub Actions.
**dbt features:**
- **Groups & access.** Groups mirror the domains: `sales`, `customers`, `products`, `marketing_web`, `shared` (each with an owner). Marts are `public`. Staging is `protected`. Intermediate models used by only one group are `private` to it (`int_order_items__*`, `int_orders__*` → `sales`; `int_identity__lead_to_customer` → `customers`). Intermediate models used across groups (`int_events__sessionized`, `int_identity__anonymous_to_customer`) are `protected` in `marketing_web`. This is possible because `dim_customers` aggregates from `fct_orders`, not from `int_orders__*`.
- **Exposures** for the real consumers of the marts, so lineage ends where the data is used (in the dbt docs and the UI's Lineage screen):
  - `sherpa_home` (type `dashboard`): the UI's Home screen, depending on exactly the marts its curated queries read (`fct_orders`, `fct_refunds`, `dim_date`)
  - `sherpa_agent` (type `application`): the text-to-SQL agent, used by the CLI and the UI's Ask screen, depending on every public mart
- **Analyses:** revenue reconciliation raw vs marts; incremental vs full-refresh diff.
- **A tag and a YAML selector, for CI:** the generator-distribution tests (`dbt_expectations` row-count and mean checks, the Pareto check) are tagged `distribution`. The `ci` selector builds everything except them, because CI's 30-day, scale-0.05 data can't meet distribution targets meant for a full year.
- **State and environment commands.** `make slim` runs `dbt build --select state:modified+ --defer --state target-prod` on `dev`. `make clone` runs **`dbt clone`** of prod relations into dev schemas, so dev can query full data without rebuilding. **`--empty`** is used in CI as a first schema-only build that catches SQL and contract errors in seconds.

**CI (`.github/workflows/ci.yml`, on PR; adds the `ci` dbt target to `profiles.yml`):** Postgres service container → `uv sync` → `ruff` + `sqlfluff lint` → generator unit tests → generator (30 days, scale 0.05) → `dbt deps/seed` → `dbt build --empty --target ci --selector ci` → `dbt build --target ci --selector ci`. Full build on tiny data: deferral needs persistent prod relations, which CI does not have, so slim/defer is demonstrated locally.
**Acceptance:** CI green on a PR; both exposures appear in the docs lineage graph with their dependencies.

### data-platform-5 — Semantic Layer
**Builds:** semantic models on `fct_orders`, `fct_order_items`, `fct_sessions`, `fct_ad_spend_daily`; metrics for every row of §6.6. Their consumer is the agent's MetricFlow variant (agent spec, agent-design-9).
**dbt features:** MetricFlow (`dbt-metricflow`, open-source CLI `mf`), time spine on `dim_date`, simple/ratio/derived metrics.
**Acceptance:** `mf query --metrics net_revenue --group-by metric_time__month` matches `Σ fct_orders.net_revenue` per month (non-cancelled) exactly.

### Added by ui-1 (UI spec)
The UI's first milestone makes three data-platform changes, because the UI needs them before data-platform-3 to 5:
- **The `sherpa_ui` role** (§3), with the idempotent roles script and `make roles` (§10).
- **An `on-run-end` audit hook** that writes one row per executed node to **`ops.dbt_run_results`**:
  - columns: `invocation_id`, `target_name`, `command`, `is_full_run` (true when the invocation had no `--select`, `--exclude` or `--selector`), `unique_id`, `resource_type`, `status`, `failures`, `execution_time_s`, `message`, `run_started_at`
  - `ops` is created by the hook if missing and dropped by `make reset`

  dbt rewrites `target-prod/run_results.json` on every command that compiles (`docs generate`, `run-operation`, `snapshot`), so the file can't tell the UI what the last full build did. The table keeps every run, and the UI reads the latest full prod build from it.
- **`make build-prod` also runs `dbt docs generate`**, so `catalog.json` is always current for the UI.

**Acceptance:** after `make build-prod`, `ops.dbt_run_results` has one row per built node and test for that invocation, with `is_full_run = true`; a `--select` build adds rows with `is_full_run = false` and doesn't change what the UI reports (`run-operation`, e.g. `make grant-read`, runs no hooks and adds none).

---

## 9. Excluded dbt Features (and why)

| Feature | Reason |
|---|---|
| Python models | Not supported by the Postgres adapter |
| dbt Cloud (IDE, jobs, Explorer, hosted Semantic Layer APIs, Mesh cross-project refs) | Paid / hosted; out of scope for a local project |
| Mesh cross-project refs | Single project; groups/access cover governance within one project |
| Materialized views | No consumer needs pre-aggregation: Home's queries on the marts take milliseconds at this scale |
| Model versions | No mart needs a breaking change; versioning comes in when one does, with the consumers that need the old version |
| MetricFlow saved queries | Their only candidate consumer is Home, and Home tiles from saved queries is a UI later idea; they come with it |
| `--sample` | No workflow needs dev builds on a sample; a full dev build already takes seconds |
| Selectors and tags beyond `ci` | No scheduler or Make target would use them |

---

## 10. Developer Workflow (Makefile)

| Target | Does |
|---|---|
| `make up` / `make down` | Start/stop Docker Compose |
| `make generate DAYS=N` | Generator for N more days (`DAYS` required), capped at yesterday |
| `make backfill [DAYS=365]` | Generator for `DAYS` days (default 365); from data-platform-3, in 7-day chunks with `dbt snapshot --target prod` after each chunk |
| `make status` | Simulated range and row counts |
| `make reset` | Drop the dbt schemas (including the dbt test-failure audit schemas), then drop and recreate the raw schemas empty |
| `make deps` / `make seed` | `dbt deps` / `dbt seed` on `dev` |
| `make build` / `make build-prod` | `dbt source freshness` then `dbt build` on `dev` / on `prod` with `--target-path target-prod`. From ui-1, `make build-prod` then also runs `dbt docs generate` on prod, so `catalog.json` is current for the UI |
| `make docs` / `make docs-prod` | `dbt docs generate && dbt docs serve` on dev / prod (`--target-path target-prod`) |
| `make docs-check` | `dbt docs generate`, then check every model's YAML columns against the real columns |
| `make lint` | `ruff check`, `ruff format --check`, `mypy`, `sqlfluff lint dbt` |
| `make test` | `pytest` with `.env` loaded; integration tests need Postgres |

Targets added by later milestones:

| Target | Milestone | Does |
|---|---|---|
| `make eval-db` | data-platform-2 | Build the frozen eval database (§3) |
| `make advance DAYS=3` | data-platform-3 | Generator for N more days (capped at yesterday) + `dbt snapshot --target prod` |
| `make slim` / `make clone` | data-platform-4 | State-based dev build deferring to `target-prod` / `dbt clone` prod → dev |
| `make roles` | ui-1 | Re-run the idempotent roles script (`data-platform/postgres/roles.sql`) as the `postgres` superuser, so a role added later (e.g. `sherpa_ui`) reaches an existing volume without wiping it |

---

## 11. Interface to the Agent

The agent may rely on exactly these:

- **Schema** `marts` (prod target) with the models in §6.5. Intermediate, staging and snapshot models are not part of the interface.
- **Role** `sherpa_agent`: read-only, `SELECT` on `marts` only, `USAGE` on `extensions` (for `pg_trgm`), `statement_timeout = 5s`.
- **Artifacts** `data-platform/dbt/target-prod/manifest.json` and `catalog.json` (`manifest.json` is produced by `make build-prod`; `catalog.json` by `make docs-prod` until ui-1 adds it to `make build-prod`; dev runs never touch this directory), containing: model and column descriptions, `metric_<name>` doc blocks, `meta.domain`, `meta.grain`, `meta.primary_key`, `meta.pii`, `meta.synonyms`, `accepted_values` tests, contracts with column types, and (from data-platform-4) exposures.
- **Eval dataset:** database `sherpa_eval` with artifacts in `data-platform/dbt/target-eval/` (§3), built by `make eval-db`.
- **dbt target `agent`** (from agent-design-9) for read-only compilation with `sherpa_agent` credentials.
- **Postgres comments** on mart tables and columns (`persist_docs`), so the descriptions are available without the manifest.
- **`pg_trgm`** with GIN trigram indexes on product/brand/category name columns for fuzzy value search.
- **Determinism:** `as_of_date` semantics (§6.5) — time-relative columns never depend on `now()`.

**The UI** (UI spec) relies on the same interface, with its own read-only role `sherpa_ui` (§3). It also reads build history from `ops.dbt_run_results` (§8, "Added by ui-1") and freshness from `data-platform/dbt/target-prod/sources.json`, which only `dbt source freshness` writes.

Agent and UI work can start once data-platform-2 is done; data-platform-3–data-platform-5 add tables and metadata without breaking the data-platform-2 interface.

---

## 12. Decisions Made by the Spec Author

- Separate `raw_*` schemas per source system rather than one `raw` schema.
- Prices are net of tax; tax is a flat 20% added at checkout (var `tax_rate`).
- Refunds attributed to order date in `fct_orders`, refund date in `fct_refunds`.
- Soft-deleted customers kept and anonymized (not excluded), so facts keep integrity.
- `as_of_date` instead of `now()` for all relative-time columns.
- Last-touch session attribution; `acquisition_channel` from the first order's session.
- Contracts enforce PK/not-null in the database; foreign keys are tested, not enforced.
- CI does a full build on tiny data; slim/defer is local-only.
- Backfill runs in 7-day chunks with snapshots between chunks.
- Scale of ~8k orders / ~600k events per simulated year, consistent with a realistic ~3.1% session conversion. Volume, rate and distribution numbers are approximate targets, not exact goals.
- Snapshots live in one shared `snapshots` schema for all targets; prod artifacts in `data-platform/dbt/target-prod/`.
- Cancellation refunds don't reduce revenue (the cancelled order already contributes 0); goodwill refunds never include shipping.
- `new_customers` / CAC are campaign-level (via `acquisition_campaign_id`), so they sum correctly.
- Late events are held in `raw_meta.pending_events` until their `received_at` day; the generator assigns its own PKs.
- `stg_events__events` uses microbatch; merge and delete+insert each have their own model.
- Unit-tested intermediate models are views, not ephemeral.
- `dim_date` range is static via vars.
