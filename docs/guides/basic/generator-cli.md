# Using the generator

The generator (`sherpa-generate`) simulates an online sports shop day by day and writes what its four source systems would hold into Postgres: `raw_shop`, `raw_events`, `raw_marketing` and `raw_crm`, plus its bookkeeping in `raw_meta`. The data is deterministic: the same seed, start date and scale give the same rows.

Setup (`.env`, Postgres, uv) is in the [README Quickstart](../../../README.md#quickstart); this guide needs only the database (`docker compose up -d --wait postgres`). Run everything from the repository root. The database listens on `127.0.0.1:5432`; set `SHERPA_DB_PORT` in `.env` if the port is taken.

## First run: a year of data

```bash
make backfill
uv run sherpa-generate status
```

`make backfill` starts a new simulation of `DAYS` days (default 365) ending yesterday (UTC), in weekly chunks with a `dbt snapshot` after each one, so the snapshot sees every catalog price change ([price history](building-the-warehouse.md#keeping-it-current)). It takes about 9 minutes for a year of data (measured on a Windows laptop; the 52 snapshots add about a second each). It refuses to run on a database that already holds a simulation (`make reset` first; `make advance` continues one). The first run creates the raw schemas and the catalog (categories, products, variants, campaigns, promotions, coupons); then every day is simulated in its own transaction. Logs go to stderr, results to stdout. A year at the default scale gives about 5,000 customers, 8,100 orders, 670,000 events and 5,800 leads. `status` prints the range, seed and scale, then one row count per raw table:

```
simulated 2025-10-23 .. 2026-10-22 (seed 42, scale 1.0)
raw_events.events: 670748
raw_meta.pending_events: 96
raw_shop.orders: 8090
…
```

`raw_meta.pending_events` holds late events (rule R2) whose `received_at` is after the last simulated day; they land in `raw_events.events` when that day is simulated.

## Commands and options

| Command | What it does |
|---|---|
| `generate --days N` | Simulate `N` more days. The first run sets up the catalog; later runs continue after the last simulated day. Never goes beyond yesterday (UTC). |
| `start-date` | Print the first simulated day (nothing on an empty database). `make build` reads it as the `begin` of the event model. |
| `status` | Print the simulated range, seed, scale and the row count of every raw table. |
| `reset` | Drop and recreate the raw schemas: all generated data and the simulation state are gone. |

| `generate` option | Default | Meaning |
|---|---|---|
| `--days N` | required | Days to simulate, at least 1. |
| `--start-date YYYY-MM-DD` | `N` days before today | First simulated day. First run only. |
| `--scale S` | `1.0` | Multiplier for every daily volume. The catalog does not scale. |
| `--seed K` | `42` | Seed for every random draw. |

The seed and scale are stored on the first run; every later run must pass the same values. `make advance DAYS=3` (more days, then a snapshot) and `make backfill` always use the default seed and scale; call `uv run sherpa-generate` directly for anything else. `make reset` drops the dbt schemas first (their views read the raw schemas), then runs `sherpa-generate reset`.

## Recipes

**A small, fast dataset** (about 6 seconds, ~65 orders, enough to run every dbt model and test). Later runs must keep `--scale 0.1`.

```bash
make reset
uv run sherpa-generate generate --days 30 --scale 0.1
```

**A reproducible dataset.** Fix the seed and the start date, then compare two runs:

```bash
make reset
uv run sherpa-generate generate --days 30 --start-date 2026-09-01 --scale 0.1 --seed 7
uv run sherpa-generate status > run1.txt
make reset
uv run sherpa-generate generate --days 30 --start-date 2026-09-01 --scale 0.1 --seed 7
uv run sherpa-generate status > run2.txt
diff run1.txt run2.txt && echo identical
```

Campaigns and promotions cover at most the first 365 days from the start date, or end at yesterday of the first run if that is sooner. Runs therefore match on any later day only when the start date is at least a year back (for example `--start-date 2025-09-01`); with a recent start date they match only if both use the same yesterday.

**Topping up to yesterday.** Rerun `generate` with the same `--scale` and `--seed` and enough days; it stops at yesterday on its own (`make advance DAYS=7` does the same with defaults):

```bash
uv run sherpa-generate generate --days 30 --scale 0.1 --seed 7
```

Topping up keeps the freshness checks on orders, events and leads green (see [Building the warehouse](building-the-warehouse.md)). Top-up days have no paid campaigns, promotions or ad spend, so once the newest `ad_spend_daily` row is over 48 hours old its freshness check warns. For a full catalog again, start over.

**Starting over.** Then rebuild the dbt layers with `make build`:

```bash
make reset
make backfill
```

## Errors

Every refused run exits with code 2 and writes nothing.

| Symptom | Fix |
|---|---|
| `error: stored state has seed 42, scale 0.1; got seed 42, scale 1.0 (reset to start over)` | Pass the stored values (`uv run sherpa-generate status` shows them) or `make reset`. `make advance` always uses the defaults. |
| `error: a start date can only be given on the first run; the simulation started on …` | Drop `--start-date`, or `make reset` first. |
| `error: a simulation already runs from 2026-10-06; make backfill starts a new one …` | `make reset` first, or `make advance DAYS=<n>` to continue it. |
| `error: start date 2026-10-23 is after yesterday (2026-10-22)` | Nothing may be timestamped after yesterday (UTC); use yesterday or earlier. |
| `nothing to simulate: already caught up to yesterday` (exit code 0) | Not an error; run it again tomorrow. |
| `error: cannot connect to the database (is it running? see make up)…` | Start Postgres with `docker compose up -d --wait postgres` and check `SHERPA_DB_PORT` in `.env`. The attempt gives up after 10 seconds. |
| `DAYS is required, e.g. make advance DAYS=3.` | Pass `DAYS=n`. `--days` must be at least 1. |

## Seeing the data and the realism rules

```bash
docker compose exec postgres psql -U sherpa_admin -d sherpa
```

`\dn` lists the schemas, `\dt raw_shop.*` the tables of one. Orders by status and channel:

```sql
SELECT status, sales_channel, count(*) AS orders, sum(order_total) AS order_total
FROM raw_shop.orders
GROUP BY status, sales_channel
ORDER BY status, sales_channel;
```

The generator injects a fixed list of data problems, R1 to R12, which dbt's staging models clean up; see the [data platform spec §4.4](../../superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md) for the full list. Two to find in the raw data:

```sql
-- R1, duplicate events: the same event_id received seconds apart
SELECT event_id, event_type, occurred_at, received_at
FROM raw_events.events
WHERE event_id = (
    SELECT event_id FROM raw_events.events GROUP BY event_id HAVING count(*) > 1 ORDER BY event_id LIMIT 1
);

-- R3, noisy emails: casing or whitespace that lower(trim(email)) removes
SELECT id, email, lower(trim(email)) AS normalized
FROM raw_shop.customers
WHERE email <> lower(trim(email))
ORDER BY id
LIMIT 5;
```

```
               event_id               |  event_type  |        occurred_at        |          received_at
--------------------------------------+--------------+---------------------------+-------------------------------
 00022b1c-8330-4894-87a0-1bacbb0bf235 | product_view | 2026-01-20T15:48:49+01:00 | 2026-01-20 14:48:52.344334+00
 00022b1c-8330-4894-87a0-1bacbb0bf235 | product_view | 2026-01-20T15:48:49+01:00 | 2026-01-20 14:49:20.344334+00
```

`occurred_at` is text with the client's UTC offset; `received_at` is a UTC `timestamptz`. Quit psql with `\q`.

## Next

- [Building the warehouse](building-the-warehouse.md): run dbt on this data.
- [Extending the source data model](../advanced/extending-the-source-data-model.md): add columns, tables, enum values or realism rules.
