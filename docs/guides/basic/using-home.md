# Using Home

Home is the first screen: five headline numbers for the last 30 days, a weekly revenue trend, a box that shows data problems when there are any and a quiet footer line for the healthy facts. It reads only `marts.dim_date`, `marts.fct_orders` and `marts.fct_refunds` and shows aggregates, never rows ([UI spec §6.2](../../superpowers/specs/2026-10-24-sql-sherpa-ui-design.md)). Start the stack as in the [README Quickstart](../../../README.md#quickstart), run `make build-prod`, and open <http://127.0.0.1:8090>.

## The tiles

The heading reads "Last 30 days", with "to <as of>, compared with the 30 days before" under it. **As of** is the latest day with orders in `marts.fct_orders` (never today's date). Each tile shows its last 30 days and its change against the 30 days before, with a one-word verdict beside the arrow ("better" or "worse"; none when flat, "no comparison" when there is none). Every tile has an info button with its definition. The definitions are the data platform spec's ([§6.6](../../superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md)):

| Tile | Change shown as |
|---|---|
| Net revenue, Orders, Average order value, New customers | relative, in % |
| Refund rate | difference, in percentage points (`pp`) |

What each counts (non-cancelled orders by `order_date`, new customers by first order) is in [data platform spec §6.6](../../superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md). Home's own definitions, the refund rate by `refund_date` and the previous period of Net revenue and AOV, are in [UI spec §6.2](../../superpowers/specs/2026-10-24-sql-sherpa-ui-design.md).

The windows are (as of − 30, as of] and (as of − 60, as of − 30]. The arrow and colour say whether the move is good (green) or bad (red): for the refund rate, up is bad. The verdict word says the same, and a screen reader hears the full sentence ("up 12.0% on the previous 30 days, an improvement"), so nothing relies on colour.
Net revenue and Average order value compare with the previous period **as it stood 30 days ago**. Refunds arrive up to about 7 weeks after an order, so the latest 30 days have few refunds yet and read high; the earlier window's final figure has all of its refunds, and a change against it would be inflated by about one refund rate. Their tooltips say so. The refund rate counts refunds paid in the 30 days over sales placed in them, so it differs from a by-order rate of recent orders, which reads low until their refunds arrive (its tooltip says so too). With a history of 60 to 106 days, the refund rate's earlier value reads low, because the refunds of orders placed before the history are missing.

A change that rounds to zero (under 0.05%, or under 0.05 points) is flat: no arrow and no colour.

A tile shows "—" for its change, and keeps its value, when:

- the history is shorter than 59 days (the first order is later than 59 days before as of), so the earlier window would be incomplete;
- the earlier value is 0 (no orders, no sales); or
- the value itself is empty (an average order value with no orders).

## The trend

Net revenue per week (Monday to Sunday) for the last 26 weeks, oldest first. The latest week is marked partial unless as of is its Sunday. Black Friday weeks are highlighted (a partial week shows as partial even in a Black Friday week). The chart has a title, "Weekly net revenue", and a caption: recent weeks read high until their refunds arrive (about 7 weeks). A small legend beside the caption names only the unusual kinds of week it draws. A shorter history shows fewer bars. A table of the same weeks sits beside the chart for screen readers.

## Data problems and the footer

Each item is in one place. A problem shows in a box under the heading (red "Data errors", or yellow "Data warnings" when none is an error; the first 3 items, then "Show N more"); with no problem there is no box. What is healthy is one dimmed line below the chart.

| Item | Source | Where |
|---|---|---|
| Last full build, with its node count | the newest full `dbt build` on prod in `ops.dbt_run_results` | footer |
| N failed nodes (only when above 0), N failing tests | the same build: models, seeds and snapshots whose own run failed, and failed tests. Each links to [Lineage](using-lineage.md) with "Failing only" on. A warning is not failing | box |
| "No failing tests" | the same build, at 0 | footer |
| One line per source in warning or error, or "Sources fresh" | `target-prod/sources.json`, written by `make build-prod` | box, or footer when fresh |

Each item that cannot be shown says why in the box as a warning, and the others stay: one broken dependency never blanks the screen.

## Symptoms

| Symptom | Fix |
|---|---|
| "No data yet" in place of the tiles | No marts, or marts with no orders: `make build-prod`; if `uv run sherpa-generate status` shows no simulation, run `make backfill` first (it refuses to run on an existing simulation) |
| "The figures are unavailable" with a Retry | The database is down, a query timed out or the server is `busy`: start Postgres (`docker compose up -d --wait postgres`) and press Retry |
| "build status unavailable" | The build record in `ops.dbt_run_results` could not be read (database down, missing grant): `dbt run-operation grant_read_access --target prod --target-path target-prod`, or start the database |
| "no full prod build recorded (run make build-prod)" | No full `dbt build` on prod is recorded yet; a build of one model does not count |
| "freshness unavailable" | `sources.json` is half-written (a build is running) or unreadable: wait for `make build-prod` and reload |
| "no freshness check recorded (run make build-prod)" | `sources.json` does not exist yet: `make build-prod` |
| "Sources ... (checked <time>)" is older than the build line | The freshness comes from this checkout's `target-prod/sources.json`, the build line from the shared warehouse, so after a blocked or old `make build-prod` the freshness can be older; the time shows when the check ran. Run `make build-prod` |
| `marketing.ad_spend_daily: warning, last loaded N h ago (checked <time>)` | Expected once the campaigns end: they stop at the catalog horizon ([data platform spec §5.2](../../superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md#52-daily-simulation-scale--10); the warn threshold is 48 h (§7)), so the table stops growing |
| A tile's change is "—" | One of the three cases above; a short history is the usual one |
| The numbers did not move after `make advance` | Run `make build-prod`, then reload: Home reads the marts, not the raw tables |

## Next

- [Extending the UI](../advanced/extending-the-ui.md#4-add-or-change-a-home-tile): add or change a tile.
- [Using Lineage](using-lineage.md): where the failing-tests link goes.
