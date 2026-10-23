# Using the generator

The generator (`sherpa-generate`) simulates an online sports shop day by day and writes what its four source systems would hold into Postgres: the shop database (`raw_shop`), the event tracker (`raw_events`), the ads platform (`raw_marketing`) and the CRM (`raw_crm`). Its bookkeeping lives in `raw_meta`. The data is deterministic: the same seed, start date and scale give the same rows.

All commands run from the repository root. Outputs below are real; row counts depend on the dates you simulate, and timings on your machine.

## Prerequisites and setup

You need Docker, [uv](https://docs.astral.sh/uv/) and GNU Make (on Windows, install `make` with Scoop, Chocolatey or WSL).

```bash
cp .env.example .env     # then replace every change-me password
make up                  # start Postgres and wait until it is healthy
```

```
docker compose up -d --wait
 Container <folder>-postgres-1 Starting
 Container <folder>-postgres-1 Started
 Container <folder>-postgres-1 Waiting
 Container <folder>-postgres-1 Healthy
```

Docker Compose names the container after the folder you cloned into. There is no separate install step: the first `uv run` creates the Python 3.12 environment with every dependency group.

Postgres listens on `127.0.0.1:5432` (the port is published on the loopback address only, and `.env` sets `SHERPA_DB_HOST=127.0.0.1` because `localhost` can resolve to IPv6 first on Windows and hang). If that port is taken, set `SHERPA_DB_PORT` in `.env`; Docker Compose, the generator and dbt all read it. On the first start the container runs `docker/postgres/init/00_roles.sh`, which creates the `sherpa_admin` and `sherpa_agent` roles with the passwords from `.env`.

## First run: a year of data

```bash
make backfill DAYS=365
```

This simulates the 365 days ending yesterday (UTC). It took 4.5 minutes on a laptop; the log prints one line per day on stderr and a summary at the end:

```
uv run sherpa-generate generate --days 365
2026-10-23 09:59:43,482 INFO sherpa_generator.simulation.simulator: set up the catalog from 2025-10-23: 1848 rows
2026-10-23 09:59:54,756 INFO sherpa_generator.simulation.simulator: simulated 2025-10-23: 1657 rows
...
2026-10-23 10:04:10,256 INFO sherpa_generator.simulation.simulator: simulated 2026-10-22: 1922 rows
simulated 2025-10-23 .. 2026-10-22: 923036 rows written
```

The first run creates the five raw schemas, builds the catalog (categories, products, variants, campaigns, promotions and coupons) and then simulates every day in its own transaction. A year at the default scale gives about 5,000 customers, 8,000 orders, 650,000 events and 5,800 leads. See them with `make status`:

```
uv run sherpa-generate status
simulated 2025-10-23 .. 2026-10-22 (seed 42, scale 1.0)
raw_crm.email_sends: 164646
raw_crm.leads: 5815
raw_events.events: 646446
raw_marketing.ad_spend_daily: 1307
raw_marketing.campaigns: 15
raw_meta.pending_events: 70
raw_meta.simulation_state: 1
raw_shop.addresses: 9934
raw_shop.categories: 41
raw_shop.coupon_codes: 17
raw_shop.customers: 4985
raw_shop.order_discounts: 1969
raw_shop.order_items: 14046
raw_shop.order_status_history: 31460
raw_shop.orders: 8039
raw_shop.payments: 8642
raw_shop.product_variants: 1457
raw_shop.products: 300
raw_shop.promotions: 18
raw_shop.refunds: 1165
raw_shop.return_items: 1373
raw_shop.returns: 969
raw_shop.shipments: 7613
```

`raw_meta.pending_events` holds late events (realism rule R2) whose `received_at` is after the last simulated day; they land in `raw_events.events` when that day is simulated.

## Commands and options

```bash
uv run sherpa-generate --help
```

| Command | What it does |
|---|---|
| `generate --days N` | Simulate `N` more days. The first run sets up the catalog and ends yesterday; later runs continue after the last simulated day. Never goes beyond yesterday (UTC). |
| `status` | Print the simulated range, seed, scale and the row count of every raw table. |
| `reset` | Drop and recreate the raw schemas: all generated data and the simulation state are gone. |

`generate` options:

| Option | Default | Meaning |
|---|---|---|
| `--days N` | required | Days to simulate, at least 1. |
| `--start-date YYYY-MM-DD` | `N` days before today | First simulated day. Only allowed on the first run. |
| `--scale S` | `1.0` | Multiplier for every daily volume (sessions, signups, leads, ...). The catalog does not scale. |
| `--seed K` | `42` | Seed for every random draw. |

The seed and scale are stored on the first run and every later run must pass the same values. Logs go to stderr and the results to stdout, so `uv run sherpa-generate status > counts.txt` captures only the counts.

### Make shortcuts

| Shortcut | Runs |
|---|---|
| `make generate DAYS=3` | `sherpa-generate generate --days 3` (`DAYS` is required) |
| `make backfill` | `sherpa-generate generate --days 365` (`DAYS=...` overrides) |
| `make status` | `sherpa-generate status` |
| `make reset` | drops the dbt schemas (`dev_seeds`, `dev_staging`, `dev_marts`, `dev_dbt_test__audit`, `seeds`, `staging`, `marts`, `dbt_test__audit`), then `sherpa-generate reset` |

The shortcuts always use the default seed and scale. For anything else call `uv run sherpa-generate` directly.

## Common recipes

### A small, fast dataset for experiments

```bash
make reset
uv run sherpa-generate generate --days 30 --scale 0.1
```

```
2026-10-23 10:11:13,113 INFO sherpa_generator.simulation.simulator: set up the catalog from 2026-09-23: 1796 rows
...
simulated 2026-09-23 .. 2026-10-22: 7844 rows written
```

About 6 seconds, ~40 customers, ~65 orders and ~5,000 events. That is enough to run every dbt model and test. Later runs must keep `--scale 0.1`.

### A reproducible dataset

Fix the seed and the start date:

```bash
make reset
uv run sherpa-generate generate --days 30 --start-date 2026-09-01 --scale 0.1 --seed 7
uv run sherpa-generate status > run1.txt
make reset
uv run sherpa-generate generate --days 30 --start-date 2026-09-01 --scale 0.1 --seed 7
uv run sherpa-generate status > run2.txt
diff run1.txt run2.txt && echo identical
```

```
simulated 2026-09-01 .. 2026-09-30: 7648 rows written
...
identical
```

The setup creates campaigns and promotions for at most one year from the start date (or up to yesterday if sooner), so the same seed, start date, scale and number of days give identical data on any later calendar day, as long as the range is already in the past.

### Topping up to yesterday after a few days off

Run `generate` again with the same `--scale` and `--seed` and enough days to reach yesterday; it stops there on its own:

```bash
uv run sherpa-generate generate --days 30 --scale 0.1 --seed 7
```

```
2026-10-23 10:17:30,801 INFO sherpa_generator.simulation.simulator: simulated 2026-10-01: 238 rows
...
simulated 2026-10-01 .. 2026-10-22: 4611 rows written
```

With the default seed and scale, `make generate DAYS=7` does the same. Topping up keeps `dbt source freshness` green (see [Building the warehouse](building-the-warehouse.md)). Campaigns and promotions end one year after the start date, or on the yesterday of the first run if that is sooner, so days added after that have no campaigns, promotions or ad spend, and the freshness check on `ad_spend_daily` warns after 48 hours. To get a full catalog again, start over.

### Starting over

```bash
make reset
make backfill DAYS=365
```

`make reset` prints the dbt objects it drops (a long `NOTICE: drop cascades to ...` list when dbt has run) and ends with:

```
DROP SCHEMA
uv run sherpa-generate reset
raw schemas reset
```

Rebuild the dbt layers afterwards with `make build`.

## What the errors mean

Every refused run exits with code 2 and writes nothing.

**Seed or scale mismatch.** The stored simulation was made with other values. Pass the stored ones (`make status` shows them) or start over.

```
$ make generate DAYS=1
uv run sherpa-generate generate --days 1
error: stored state has seed 42, scale 0.1; got seed 42, scale 1.0 (reset to start over)
```

**Start date on a later run.** `--start-date` only applies to the first run. Drop it, or `make reset` first.

```
$ uv run sherpa-generate generate --days 5 --scale 0.1 --start-date 2026-01-01
error: a start date can only be given on the first run; the simulation started on 2026-09-23
```

**Start date after yesterday.** Nothing may be timestamped after yesterday (UTC), so the first day must be yesterday or earlier.

```
$ uv run sherpa-generate generate --days 1 --start-date 2026-10-23
error: start date 2026-10-23 is after yesterday (2026-10-22)
```

**Nothing to simulate.** Not an error (exit code 0): the simulation already reaches yesterday. Run it again tomorrow.

```
$ uv run sherpa-generate generate --days 1 --scale 0.1
2026-10-23 10:11:30,629 INFO sherpa_generator.simulation.simulator: nothing to simulate: already at 2026-10-22
nothing to simulate: already caught up to yesterday
```

**Postgres not running.** Start it with `make up` (and check `SHERPA_DB_PORT` in `.env`). The connection attempt gives up after 10 seconds:

```
$ uv run sherpa-generate status
error: cannot connect to the database (is it running? see `make up`): connection timeout expired
```

`generate` and `reset` report the same error and exit with code 2.

**Missing or invalid `--days`.** `make generate` needs `DAYS`, and `--days` must be at least 1:

```
$ make generate
Makefile:16: *** DAYS is required, e.g. make generate DAYS=3.  Stop.
$ uv run sherpa-generate generate --days 0
...
| Invalid value for '--days': 0 is not in the range x>=1.                     |
```

## Inspecting the data with psql

Open a shell in the database container:

```bash
docker compose exec postgres psql -U sherpa_admin -d sherpa
```

`\dn` lists the schemas and `\dt raw_shop.*` the tables of one schema. The results below come from the 365-day run above.

### Shop (`raw_shop`)

Orders by status and channel:

```sql
SELECT status, sales_channel, count(*) AS orders, sum(order_total) AS order_total
FROM raw_shop.orders
GROUP BY status, sales_channel
ORDER BY status, sales_channel;
```

```
  status   | sales_channel | orders | order_total
-----------+---------------+--------+-------------
 cancelled | app           |    143 |    32042.72
 cancelled | web           |    254 |    60184.45
 delivered | app           |   2624 |   620244.12
 delivered | web           |   4899 |  1138999.59
 paid      | app           |     10 |     2488.71
 paid      | web           |     19 |     3740.63
 shipped   | app           |     29 |     6446.25
 shipped   | web           |     61 |    12379.07
```

Orders per month show the seasonality (Black Friday week and December up, January down):

```sql
SELECT date_trunc('month', placed_at)::date AS month, count(*) AS orders
FROM raw_shop.orders
GROUP BY 1
ORDER BY 1;
```

```
   month    | orders
------------+--------
 2025-10-01 |    179
 2025-11-01 |    926
 2025-12-01 |    975
 2026-01-01 |    474
 2026-02-01 |    595
...
```

The first month is partial because the run started on 23 October.

### Events (`raw_events`)

Events per type:

```sql
SELECT event_type, count(*) AS events
FROM raw_events.events
GROUP BY event_type
ORDER BY events DESC;
```

```
   event_type   | events
----------------+--------
 product_view   | 403688
 page_view      | 164967
 add_to_cart    |  34950
 login          |  16866
 begin_checkout |  15661
 purchase       |   5281
 signup         |   5033
```

`context` and `properties` are `jsonb`; `->>` extracts a key as text:

```sql
SELECT event_type, context ->> 'utm_source' AS utm_source, properties ->> 'order_id' AS order_id
FROM raw_events.events
WHERE event_type = 'purchase'
ORDER BY received_at
LIMIT 3;
```

```
 event_type | utm_source | order_id
------------+------------+----------
 purchase   | instagram  | 1
 purchase   |            | 2
 purchase   |            | 3
```

### Marketing (`raw_marketing`)

Spend and ad-platform clicks per channel:

```sql
SELECT c.channel, sum(a.spend) AS spend, sum(a.clicks) AS clicks
FROM raw_marketing.ad_spend_daily a
JOIN raw_marketing.campaigns c ON c.id = a.campaign_id
GROUP BY c.channel
ORDER BY spend DESC;
```

```
   channel   |  spend   | clicks
-------------+----------+--------
 paid_search | 99518.79 |  29462
 paid_social | 92556.61 |  19421
 affiliate   |  8354.66 |   8087
```

### CRM (`raw_crm`)

Leads per lifecycle stage, and the email engagement rates:

```sql
SELECT lifecycle_stage, count(*) AS leads
FROM raw_crm.leads
GROUP BY lifecycle_stage
ORDER BY leads DESC;

SELECT count(*) AS sends,
       round(avg((opened_at IS NOT NULL)::int), 3) AS open_rate,
       round(avg((clicked_at IS NOT NULL)::int), 3) AS click_rate
FROM raw_crm.email_sends;
```

```
 lifecycle_stage | leads
-----------------+-------
 lead            |  1874
 mql             |  1824
 customer        |  1781
 subscriber      |   336

 sends  | open_rate | click_rate
--------+-----------+------------
 164646 |     0.250 |      0.030
```

### The realism rules

The generator injects a fixed list of data problems, R1 to R12 (data platform spec §4.4). dbt's staging models clean them up. A few of them, found in the raw data:

**R1, a duplicate event:** the same `event_id` twice, received seconds apart on the same day.

```sql
SELECT event_id, event_type, occurred_at, received_at
FROM raw_events.events
WHERE event_id = (
    SELECT event_id FROM raw_events.events GROUP BY event_id HAVING count(*) > 1 ORDER BY event_id LIMIT 1
);
```

```
               event_id               |  event_type  |        occurred_at        |          received_at
--------------------------------------+--------------+---------------------------+-------------------------------
 00006e20-46d4-49ea-964d-ca7a21efae8e | product_view | 2026-08-08T15:22:27+02:00 | 2026-08-08 13:23:26.401044+00
 00006e20-46d4-49ea-964d-ca7a21efae8e | product_view | 2026-08-08T15:22:27+02:00 | 2026-08-08 13:22:30.401044+00
```

`occurred_at` is text with the client's UTC offset; `received_at` is a UTC `timestamptz`. 646,446 rows hold 640,252 distinct events.

**R2, late events:** received more than a day after they occurred.

```sql
SELECT event_id, occurred_at, received_at
FROM raw_events.events
WHERE received_at - occurred_at::timestamptz > interval '1 day'
ORDER BY received_at
LIMIT 3;
```

```
               event_id               |        occurred_at        |          received_at
--------------------------------------+---------------------------+-------------------------------
 48e73f56-b213-4ee7-b5ff-19b35cf7c6da | 2025-10-23T08:22:15+02:00 | 2025-10-24 08:32:06.187802+00
 c6855276-8fb7-4a88-bde3-8af18fa6b218 | 2025-10-23T12:03:56+02:00 | 2025-10-24 10:32:04.526742+00
 abeb766e-bd76-4615-9588-4ee9e61b318d | 2025-10-23T09:22:05+02:00 | 2025-10-24 17:21:03.760813+00
```

**R3, a noisy email:** casing or surrounding whitespace that `lower(trim(email))` removes.

```sql
SELECT id, email, lower(trim(email)) AS normalized
FROM raw_shop.customers
WHERE email <> lower(trim(email))
ORDER BY id
LIMIT 5;
```

```
 id |            email             |         normalized
----+------------------------------+-----------------------------
  7 | DANTERESPIGHI7@POSTE.IT      | danterespighi7@poste.it
 18 |  damian6918@hotmail.com      | damian6918@hotmail.com
 25 | Zgonzalez25@gmail.com        | zgonzalez25@gmail.com
 35 | virgilioalmagro35@yahoo.com  | virgilioalmagro35@yahoo.com
 49 | Nicolasmarcia27@hotmail.com  | nicolasmarcia27@hotmail.com
```

Customer 18 has a leading space and customer 35 a trailing one.

**R4, country code noise:** lowercase codes and `UK` for `GB`.

```sql
SELECT country_code, count(*) AS customers
FROM raw_shop.customers
GROUP BY country_code
ORDER BY customers DESC;
```

```
 country_code | customers
--------------+-----------
 DE           |      1478
 FR           |       969
...
 de           |        49
...
 uk           |         5
 at           |         5
 UK           |         5
```

**R5, test accounts:** internal `@sqlsherpa-test.com` customers that place real orders.

```sql
SELECT c.id, c.email, c.is_test_account, count(o.id) AS orders
FROM raw_shop.customers c
LEFT JOIN raw_shop.orders o ON o.customer_id = c.id
WHERE c.email ILIKE '%@sqlsherpa-test.com%'
GROUP BY c.id, c.email, c.is_test_account
ORDER BY c.id
LIMIT 5;
```

```
 id  |              email              | is_test_account | orders
-----+---------------------------------+-----------------+--------
  58 | maurits2458@sqlsherpa-test.com  | t               |      3
 197 | pjover197@sqlsherpa-test.com    | t               |      2
 362 | evamercks362@sqlsherpa-test.com | t               |      1
 462 | james28462@sqlsherpa-test.com   | t               |      3
 479 | juliana06479@sqlsherpa-test.com | t               |      1
```

**R7, a split payment and a failed attempt:** an order paid with a gift card plus a card, and one whose first attempt failed before it was captured.

```sql
SELECT order_id, method, amount, status, created_at
FROM raw_shop.payments
WHERE order_id = (
    SELECT order_id FROM raw_shop.payments
    WHERE status = 'captured'
    GROUP BY order_id HAVING count(*) > 1
    ORDER BY order_id LIMIT 1
)
ORDER BY id;

SELECT order_id, method, amount, status, created_at
FROM raw_shop.payments
WHERE order_id = (SELECT min(order_id) FROM raw_shop.payments WHERE status = 'failed')
ORDER BY id;
```

```
 order_id |  method   | amount |  status  |       created_at
----------+-----------+--------+----------+------------------------
       22 | gift_card |  84.65 | captured | 2025-10-24 20:07:03+00
       22 | card      | 116.90 | captured | 2025-10-24 20:07:03+00

 order_id | method | amount |  status  |       created_at
----------+--------+--------+----------+------------------------
       87 | paypal | 431.70 | failed   | 2025-10-26 22:36:00+00
       87 | paypal | 431.70 | captured | 2025-10-26 23:05:33+00
```

Revenue sums only `captured` payments, never every row.

**R8, two discounts on one order:** a promotion plus a coupon of another promotion.

```sql
SELECT order_id, promotion_id, coupon_code_id, amount
FROM raw_shop.order_discounts
WHERE order_id = (
    SELECT order_id FROM raw_shop.order_discounts GROUP BY order_id HAVING count(*) > 1 ORDER BY order_id LIMIT 1
);
```

```
 order_id | promotion_id | coupon_code_id | amount
----------+--------------+----------------+--------
      702 |           11 |                |   5.00
      702 |            2 |              4 |  51.18
```

The simulation state itself is one row:

```sql
SELECT seed, scale, start_date, last_simulated_date FROM raw_meta.simulation_state;
```

```
 seed | scale | start_date | last_simulated_date
------+-------+------------+---------------------
   42 |     1 | 2025-10-23 | 2026-10-22
```

Quit psql with `\q`.

## Next

- [Building the warehouse](building-the-warehouse.md): run dbt on this data.
- [Extending the source data model](../advanced/extending-the-source-data-model.md): add columns, tables, enum values or realism rules.
