# Querying the marts

The marts are the tables to query: clean, tested and documented, with the business metrics already worked out. This guide shows what each mart answers, which revenue column to use, copy-paste queries for common questions, the traps to avoid, fuzzy search on products, and what the agent's database role can see. It assumes the warehouse is built on prod ([Building the warehouse](building-the-warehouse.md), `make build-prod`).

Every query runs in `psql` as the admin role, and the outputs are real, from the 365-day dataset (the exact numbers change with the seed and the dates):

```bash
docker compose exec postgres psql -U sherpa_admin -d sherpa
```

A **mart** is a dbt model in the `marts` schema, built as a table for consumers. Its columns and types are checked against the YAML on every build (a *contract*), and its descriptions are stored as Postgres comments, so `\d+ marts.fct_orders` in psql shows what each column means.

## The marts

| Mart | One row per | Answers |
|---|---|---|
| `fct_orders` | order (cancelled orders included, flagged) | Revenue, discounts, refunds, margin, channel and delivery time by day, customer, country or channel |
| `fct_order_items` | order line | Sales by product or category; join to `dim_products` |
| `fct_returns` | return | Why customers return, how many items come back and are restockable |
| `fct_refunds` | refund | Money given back, by reason, by the date it was refunded |
| `dim_customers` | customer | Lifetime value, acquisition channel, lifecycle segment, VIPs |
| `dim_products` | product variant (size and colour) | Names, brand, three-level category, current prices |
| `dim_promotions` | promotion | Type, value, coupon codes, redemptions, total discount given |
| `dim_date` | calendar day | Week, month, quarter, weekend and Black Friday week |

Facts (`fct_`) record events; dimensions (`dim_`) describe things you group or filter by. Each mart's grain, primary key and domain are in its `meta` block in the dbt docs (`make docs-prod`).

## Which revenue to use

The metric definitions are written once, in `dbt/models/marts/_metrics.md` (one *doc block* per metric: reusable Markdown that column descriptions pull in with `doc('metric_net_revenue')`). So the text below is the same text the column comments carry.

| Column | Meaning | Use it for |
|---|---|---|
| `gross_revenue` | Σ quantity × unit price over the lines. Net of tax, no shipping, before discounts | "How much did we list-price sell?" |
| `discount_amount` | Promotions and coupons, spread over the lines | Discount cost |
| `refunded_revenue` | Revenue given back for `return` and `goodwill` refunds, tax removed. `cancellation` refunds count 0 | Refund cost |
| `net_revenue` | gross − discount − refunded, **0 for a cancelled order** | **"Revenue", unless the question says otherwise** |
| `gross_margin` | net_revenue − (cogs − returned_cogs) | Profit before overheads |

All sums are over non-cancelled orders (`where not is_cancelled`, see the traps below). The ratios:

- **AOV** (average order value) = Σ `net_revenue` / count of non-cancelled orders.
- **Refund rate** = Σ `refunded_revenue` / Σ `gross_revenue`, over non-cancelled orders.

### Net revenue by month

```sql
select date_trunc('month', order_date)::date as month,
       round(sum(net_revenue), 2) as net_revenue
from marts.fct_orders
where not is_cancelled
group by 1
order by 1
limit 4;
```

```
   month    | net_revenue
------------+-------------
 2025-10-01 |    13619.12
 2025-11-01 |    69771.69
 2025-12-01 |    73566.43
 2026-01-01 |    35329.31
```

### Refund rate overall

```sql
select round(sum(refunded_revenue) / sum(gross_revenue), 4) as refund_rate
from marts.fct_orders
where not is_cancelled;
```

```
 refund_rate
-------------
      0.0793
```

### Top 10 products by net revenue

Product sales come from the lines. A line's `net_amount` is its gross amount minus its share of the discount; `refunded_revenue` on a line covers return refunds only (goodwill and cancellation refunds exist only on the order):

```sql
select p.product_name,
       round(sum(i.net_amount - i.refunded_revenue), 2) as net_revenue
from marts.fct_order_items as i
inner join marts.dim_products as p using (variant_id)
where not i.is_cancelled
group by 1
order by 2 desc
limit 10;
```

```
         product_name         | net_revenue
------------------------------+-------------
 Halden Flex Dumbbells        |   183000.79
 Orbit Max Hydration          |    49293.18
 Gravel Core Resistance Bands |    44046.34
 Ironpeak Elite Dumbbells     |    23485.92
 Pacer Rapid Hip Packs        |    16476.84
 Ridgeline Rapid Tents        |    14731.77
 Aerolite Summit Yoga Mats    |    11729.99
 Solace Core Jump Ropes       |    11721.48
 Pacer Light Hydration        |    10388.98
 Altura Core Jump Ropes       |     8274.44
```

### Refund rate by category

