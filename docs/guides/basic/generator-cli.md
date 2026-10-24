# Using the generator

The generator (`sherpa-generate`) simulates an online sports shop day by day and writes what its four source systems would hold into Postgres: the shop database (`raw_shop`), the event tracker (`raw_events`), the ads platform (`raw_marketing`) and the CRM (`raw_crm`). Its bookkeeping lives in `raw_meta`. The data is deterministic: the same seed, start date and scale give the same rows.

All commands run from the repository root. Outputs below are real; row counts depend on the dates you simulate, and timings on your machine.

## Prerequisites and setup

You need Docker, [uv](https://docs.astral.sh/uv/) and GNU Make (on Windows, install `make` with Scoop, Chocolatey or WSL).

```bash
cp .env.example .env     # then replace every change-me password
docker compose up -d --wait postgres   # start Postgres and wait until it is healthy
```

```
$ docker compose ps postgres
NAME                    IMAGE                COMMAND                  SERVICE    CREATED       STATUS                 PORTS
sql-sherpa-postgres-1   postgres:16-alpine   "docker-entrypoint.s…"   postgres   3 hours ago   Up 3 hours (healthy)   127.0.0.1:5432->5432/tcp
```

`make up` starts every service (Postgres and the MCP server, see [Using the MCP server](using-the-mcp-server.md)); this guide needs only the database. The compose project is pinned to `sql-sherpa`, so the container has the same name from every checkout. There is no separate install step: the first `uv run` creates the Python 3.12 environment with every dependency group.

Postgres listens on `127.0.0.1:5432` (the port is published on the loopback address only, and `.env` sets `SHERPA_DB_HOST=127.0.0.1` because `localhost` can resolve to IPv6 first on Windows and hang). If that port is taken, set `SHERPA_DB_PORT` in `.env`; Docker Compose, the generator and dbt all read it. On the first start the container runs `data-platform/postgres/init/00_roles.sh`, which creates the `sherpa_admin` and `sherpa_agent` roles with the passwords from `.env`.

## First run: a year of data

```bash
make backfill DAYS=365
```

This simulates the 365 days ending yesterday (UTC). It took 3.5 minutes on a laptop; the log prints one line per day on stderr and a summary at the end:

```
uv run sherpa-generate generate --days 365
2026-10-23 14:44:27,155 INFO sherpa_generator.simulation.simulator: set up the catalog from 2025-10-23: 1878 rows
2026-10-23 14:44:28,509 INFO sherpa_generator.simulation.simulator: simulated 2025-10-23: 1593 rows
...
2026-10-23 14:47:57,864 INFO sherpa_generator.simulation.simulator: simulated 2026-10-22: 2214 rows
simulated 2025-10-23 .. 2026-10-22: 948182 rows written
```

The first run creates the five raw schemas, builds the catalog (categories, products, variants, campaigns, promotions and coupons) and then simulates every day in its own transaction. A year at the default scale gives about 5,000 customers (43 % of whom never order), 8,100 orders, 670,000 events and 5,800 leads. See them with `make status`:

```
uv run sherpa-generate status
simulated 2025-10-23 .. 2026-10-22 (seed 42, scale 1.0)
raw_crm.email_sends: 164646
raw_crm.leads: 5815
raw_events.events: 670748
raw_marketing.ad_spend_daily: 1307
raw_marketing.campaigns: 15
raw_meta.pending_events: 96
raw_meta.simulation_state: 1
raw_shop.addresses: 9808
raw_shop.categories: 41
raw_shop.coupon_codes: 16
raw_shop.customers: 4951
raw_shop.order_discounts: 1930
raw_shop.order_items: 14269
raw_shop.order_status_history: 31670
raw_shop.orders: 8090
raw_shop.payments: 8683
raw_shop.product_variants: 1488
raw_shop.products: 300
raw_shop.promotions: 18
raw_shop.refunds: 1195
raw_shop.return_items: 1342
raw_shop.returns: 971
raw_shop.shipments: 7654
```

`raw_meta.pending_events` holds late events (realism rule R2; the realism rules R1 to R12 are listed in the data platform spec §4.4) whose `received_at` is after the last simulated day; they land in `raw_events.events` when that day is simulated.

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
2026-10-23 14:55:12,150 INFO sherpa_generator.simulation.simulator: set up the catalog from 2026-09-23: 1790 rows
...
simulated 2026-09-23 .. 2026-10-22: 7917 rows written
```

About 6 seconds, ~30 customers, ~65 orders and ~5,100 events. That is enough to run every dbt model and test. Later runs must keep `--scale 0.1`.

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
simulated 2026-09-01 .. 2026-09-30: 7874 rows written
...
identical
```

The setup creates campaigns and promotions for at most the first 365 days from the start date, or up to yesterday of the first run if that is sooner. The same seed, start date, scale and number of days therefore give identical data on any later calendar day when the range is in the past and the start date is at least a year back (for example `--start-date 2025-09-01`). With a more recent start date, as in this recipe, the two runs above match because they use the same yesterday; a run on another day gets a longer catalog.

### Topping up to yesterday after a few days off

Run `generate` again with the same `--scale` and `--seed` and enough days to reach yesterday; it stops there on its own:

```bash
uv run sherpa-generate generate --days 30 --scale 0.1 --seed 7
```

```
2026-10-23 15:03:37,416 INFO sherpa_generator.simulation.simulator: simulated 2026-10-01: 231 rows
...
simulated 2026-10-01 .. 2026-10-22: 4630 rows written
```

With the default seed and scale, `make generate DAYS=7` does the same. Topping up keeps the freshness checks on orders, events and leads green (see [Building the warehouse](building-the-warehouse.md)). Campaigns and promotions cover the first 365 days from the start date, or end on the yesterday of the first run if that is sooner, so top-ups after the first run add days without paid campaigns, promotions or ad spend. Once the newest `ad_spend_daily` row is more than 48 hours old, its freshness check warns. To get a full catalog again, start over.

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
make: *** [Makefile:17: generate] Error 2
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
2026-10-23 15:04:03,351 INFO sherpa_generator.simulation.simulator: nothing to simulate: already at 2026-10-22
nothing to simulate: already caught up to yesterday
```

**Postgres not running.** Start it with `docker compose up -d --wait postgres` (and check `SHERPA_DB_PORT` in `.env`). The connection attempt gives up after 10 seconds:

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
 cancelled | app           |    142 |    14722.44
 cancelled | web           |    258 |    29797.96
 delivered | app           |   2649 |   288414.68
 delivered | web           |   4931 |   524794.06
 paid      | app           |     13 |     1528.68
 paid      | web           |     23 |     2369.64
 shipped   | app           |     25 |     2462.87
 shipped   | web           |     49 |     4782.28
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
 2025-10-01 |    172
 2025-11-01 |    962
 2025-12-01 |   1020
 2026-01-01 |    463
 2026-02-01 |    605
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
 product_view   | 404041
 page_view      | 164875
 login          |  40728
 add_to_cart    |  35008
 begin_checkout |  15799
 purchase       |   5299
 signup         |   4998
```

`context` and `properties` are `jsonb`; `->>` extracts a key as text. UTM tags (`utm_source`, `utm_medium`, `utm_campaign`) are URL parameters that record where a visit came from:

```sql
SELECT event_type, context ->> 'utm_source' AS utm_source, properties ->> 'order_id' AS order_id
FROM raw_events.events
WHERE event_type = 'purchase' AND context ->> 'utm_source' IS NOT NULL
ORDER BY received_at
LIMIT 3;
```

```
 event_type | utm_source | order_id
------------+------------+----------
 purchase   | google     | 4
 purchase   | google     | 12
 purchase   | newsletter | 21
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
   channel   |   spend   | clicks
-------------+-----------+--------
 paid_social | 104330.15 |  19643
 paid_search |  76951.17 |  29149
 affiliate   |   9647.59 |   8072
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
 lead            |  1912
 mql             |  1862
 customer        |  1709
 subscriber      |   332

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
 00022b1c-8330-4894-87a0-1bacbb0bf235 | product_view | 2026-01-20T15:48:49+01:00 | 2026-01-20 14:48:52.344334+00
 00022b1c-8330-4894-87a0-1bacbb0bf235 | product_view | 2026-01-20T15:48:49+01:00 | 2026-01-20 14:49:20.344334+00
```

`occurred_at` is text with the client's UTC offset; `received_at` is a UTC `timestamptz`. 670,748 rows hold 664,326 distinct events.

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
 c6855276-8fb7-4a88-bde3-8af18fa6b218 | 2025-10-23T12:03:56+02:00 | 2025-10-24 10:32:04.526742+00
 c9b74b24-d807-4f4c-9eb5-66c6cb92aee0 | 2025-10-23T14:44:47+02:00 | 2025-10-24 14:20:02.774598+00
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
 id |             email             |          normalized
----+-------------------------------+------------------------------
  5 | CCAFARCHIA5@TIM.IT            | ccafarchia5@tim.it
  7 |  SPEERJUERGEN7@WEB.DE         | speerjuergen7@web.de
 16 | tbuisson16@orange.fr          | tbuisson16@orange.fr
 56 |  brunildacorral4@yahoo.com    | brunildacorral4@yahoo.com
 70 | cassandragottardi50@poste.it  | cassandragottardi50@poste.it
```

Customers 7 and 56 have a leading space and customers 16 and 70 a trailing one.

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
 DE           |      1445
 FR           |       952
...
 de           |        55
...
 at           |         8
 uk           |         5
 UK           |         4
 be           |         3
 gb           |         2
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
 id  |                   email                   | is_test_account | orders
-----+-------------------------------------------+-----------------+--------
  58 | maurits2458@sqlsherpa-test.com            | t               |      4
  75 | gertrudisquintanilla75@sqlsherpa-test.com | t               |      2
 432 | postashley432@sqlsherpa-test.com          | t               |      3
 634 | manriquefulgencio634@sqlsherpa-test.com   | t               |      3
 704 | ggemito704@sqlsherpa-test.com             | t               |     14
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
        8 | gift_card |  21.80 | captured | 2025-10-24 10:51:07+00
        8 | card      |  72.99 | captured | 2025-10-24 10:51:07+00

 order_id | method | amount |  status  |       created_at
----------+--------+--------+----------+------------------------
       28 | card   | 143.96 | failed   | 2025-10-24 23:42:54+00
       28 | card   | 143.96 | captured | 2025-10-24 23:47:46+00
```

The failed card attempt of order 28 was retried a few minutes later and captured.

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
      645 |           11 |                |   5.00
      645 |            2 |              3 |  23.59
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
