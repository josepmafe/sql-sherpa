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

Top 10 products by net revenue. A line's `net_amount` is its gross minus its share of the discount, and its `refunded_revenue` covers return refunds only:

```sql
select p.product_name, round(sum(i.net_amount - i.refunded_revenue), 2) as net_revenue
from marts.fct_order_items as i
inner join marts.dim_products as p using (variant_id)
where not i.is_cancelled
group by 1 order by 2 desc limit 10;
```

Conversion rate by channel (the share of web sessions that ended in a purchase, defined in the spec's section 6.6):

```sql
select marketing_channel, count(*) as sessions,
       round(avg(purchased::int), 4) as conversion_rate
from marts.fct_sessions group by 1 order by 2 desc;
```

New against returning customers (`is_first_order` marks a customer's first non-cancelled order), and customers by segment (`prospect`, `new`, `active`, `at_risk`, `churned`; rules in the `customer_segment` column description):

```sql
select case when is_first_order then 'new' else 'returning' end as customer_type,
       count(*) as orders, round(sum(net_revenue) / count(*), 2) as aov
from marts.fct_orders where not is_cancelled group by 1;

select customer_segment, count(*) as customers
from marts.dim_customers
group by 1 order by 2 desc;
```

## Traps

### Cancelled orders

`fct_orders` keeps cancelled orders. Their amount columns (`gross_revenue`, `cogs`) describe the order as placed and keep their values; the outcome columns (`net_revenue`, `gross_margin`) are 0. Filter `where not is_cancelled` for every sum of `gross_revenue` and every count or average of orders (in the 365-day dataset the 398 cancelled orders hold 36,741.85 of `gross_revenue` and 0.00 of `net_revenue`).

### Refunds by order date or by refund date

`fct_orders.refunded_revenue` belongs to the order's date; `fct_refunds.revenue_amount` to the refund's date (`refund_date`). They answer "how much of January's sales was later refunded?" against "how much did we refund in January?". `fct_refunds.amount` is the cash refunded (tax included); `revenue_amount` is the revenue part (tax removed, 0 for `cancellation` refunds).

```sql
select 'by order date' as basis, round(sum(refunded_revenue), 2) as refunded_revenue
from marts.fct_orders where order_date between '2026-01-01' and '2026-01-31'
union all
select 'by refund date', round(sum(revenue_amount), 2)
from marts.fct_refunds where refund_date between '2026-01-01' and '2026-01-31';
```

### The `unknown` channel

`marketing_channel` is one of eight channels or `unknown`: a web order whose purchase event has not arrived yet (late events) is `unknown` until it lands. Keep `unknown` in breakdowns so totals add up. For customers it is large: `dim_customers.acquisition_channel` is `unknown` for every prospect, so filter `order_count >= 1` in acquisition analyses.

### `as_of_date`, not today

`days_since_last_order` and the segments are measured to `as_of_date`, never `now()`. Use the same anchor in your own queries: `select max(order_date) from marts.fct_orders;`.

### Sessions are web only

A session is one device's events with no gap over 30 minutes. App orders (R12) have no session, so session counts and `conversion_rate` (purchased sessions / all sessions, not orders; a session whose order was later cancelled still counts as purchased) leave them out. Compare orders per channel with `fct_orders`, and join `fct_orders` to `fct_sessions` on `session_id`. Anonymous visitors never return (every session of a non-customer is a first session). `fct_sessions.customer_id` is the customer the device was identified as at its latest identification, so sessions from before a visitor logged in carry it too, and it is null for visitors never identified. `is_first_session` is true for a visitor's first session, where the visitor is the customer when known (across devices) and the device otherwise.

### Line grain against order grain

Joining `fct_orders` to `fct_order_items` repeats each order's columns once per line, so summing an order column after the join multiplies it (in the 365-day dataset, gross revenue came out at 1,589,281.58 instead of 717,784.62). Sum order columns in `fct_orders` alone and line columns (`gross_amount`, `net_amount`, `quantity`) in `fct_order_items`; to use both, aggregate the lines in a subquery first. `fct_order_discounts` fans out the same way when an order has several discounts: filter to one promotion, or use `where exists (...)`.

## Fuzzy search on products

`dim_products` has GIN trigram indexes on the name, brand and category columns, and `pg_trgm` is on the search path.

- `'text' <% column`: `text` is similar to a run of whole words in `column` (word similarity at least 0.6). Use it for a misspelled word inside a longer name.
- `column % 'text'`: whole-string similarity above 0.3. Use it for short values such as a brand or category.
- `ilike '%text%'` finds exact fragments only.

```sql
select distinct product_name, round(word_similarity('dumbell', product_name)::numeric, 2) as sim
from marts.dim_products
where 'dumbell' <% product_name
order by sim desc, product_name limit 5;
```

## Connecting as `sherpa_agent`

The agent's role reads only the `marts` schema (and uses the functions in `extensions`) on prod and on the frozen eval database. It cannot read staging, intermediate, seeds, raw or `dev_` schemas, cannot create temporary tables, and its queries stop after 5 seconds. dbt grants this at the end of every prod and eval build.

The container already holds the `.env` variables, so read the password there instead of putting it on a command line:

```bash
agent_psql() {
  docker compose exec -T postgres sh -c \
    'PGPASSWORD="$SHERPA_AGENT_PASSWORD" exec psql -h "$(hostname -i)" -U "$SHERPA_AGENT_USER" -d "$POSTGRES_DB" "$@"' sh "$@"
}
agent_psql -c "select count(*) from marts.fct_orders"
agent_psql -c "select count(*) from dev_marts.fct_orders"   # ERROR:  permission denied for schema dev_marts
```

For the eval database add `-d sherpa_eval` (see [the frozen eval database](building-the-warehouse.md#the-frozen-eval-database)).
## Errors

| Symptom | Fix |
|---|---|
| `relation "marts.fct_orders" does not exist` | The prod warehouse is not built: `make build-prod`. |
| Revenue is too high | Missing `where not is_cancelled`, or an order column summed after a line join. |
| `permission denied for schema ...` as `sherpa_agent` | Expected outside `marts`; rebuild prod (`make build-prod`) if `marts` itself is refused. |
| `canceling statement due to statement timeout` | The agent role stops queries after 5 seconds; add filters. |

## Next

- [Building the warehouse](building-the-warehouse.md): build, test and debug the models behind these tables.
- [Adding or changing a mart](../advanced/adding-or-changing-a-mart.md): add a column, a mart or a metric.