```sql
select p.category_l1,
       round(sum(i.refunded_revenue) / nullif(sum(i.gross_amount), 0), 4) as refund_rate
from marts.fct_order_items as i
inner join marts.dim_products as p using (variant_id)
where not i.is_cancelled
group by 1
order by 2 desc;
```

```
 category_l1 | refund_rate
-------------+-------------
 Footwear    |      0.1543
 Apparel     |      0.0815
 Accessories |      0.0737
 Equipment   |      0.0727
```

Line refunds exclude goodwill refunds, so the line-level rates sit slightly below the order-level 0.0793 above.

### AOV by marketing channel

```sql
select marketing_channel,
       count(*) as orders,
       round(sum(net_revenue) / count(*), 2) as aov
from marts.fct_orders
where not is_cancelled
group by 1
order by 3 desc;
```

```
 marketing_channel | orders |  aov
-------------------+--------+-------
 email             |    494 | 82.89
 direct            |   1245 | 82.58
 referral          |    235 | 81.36
 app               |   2666 | 81.24
 paid_search       |    943 | 79.94
 paid_social       |    577 | 77.37
 affiliate         |    260 | 76.71
 organic           |   1215 | 76.09
```

### Customers by segment

`dim_customers.customer_segment` classifies a customer by their non-cancelled orders and how long ago the last one was, measured to `as_of_date` (see the traps):

| Segment | Rule |
|---|---|
| `prospect` | 0 orders |
| `new` | 1 order, the last 90 days ago or less |
| `active` | 2 or more orders, the last 90 days ago or less |
| `at_risk` | last order 91 to 180 days ago |
| `churned` | last order more than 180 days ago |

```sql
select customer_segment, count(*) as customers
from marts.dim_customers
group by 1
order by 2 desc;
```

```
 customer_segment | customers
------------------+-----------
 prospect         |      2179
 at_risk          |       780
 active           |       752
 churned          |       668
 new              |       541
```

### VIP share

`is_vip` marks the top 10% of buyers by `lifetime_net_revenue`, rounded up (ties go to the lowest `customer_id`). Prospects are not buyers and never VIPs:

```sql
select count(*) filter (where is_vip) as vips,
       count(*) filter (where order_count >= 1) as buyers,
       round(sum(lifetime_net_revenue) filter (where is_vip) / sum(lifetime_net_revenue), 3) as vip_share_of_revenue
from marts.dim_customers;
```

```
 vips | buyers | vip_share_of_revenue
------+--------+----------------------
  275 |   2741 |                0.405
```

## Traps

### Cancelled orders

`fct_orders` keeps cancelled orders, flagged with `is_cancelled`. Their amount columns describe the order **as placed** and keep their values; their outcome columns are 0 because nothing was sold:

```sql
select is_cancelled, count(*) as orders,
       sum(gross_revenue) as gross_revenue, sum(net_revenue) as net_revenue, sum(cogs) as cogs
from marts.fct_orders
group by 1;
```

```
 is_cancelled | orders | gross_revenue | net_revenue |    cogs
--------------+--------+---------------+-------------+-----------
 f            |   7635 |     681042.77 |   611882.32 | 301133.34
 t            |    398 |      36741.85 |        0.00 |      0.00
```

Summing `gross_revenue` without `where not is_cancelled` overstates revenue by the cancelled 36,741.85. Every metric query on this page filters on it; `net_revenue`, `cogs` and `gross_margin` are safe either way because they are 0 for cancelled orders, but a count of orders or an average is not.

### Refunds by order date or by refund date

`fct_orders.refunded_revenue` is attributed to the **order's date**, `fct_refunds.revenue_amount` to the **refund's date** (`refund_date`). Both are right; they answer different questions: "how much of January's sales was later refunded?" against "how much did we refund in January?":

```sql
select 'by order date' as basis, round(sum(refunded_revenue), 2) as refunded_revenue
from marts.fct_orders
where order_date between '2026-01-01' and '2026-01-31'
union all
select 'by refund date', round(sum(revenue_amount), 2)
from marts.fct_refunds
where refund_date between '2026-01-01' and '2026-01-31';
```

```
     basis      | refunded_revenue
----------------+------------------
 by order date  |          3636.74
 by refund date |          6249.73
```

`fct_refunds.amount` is the cash refunded, tax included; `revenue_amount` is the revenue part (tax removed, and 0 for `cancellation` refunds).

### The `unknown` channel

Every order has a `marketing_channel`: one of the eight channels (`direct`, `organic`, `paid_search`, `paid_social`, `email`, `affiliate`, `referral`, `app`) or `unknown`. A web order takes the channel of the session that contains its purchase event. If that event has not arrived yet (events can be delivered late), the order is `unknown` until it lands. App orders are always `app`. Keep `unknown` in channel breakdowns so the totals add up, and expect it to be small:

```sql
select count(*) filter (where marketing_channel = 'unknown') as unknown_orders from marts.fct_orders;
```

```
 unknown_orders
----------------
              0
```

### `as_of_date`, not today

