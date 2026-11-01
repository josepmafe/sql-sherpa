# Querying the marts

The marts are the tables to query: tested, documented, with the business metrics already worked out. This guide assumes the prod warehouse is built ([Building the warehouse](building-the-warehouse.md)). Run the queries in `psql` as the admin role; `\d+ marts.fct_orders` shows each column's description.

```bash
docker compose exec postgres psql -U sherpa_admin -d sherpa
```

## The marts

| Mart | One row per |
|---|---|
| `fct_orders` | order (cancelled orders included, flagged `is_cancelled`) |
| `fct_order_items` | order line |
| `fct_order_discounts` | discount applied to an order |
| `fct_returns` | return |
| `fct_refunds` | refund |
| `dim_customers` | customer |
| `dim_products` | product variant (size and colour) |
| `dim_promotions` | promotion |
| `dim_date` | calendar day |
| `fct_sessions` | web session (a device's events with no gap over 30 minutes) |
| `dim_campaigns` | marketing campaign |
| `fct_ad_spend_daily` | paid campaign and day with spend |
| `fct_leads` | CRM lead |

## Which revenue to use

Use `net_revenue` for "revenue" unless the question says otherwise. It is gross minus discounts minus refunds, and 0 for a cancelled order. The other columns (`gross_revenue`, `discount_amount`, `refunded_revenue`, `gross_margin`), AOV and refund rate are defined in [the data platform spec §6.6](../../superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md); the same text is in the column comments.

## Example queries

Net revenue by month, and AOV by marketing channel:

```sql
select date_trunc('month', order_date)::date as month, round(sum(net_revenue), 2) as net_revenue
from marts.fct_orders where not is_cancelled group by 1 order by 1;

select marketing_channel, count(*) as orders, round(sum(net_revenue) / count(*), 2) as aov
from marts.fct_orders where not is_cancelled group by 1 order by 3 desc;
```

Top 10 products by net revenue (a line's `net_amount` is its gross minus its share of the discount; its `refunded_revenue` covers return refunds only):

```sql
select p.product_name, round(sum(i.net_amount - i.refunded_revenue), 2) as net_revenue
from marts.fct_order_items as i
inner join marts.dim_products as p using (variant_id)
where not i.is_cancelled
group by 1 order by 2 desc limit 10;
```

Conversion rate by channel is `avg(purchased::int)` over `marts.fct_sessions`, grouped by `marketing_channel` (defined in the spec's section 6.6).

## Campaigns, ad spend and leads

ROAS (attributed net revenue / spend) by channel for the last full month of data, anchored on the latest spend day like `as_of_date`. ROAS and CAC are defined in [the spec §6.6](../../superpowers/specs/2026-10-22-sql-sherpa-data-platform-design.md):

```sql
with as_of as (
    select date_trunc('month', max(spend_date)) as this_month from marts.fct_ad_spend_daily
)
select a.channel, round(sum(a.spend), 2) as spend,
       round(sum(a.attributed_net_revenue) / nullif(sum(a.spend), 0), 2) as roas
from marts.fct_ad_spend_daily as a
cross join as_of
where a.spend_date >= as_of.this_month - interval '1 month' and a.spend_date < as_of.this_month
group by a.channel order by roas desc;
```

CAC (spend / new customers) by campaign. Divide the sums, never average the daily ratios; a campaign with no new customers has no CAC:

```sql
select c.campaign_name, c.channel, round(sum(a.spend), 2) as spend, sum(a.new_customers) as new_customers,
       round(sum(a.spend) / nullif(sum(a.new_customers), 0), 2) as cac
from marts.fct_ad_spend_daily as a
inner join marts.dim_campaigns as c using (campaign_id)
group by c.campaign_name, c.channel order by cac;
```

Lead-to-customer conversion by `lead_source`, counted by `lead_id` (the email is a hash):

```sql
select lead_source, count(lead_id) as leads, count(lead_id) filter (where is_converted) as converted,
       round(count(lead_id) filter (where is_converted) * 1.0 / count(lead_id), 4) as conversion_rate
from marts.fct_leads group by lead_source order by conversion_rate desc;
```

## Traps

### Cancelled orders

`fct_orders` keeps cancelled orders. Their amount columns (`gross_revenue`, `cogs`) describe the order as placed and keep their values; the outcome columns (`net_revenue`, `gross_margin`) are 0. Filter `where not is_cancelled` for every sum of `gross_revenue` and every count or average of orders.

### Refunds by order date or by refund date

`fct_orders.refunded_revenue` belongs to the order's date; `fct_refunds.revenue_amount` to the refund's date (`refund_date`): "how much of January's sales was later refunded?" against "how much did we refund in January?". `fct_refunds.amount` is the cash refunded (tax included); `revenue_amount` is the revenue part (tax removed, 0 for `cancellation` refunds).

### The `unknown` channel

`marketing_channel` is one of eight channels or `unknown`: a web order whose purchase event has not arrived yet (late events) is `unknown` until it lands. Keep `unknown` in breakdowns so totals add up. `dim_customers.acquisition_channel` is `unknown` for every prospect: filter `order_count >= 1` in acquisition analyses.

### `as_of_date`, not today

`days_since_last_order` and the segments are measured to `as_of_date`, never `now()`. Use the same anchor in your own queries: `select max(order_date) from marts.fct_orders;`.

### Sessions are web only

A session is one device's events with no gap over 30 minutes. App orders (R12) have no session, so session counts and `conversion_rate` (purchased sessions / all sessions, not orders; a session whose order was later cancelled still counts as purchased) leave them out. Compare orders per channel with `fct_orders`, and join `fct_orders` to `fct_sessions` on `session_id`. Anonymous visitors never return (every session of a non-customer is a first session). `fct_sessions.customer_id` is the customer the device was identified as at its latest identification (null for visitors never identified), so sessions from before a login carry it too. `is_first_session` is true for a visitor's first session, where the visitor is the customer when known (across devices) and the device otherwise.

### Line grain against order grain

Joining `fct_orders` to `fct_order_items` repeats each order's columns once per line, so summing an order column after the join multiplies it (in the 365-day dataset, 1,589,281.58 instead of 717,784.62). Sum order columns in `fct_orders` alone and line columns in `fct_order_items`; to use both, aggregate the lines in a subquery first. `fct_order_discounts` fans out the same way: filter to one promotion, or use `where exists (...)`.

### Campaigns and leads

- **Email has no ad spend.** `fct_ad_spend_daily` holds paid campaigns only; email campaigns are in `dim_campaigns`.
- **Clicks are not sessions (R11).** `clicks` and `impressions` are the ad platform's; `sessions` are tracked web sessions, so it is lower than `clicks`. An order belongs to its session's day, even when placed after midnight.
- **`lifecycle_stage` lags `is_converted`.** A converted lead is not always at `customer` yet; a lead at `customer` is always converted. Use `is_converted` for conversion.
- **`fct_leads.email` is a hash,** like `dim_customers.email`: count by `lead_id`, and reach the customer through `converted_customer_id`.

## Fuzzy search on products

`dim_products` has GIN trigram indexes on the name, brand and category columns, and `pg_trgm` is on the search path. `'text' <% column` matches a misspelled word inside a longer name (word similarity at least 0.6); `column % 'text'` matches short values such as a brand (whole-string similarity above 0.3); `ilike '%text%'` finds exact fragments only.

```sql
select distinct product_name, round(word_similarity('dumbell', product_name)::numeric, 2) as sim
from marts.dim_products
where 'dumbell' <% product_name
order by sim desc, product_name limit 5;
```

## Connecting as `sherpa_agent`

The agent's role reads only the `marts` schema (and uses the functions in `extensions`) on prod and on the frozen eval database. It cannot read staging, intermediate, seeds, raw or `dev_` schemas, cannot create temporary tables, and its queries stop after 5 seconds. dbt grants this at the end of every prod and eval build.

The container holds the `.env` variables, so the password stays off the command line:

```bash
agent_psql() {
  docker compose exec -T postgres sh -c \
    'PGPASSWORD="$SHERPA_AGENT_PASSWORD" exec psql -h "$(hostname -i)" -U "$SHERPA_AGENT_USER" -d "$POSTGRES_DB" "$@"' sh "$@"
}
agent_psql -c "select count(*) from marts.fct_orders"
agent_psql -c "select count(*) from dev_marts.fct_orders"   # ERROR:  permission denied for schema dev_marts
```

`dim_customers` and `fct_leads` are views: `email` is a salted hash (the same value always gives the same hash) and customer names are not in them. Count or group by `email`, identify customers by `customer_id`, and do not compare it with a value, a pattern or a string function (it gives meaningless results). The table behind each is closed to the role:

```bash
agent_psql -c "select * from marts.dim_customers_unmasked"   # ERROR:  permission denied for table dim_customers_unmasked
```

For the eval database add `-d sherpa_eval` (see [the frozen eval database](building-the-warehouse.md#the-frozen-eval-database)).

## Errors

| Symptom | Fix |
|---|---|
| `relation "marts.fct_orders" does not exist` | The prod warehouse is not built: `make build-prod`. |
| Revenue is too high | Missing `where not is_cancelled`, or an order column summed after a line join. |
| `where email = 'a@b.com'` finds nothing, or a `LIKE` on it finds random rows | `email` is a 32-character hash; look the customer up by `customer_id` |
| `permission denied for schema ...` as `sherpa_agent` | Expected outside `marts`; rebuild prod (`make build-prod`) if `marts` itself is refused. |
| `canceling statement due to statement timeout` | The agent role stops queries after 5 seconds; add filters. |

## Next

[Building the warehouse](building-the-warehouse.md) builds and debugs the models behind these tables; [Adding or changing a mart](../advanced/adding-or-changing-a-mart.md) adds a column, a mart or a metric.
