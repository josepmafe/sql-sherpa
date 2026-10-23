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
- **Marts** (`marts`): tables for consumers, such as `dim_date`.

On the `dev` target every schema gets a `dev_` prefix (`dev_staging`, ...).

## Navigating these docs

- **Project** tab (left): models grouped by folder, the way the code is laid out.
- **Database** tab (left): the same models grouped by schema, as they exist in Postgres.
- Each model page shows its description, columns, tests, SQL and dependencies.
- The **lineage graph** (the button at the bottom right) shows how sources flow into
  staging and marts; click a node to focus on it.

{% enddocs %}