Time-relative columns such as `days_since_last_order` and the segments are measured to `as_of_date`, the latest `order_date` in `fct_orders` (here 2026-10-22), never to `now()`. The same data therefore always gives the same answer. In your own queries use the same anchor:

```sql
select max(order_date) as as_of_date from marts.fct_orders;
```

```
 as_of_date
------------
 2026-10-22
```

### Line grain against order grain

`fct_orders` has one row per order, `fct_order_items` one per line. Joining them repeats each order's columns once per line, so summing an order column afterwards multiplies it:

```sql
select round(sum(o.gross_revenue), 2) as wrong_gross_after_join,
       (select round(sum(gross_revenue), 2) from marts.fct_orders) as right_gross
from marts.fct_orders as o
inner join marts.fct_order_items as i using (order_id);
```

```
 wrong_gross_after_join | right_gross
------------------------+-------------
             1589281.58 |   717784.62
```

Sum order columns in `fct_orders` alone, and line columns (`gross_amount`, `net_amount`, `quantity`) in `fct_order_items`. If you need both, aggregate the lines first in a subquery and join that to the orders.

## Fuzzy search on products

People misspell product names. `dim_products` has GIN trigram indexes on `product_name`, `brand` and the three category columns, recreated by a post-hook on every build, and the `pg_trgm` extension lives in the `extensions` schema, which is on the database search path. A *trigram* is a group of three consecutive letters; two strings with many in common are similar.

- `'text' <% column` is true when `text` is similar to some run of whole words in `column` (word similarity of at least 0.6). Use it to look up a word inside a longer name.
- `word_similarity(text, column)` returns that score, from 0 to 1.
- `column % 'text'` compares the whole strings (similarity above 0.3), and `similarity(a, b)` returns that score. It suits short values such as a brand or a category.

```sql
select distinct product_name, round(word_similarity('dumbell', product_name)::numeric, 2) as sim
from marts.dim_products
where 'dumbell' <% product_name
order by sim desc, product_name
limit 5;
```

```
       product_name        | sim
---------------------------+------
 Aerolite Active Dumbbells | 0.60
 Altura Swift Dumbbells    | 0.60
 Elevate Swift Dumbbells   | 0.60
 Fjordic Pro Dumbbells     | 0.60
 Gravel Swift Dumbbells    | 0.60
```

Without the `limit` the query returns all 15 dumbbell products. `product_name % 'dumbell'` returns only `Quill Max Dumbbells` (similarity 0.33): whole-string similarity also counts the trigrams of the other words in the name, so in a three-word name one matching word usually stays below the 0.3 threshold. `ilike '%dumbell%'` finds nothing for the misspelling. Use `ilike` for exact fragments, `<%` for a misspelled word in a name and `%` for a misspelled short value. The GIN trigram index supports both operators, so on a large table the planner uses it instead of comparing every row; on a table this small (about 1,500 rows) it may still choose a sequential scan, which is just as fast here. The usual follow-up is to take the matched `variant_id` values and query `fct_order_items`.

## Connecting as `sherpa_agent`

The agent connects as the `sherpa_agent` role, which can read **only the `marts` schema** (and use the functions in `extensions`) on prod and on the frozen eval database. It cannot read staging, intermediate, seeds, the raw schemas or the `dev_` schemas, and its queries stop after 5 seconds. dbt grants this access at the end of every run on those targets (`make grant-agent` re-applies it without a build).

Load the variables from `.env` into your shell without printing them, then run psql inside the container (the password comes from `$SHERPA_AGENT_PASSWORD`):

```bash
set -a; . ./.env; set +a
AGENT="docker compose exec -T -e PGPASSWORD=$SHERPA_AGENT_PASSWORD postgres psql -U sherpa_agent -d sherpa"
$AGENT -c "select count(*) from marts.fct_orders"
```

```
 count
-------
  8033
```

Anything outside the marts is refused:

```bash
$AGENT -c "select count(*) from staging.stg_shop__orders"
$AGENT -c "select count(*) from raw_shop.orders"
$AGENT -c "select count(*) from dev_marts.fct_orders"
```

```
ERROR:  permission denied for schema staging
ERROR:  permission denied for schema raw_shop
ERROR:  permission denied for schema dev_marts
```

Fuzzy search works for the agent too (`select similarity('dumbell', 'dumbbell')` returns 0.7), and `show statement_timeout` returns `5s`. For the frozen eval database use `-d sherpa_eval` ([Building the warehouse](building-the-warehouse.md#the-frozen-eval-database)).

`pytest -m warehouse` checks all of this against the real database (the `warehouse` marker needs `make build-prod`, and the test for `dev_marts` also needs `make build`).

## Next

- [Building the warehouse](building-the-warehouse.md): build, test and debug the models behind these tables.
- [Adding or changing a mart](../advanced/adding-or-changing-a-mart.md): add a column, a mart or a metric.
