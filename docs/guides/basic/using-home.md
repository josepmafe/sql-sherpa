# Using Home

Home is the first screen: five headline numbers for the last 30 days, a weekly revenue trend and a strip that says how fresh and how healthy the data is. It reads only `marts.dim_date`, `marts.fct_orders` and `marts.fct_refunds` and shows aggregates, never rows ([UI spec §6.2](../../superpowers/specs/2026-10-24-sql-sherpa-ui-design.md)). Start the stack as in the [README Quickstart](../../../README.md#quickstart), run `make build-prod`, and open <http://127.0.0.1:8090>.

## The tiles

Each tile shows the last 30 days ending on **as of**, the latest day with orders in `marts.fct_orders` (never today's date), and its change against the 30 days before. The definitions are the data platform spec's ([§6.6](../../superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md)):

| Tile | Change shown as |
|---|---|
| Net revenue, Orders, AOV, New customers | relative, in % |
| Refund rate | difference, in percentage points (`pp`) |

What each counts (non-cancelled orders by `order_date`, refunds by `refund_date`, new customers by first order) is in [data platform spec §6.6](../../superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md).

The windows are (as of − 30, as of] and (as of − 60, as of − 30]. The arrow and colour say whether the move is good (green) or bad (red): for the refund rate, up is bad. The words next to them say the same ("up 12.0% on the previous 30 days, an improvement"), so nothing relies on colour. Net revenue has a tooltip: refunds of recent orders keep arriving for about 40 days, so the latest period reads slightly high.

A tile shows "—" for its change, and keeps its value, when:

- the history is shorter than 59 days (the first order is later than 59 days before as of), so the earlier window would be incomplete;
- the earlier value is 0 (no orders, no sales); or
- the value itself is empty (an AOV with no orders).

## The trend

Net revenue per week (Monday to Sunday) for the last 26 weeks, oldest first. The latest week is marked partial unless as of is its Sunday. Black Friday weeks are highlighted. A shorter history shows fewer bars. A table of the same weeks sits beside the chart for screen readers.

## The data-status strip

| Item | Source |
|---|---|
| Data as of | the as-of date above |
| Last full build, with its node count | the newest full `dbt build` on prod in `ops.dbt_run_results` |
| N failing tests | the same build; a link to [Lineage](using-lineage.md) with "Failing only" on. A warning is not failing |
| Sources fresh, or one line per source in warning or error | `target-prod/sources.json`, written by `make build-prod` |

Each item that cannot be shown says why in its place, and the others stay: one broken dependency never blanks the screen.

## Symptoms

| Symptom | Fix |
|---|---|
| "No data yet" in place of the tiles | No marts, or marts with no orders: `make backfill && make build-prod` |
| "The figures are unavailable" with a Retry | The database is down, a query timed out or the server is `busy`: start Postgres (`docker compose up -d --wait postgres`) and press Retry |
| "build status unavailable" | The build record in `ops.dbt_run_results` could not be read (database down, missing grant): `dbt run-operation grant_read_access --target prod --target-path target-prod`, or start the database |
| "no full prod build recorded (run make build-prod)" | No full `dbt build` on prod is recorded yet; a build of one model does not count |
| "freshness unavailable" | `sources.json` is half-written (a build is running) or unreadable: wait for `make build-prod` and reload |
| "no freshness check recorded (run make build-prod)" | `sources.json` does not exist yet: `make build-prod` |
| `marketing.ad_spend_daily: warning, last loaded N h ago` | Expected once the campaigns end: they stop at the catalog horizon ([data platform spec §5.2](../../superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md#52-daily-simulation-scale--10); the warn threshold is 48 h (§7)), so the table stops growing |
| A tile's change is "—" | One of the three cases above; a short history is the usual one |
| The numbers did not move after `make advance` | Run `make build-prod`, then reload: Home reads the marts, not the raw tables |

## Next

- [Extending the UI](../advanced/extending-the-ui.md#4-add-or-change-a-home-tile): add or change a tile.
- [Using Lineage](using-lineage.md): where the failing-tests link goes.
