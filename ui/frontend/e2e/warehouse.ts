import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

import type { Page } from "@playwright/test";
import type { components } from "../src/api/schema";

const REPO_ROOT = resolve(import.meta.dirname, "../../..");
const COMPOSE_FILE = resolve(REPO_ROOT, "docker-compose.yml");

// An aggregate, so no row comes back before the whole cross join is counted: far longer than any test.
export const LONG_AGGREGATE =
  "select count(*) as n from marts.fct_order_items a cross join marts.fct_order_items b cross join marts.fct_order_items c";

// The admin login lives only inside the postgres container, so the check runs through `docker compose exec`.
export function adminScalar(sql: string): string {
  return psqlScalar('"$SHERPA_ADMIN_USER"', sql);
}

// The container's superuser, the only login that sees another role's query text in pg_stat_activity.
export function superuserScalar(sql: string): string {
  return psqlScalar('"${POSTGRES_USER:-postgres}"', sql);
}

export function make(...args: string[]): string {
  return execFileSync("make", args, { cwd: REPO_ROOT, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
}

export async function runQuery(page: Page, sql: string): Promise<void> {
  const editor = page.locator(".cm-content");
  await editor.click();
  await editor.fill(sql);
  await page.getByRole("button", { name: "Run", exact: true }).click();
}

function psqlScalar(user: string, sql: string): string {
  return execFileSync(
    "docker",
    ["compose", "-f", COMPOSE_FILE, "exec", "-T", "postgres", "sh", "-c", `psql -U ${user} -d "$POSTGRES_DB" -tA -c "$0"`, sql],
    { encoding: "utf8" },
  ).trim();
}

type Tile = components["schemas"]["Tile"];

export interface HomeExpectations {
  asOf: string;
  tiles: Tile[];
  trendWeeks: number;
  lastWeekStart: string;
  lastWeekNetRevenue: string;
  trendHasPartialWeek: boolean;
  trendHasBlackFriday: boolean;
  build: { startedAt: string; nodeCount: number; failedNodes: number; failingTests: number };
}

const WINDOW_DAYS = 30;
const FLAT_BELOW = 0.0005;
const TREND_WEEKS = 26;
const ISO_UTC = `'YYYY-MM-DD"T"HH24:MI:SS"Z"'`;
const TILES = [
  { id: "net_revenue", title: "Net revenue", format: "currency", decimals: 0, up_is_good: true },
  { id: "orders", title: "Orders", format: "count", decimals: 0, up_is_good: true },
  { id: "aov", title: "Average order value", format: "currency", decimals: 2, up_is_good: true },
  { id: "refund_rate", title: "Refund rate", format: "percent", decimals: 1, up_is_good: false },
  { id: "new_customers", title: "New customers", format: "count", decimals: 0, up_is_good: true },
] as const;

// Home's figures from the warehouse by a different route than the app's: explicit `between` ranges instead of the
// app's `as_of - n` filters, and new customers from dim_customers.first_order_at instead of is_first_order.
export function homeExpectations(): HomeExpectations {
  const asOf = adminScalar("select max(order_date) from marts.fct_orders");
  const last = windowSums(asOf, 0);
  const earlier = windowSums(asOf, WINDOW_DAYS);
  const earlierNet = earlierNetAsStood(asOf);
  const build = adminScalar(`
    with latest as (
      select invocation_id from ops.dbt_run_results
      where target_name = 'prod' and command = 'build' and is_full_run
      group by invocation_id order by max(run_started_at) desc, invocation_id desc limit 1
    )
    select to_char(min(run_started_at) at time zone 'UTC', ${ISO_UTC}), count(*),
      count(*) filter (where resource_type in ('model', 'seed', 'snapshot')
                         and status in ('error', 'runtime error', 'partial success')),
      count(*) filter (where resource_type in ('test', 'unit_test') and status in ('fail', 'error', 'runtime error'))
    from ops.dbt_run_results where invocation_id = (select invocation_id from latest)`).split("|");
  const lastWeekStart = adminScalar(`select to_char(date_trunc('week', date '${asOf}'), 'YYYY-MM-DD')`);

  return {
    asOf,
    tiles: TILES.map((tile) => {
      const values = {
        net_revenue: [last.net, earlierNet],
        orders: [last.orders, earlier.orders],
        aov: [last.net / last.orders, earlierNet / earlier.orders],
        refund_rate: [last.refunds / last.gross, earlier.refunds / earlier.gross],
        new_customers: [last.newCustomers, earlier.newCustomers],
      }[tile.id];
      return homeTile(tile, values[0]!, values[1]!);
    }),
    trendWeeks: Number(
      adminScalar(`
        select count(*) from generate_series(
          greatest(date_trunc('week', date '${asOf}') - interval '${(TREND_WEEKS - 1) * 7} days',
                   date_trunc('week', (select min(order_date) from marts.fct_orders))),
          date_trunc('week', date '${asOf}'), interval '7 days')`),
    ),
    lastWeekStart,
    lastWeekNetRevenue: adminScalar(
      `select coalesce(sum(net_revenue), 0) from marts.fct_orders
       where not is_cancelled and order_date between date '${lastWeekStart}' and date '${lastWeekStart}' + 6`,
    ),
    // The last week is partial unless as_of is its Sunday, and the chart draws a partial week as partial even when it
    // is a Black Friday week: a Black Friday bar needs one in the 26 weeks that is complete.
    trendHasPartialWeek: adminScalar(`select extract(isodow from date '${asOf}') < 7`) === "t",
    trendHasBlackFriday:
      adminScalar(`
        select exists(select 1 from marts.dim_date where is_black_friday_week
          and week_start >= greatest(date_trunc('week', date '${asOf}') - interval '${(TREND_WEEKS - 1) * 7} days',
                                     date_trunc('week', (select min(order_date) from marts.fct_orders)))
          and week_start <= date_trunc('week', date '${asOf}')
            - case when extract(isodow from date '${asOf}') = 7 then interval '0 days' else interval '7 days' end)`) === "t",
    build: { startedAt: build[0]!, nodeCount: Number(build[1]), failedNodes: Number(build[2]), failingTests: Number(build[3]) },
  };
}

// The earlier window's net revenue as it stood WINDOW_DAYS ago: the refunds paid after that day are not deducted yet
// (UI spec §6.2), because the latest window has few refunds so far.
function earlierNetAsStood(asOf: string): number {
  const from = `date '${asOf}' - ${2 * WINDOW_DAYS - 1}`;
  const to = `date '${asOf}' - ${WINDOW_DAYS}`;
  return Number(
    adminScalar(`
      select coalesce(sum(o.gross_revenue - o.discount_amount), 0)
        - coalesce(sum((select coalesce(sum(r.revenue_amount), 0) from marts.fct_refunds r
                        where r.order_id = o.order_id and r.refund_date <= ${to})), 0)
      from marts.fct_orders o where not o.is_cancelled and o.order_date between ${from} and ${to}`),
  );
}

interface Sums {
  orders: number;
  net: number;
  gross: number;
  refunds: number;
  newCustomers: number;
}

// The 30 days ending `daysBack` days before as_of.
function windowSums(asOf: string, daysBack: number): Sums {
  const from = `date '${asOf}' - ${daysBack + WINDOW_DAYS - 1}`;
  const to = `date '${asOf}' - ${daysBack}`;
  const [orders, net, gross, refunds, newCustomers] = adminScalar(`
    select
      (select count(*) from marts.fct_orders where not is_cancelled and order_date between ${from} and ${to}),
      (select coalesce(sum(net_revenue), 0) from marts.fct_orders where not is_cancelled and order_date between ${from} and ${to}),
      (select coalesce(sum(gross_revenue), 0) from marts.fct_orders where not is_cancelled and order_date between ${from} and ${to}),
      (select coalesce(sum(revenue_amount), 0) from marts.fct_refunds where refund_date between ${from} and ${to}),
      (select count(*) from marts.dim_customers where (first_order_at at time zone 'UTC')::date between ${from} and ${to})`)
    .split("|")
    .map(Number);
  return { orders: orders!, net: net!, gross: gross!, refunds: refunds!, newCustomers: newCustomers! };
}

function homeTile(base: (typeof TILES)[number], value: number, earlier: number): Tile {
  const points = base.format === "percent";
  const change = earlier === 0 ? null : points ? value - earlier : (value - earlier) / Math.abs(earlier);
  // A change under 0.05% (0.05 points for the refund rate) is flat (UI spec §6.2).
  const direction = change === null ? null : Math.abs(change) < FLAT_BELOW ? "flat" : change > 0 ? "up" : "down";
  return {
    ...base,
    note: null,
    value: String(value),
    change: change === null ? null : String(change),
    change_kind: points ? "points" : "relative",
    direction,
    good: direction === null || direction === "flat" ? null : (direction === "up") === base.up_is_good,
  };
}
