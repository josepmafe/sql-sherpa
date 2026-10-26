{#-
    A docs block is named Markdown that YAML descriptions can reuse with doc('<name>').
    The block named __overview__ is special: dbt docs shows it as the landing page.
-#}
{% docs __overview__ %}

# SQL Sherpa warehouse

A synthetic e-commerce warehouse. A deterministic day-by-day generator fills Postgres with
the raw data of an online sports shop (footwear, apparel, accessories, equipment), and this dbt project turns it into documented,
tested models for analysis and for a text-to-SQL agent. All money is EUR and all
timestamps are UTC.

## Source systems

The generator writes four raw schemas, one per system the shop would run:

| Source | Schema | What it holds |
|---|---|---|
| `shop` | `raw_shop` | The store: customers, catalog, promotions, orders, payments, shipments, returns and refunds |
| `events` | `raw_events` | Website and app tracking events (page views, cart, checkout, purchase) |
| `marketing` | `raw_marketing` | Campaigns and daily ad spend reported by the ad platforms |
| `crm` | `raw_crm` | Leads and the marketing emails sent to them |

## Layers

- **Seeds** (`seeds`): small reference tables kept in the repo, such as country codes and
  the mapping of UTM source/medium to marketing channels.
- **Staging** (`staging`): one view per source table. Renames columns, casts types and
  cleans values; no business logic and no joins except to seeds.
- **Snapshots** (`snapshots`): `snap_product_variants` keeps every version of a product variant's
  prices, because the raw table only holds the current ones. Dev and prod share it.
- **Intermediate** (`intermediate`): joins and calculations that several marts share, such
  as spreading an order's discount over its lines or numbering web sessions. Most are
  ephemeral (inlined into the models that use them, so they build nothing). Not for consumers.
- **Marts** (`marts`): contract-enforced tables for consumers, grouped by domain:
    - **sales**: `fct_orders`, `fct_order_items`, `fct_order_discounts`, `fct_returns`, `fct_refunds`,
      `dim_promotions`
    - **customers**: `dim_customers`
    - **products**: `dim_products`
    - **marketing_web**: `fct_sessions`
    - **shared**: `dim_date`

  Metric definitions (net revenue, refund rate, ...) are written once in the `metric_<name>` doc
  blocks and reused in the column descriptions.

On the `dev` target every schema gets a `dev_` prefix (`dev_staging`, ...).

## Navigating these docs

- **Project** tab (left): models grouped by folder, the way the code is laid out.
- **Database** tab (left): the same models grouped by schema, as they exist in Postgres.
- Each model page shows its description, columns, tests, SQL and dependencies.
- The **lineage graph** (the button at the bottom right) shows how sources flow into
  staging and marts; click a node to focus on it.

{% enddocs %}
