# Using Lineage

Lineage draws the dbt project as a flowchart from sources to marts: one table's path, a whole domain or everything, with each node's SQL, its columns and where each column comes from, and the status the node got in the last full build. It reads dbt's `manifest.json` and the build record in the database, and shows names and SQL, never data ([UI spec §6.5](../../superpowers/specs/2026-10-24-sql-sherpa-ui-design.md)). Start it as for the Explorer ([Using the Explorer](using-the-explorer.md#starting-it)); `make build-prod` writes the manifest the graph is drawn from and the build status it shows. Open <http://127.0.0.1:8090/lineage>.

## The three modes

The segmented control at the top writes a **dbt selector** into the box beside it; typing a selector and pressing Enter applies it.

| Mode | You pick | Writes | Shows |
|---|---|---|---|
| **Whole project** | nothing | an empty box | every node |
| **Table path** | a table, and upstream / downstream depth (`all` or 1 to 5) | `+fct_orders+`, `2+fct_orders+1` | that table and what it depends on or feeds |
| **Domain** | a `meta.domain` | `+config.meta.domain:sales` | every mart of the domain and all their ancestors |

Any other selector is a custom one, and a note says that no mode writes it. The URL holds the view (`/lineage?select=...&layers=...&failing=1&node=...`), so reload, back/forward and a shared link restore it.

### What the selector box accepts

A subset of dbt's syntax, evaluated on the manifest without running dbt:

| Form | Means |
|---|---|
| `fct_orders` | a model, seed, snapshot or exposure by name (not a source table) |
| `source:shop`, `source:shop.orders` | a source, or one of its tables |
| `exposure:<name>` | an exposure |
| `config.meta.domain:sales` | every node of a domain |
| `+x`, `x+`, `2+x`, `x+1`, `2+x+1` | add ancestors, descendants, or both, up to a depth |
| `a b` | the union of the atoms, separated by spaces |

Anything else (`tag:`, `,`, `@`, or a folder, package or test name such as `marts`) is refused under the box with the list of supported forms, and the last good graph stays; use the layer chips for a folder. **Copy dbt selector** copies the applied selector on one line, ready for dbt; it is disabled for the whole project and for a refused selector. The `dbt` prefix is explained in [Running dbt directly](building-the-warehouse.md#running-dbt-directly). `dbt ls` rewrites `manifest.json` in its target path, so give it a scratch one:

```bash
dbt ls --select "+fct_orders" --target prod --target-path target-scratch
dbt build --select "+fct_orders" --target prod --target-path target-prod
```

## Reading the graph

Nodes sit in vertical **bands**, left to right: the raw source schemas, seeds, staging, snapshots, intermediate, marts. A node that reads a node of its own band sits in a sub-column to its right, so every edge runs left to right (the marts band is usually the widest). Each node shows its name, a layer colour, its materialization and a status as an icon and a label (never colour alone).

| Status | Meaning |
|---|---|
| **failed** (red) | The node's own row is `error`, `runtime error` or `partial success` in the last full build, or one of its tests failed (a unit test counts against its model) |
| **warned** (orange) | Not failed, and a test with severity `warn` warned. A warning is not "failing" |
| **skipped** (grey) | dbt skipped it because an upstream node failed |
| **ok** (green) | It ran and its tests passed |
| **not in the last build** (grey) | A model, seed or snapshot with no result in that build |
| **inlined** (grey) | An ephemeral model: it never runs |
| *(none)* | Sources and exposures have no status |

The "last full build" is the newest `dbt build` on prod recorded in `ops.dbt_run_results`; a build of a single model does not count. Under the selector box:

- **Layer chips** hide layers. A hidden layer's nodes and edges disappear; edges are not bridged across the gap.
- **Failing only** keeps the nodes whose status is failed. Warnings stay out.
- **No build recorded:** no node has a status and a note says "run make build-prod". "Failing only" then shows nothing.
- **Build status unavailable** (the build record could not be read: database down, missing grant, timeout): the graph still draws from the manifest, with a note.
- With a build recorded, the note says which one the statuses come from ("Status from the full build of 2026-10-26 14:05 UTC").

Click a node (or focus it and press Enter) to emphasise it, draw its ancestors and descendants among the nodes shown (nodes and edges) in the accent colour, and fade the rest. Esc, the panel's close button or a click on the background clears it, and the focus goes back to the node. Tab walks the nodes band by band, top to bottom; the zoom buttons at the bottom left do what the wheel does. The graph is framed whole when it loads and after every selector change, never zoomed past 100%.

## Columns

Every node has a chevron that expands it into its columns; **Expand all** and **Collapse all** (beside the layer chips) act on the nodes shown. Each row shows the column name, its **kind** and its **resolution**, each as an icon and a word. Columns come from `catalog.json` (the YAML columns when the catalog lacks a node, or when there is no catalog, with a note), so `make build-prod` is what fills them.

| Kind | Means |
|---|---|
| `passthrough` | one input column, unchanged or only cast, same name |
| `rename` | the same under another name |
| `transformed` | any other expression of one or more columns |
| `row count` | a pure `count(*)`, which reads no column; it names the relation it counts ("counts fct_orders") |
| `constant` | reads no column: a literal, or values the SQL makes itself such as the date spine (`dim_date`'s columns still vary row by row) |
| `generated` | a column dbt adds to a snapshot (`dbt_valid_from`, `dbt_valid_to`, `dbt_scd_id`, `dbt_updated_at`; with the timestamp strategy `dbt_valid_from` copies `updated_at` and is not linked) |
| `source` | a source or seed column, where every chain starts |

| Resolution | Means |
|---|---|
| `full` | every input was found |
| `partial` | some inputs were found and linked and the notes name the rest: the trace is incomplete |
| `unresolved` | none was found; a note says why |

Notes that explain a gap: "reads an unknown relation <name>", "column <c> not found in <node>", "* is not expanded: ...", "not in the compiled SQL's output", "the compiled SQL did not parse", "the compiled SQL is not in the dbt target". "cast to numeric(12,2)" and "also reads its own previous rows (incremental)" are information, not failures. A note about the node as a whole (no catalog, no compiled SQL) shows above the panel's Columns table.

**Select list only.** A column edge exists when the child's select expression reads the parent column, as in dbt Catalog. Not linked: a column used only in a `join`, `where` or `group by`, the partition or order of a *named* window (`WINDOW w AS (...)`), and a `count(*)` inside a larger expression (only a pure `count(*)` is a `row count`). A fixed legend on the canvas says so. Examples on the real project, read the compiled SQL in the panel for the rest:

- `stg_shop__customers.is_test_account` has no downstream column, though `dim_customers`, `fct_orders`, `fct_sessions` and `dim_promotions` all drop test accounts: it is a filter.
- `fct_orders.is_cancelled` reaches `fct_order_items` but nothing in `dim_customers`, though `order_count`, the `lifetime_*` columns and `customer_segment` count only non-cancelled orders.
- `fct_orders.has_discount` links `stg_shop__order_discounts.order_id` but not the `amount > 0` that defines it.
- `dim_customers.avg_order_value` traces only to `fct_orders.net_revenue`: the denominator is a `count(*)` of orders, which reads no column.

### Focusing a column

Click a column row (or a column in the panel's Columns tab). The canvas then:

- expands every node that holds an upstream or downstream column and shows in it **only the related columns** plus "+k more";
- draws the column edges row to row in the accent colour (table edges fade) and dims everything else;
- opens the Columns tab of the panel with the column marked.

"+k more" and that node's chevron show or hide the node's other columns **for this focus only** ("+k more" stays on screen as a toggle, "Hide the other k columns"). A mouse click does not move the canvas; keyboard focus scrolls the row into view. Esc (then Esc again for the node), a click on the background, selecting another node or focusing another column ends the focus, and your own expansions are back. Nodes the focus does not reach keep your expansion. A related column in a node that is not shown (another selector, a hidden layer) is counted in a note under the panel's header with a link to `+<node>+`, which always holds it. `column=` is in the URL; a column the node does not have is dropped from it.

## The node panel

The panel sits below the graph, always, and scrolls on its own, so selecting a node never resizes the graph. Until you select a node it says "Select a node to see its SQL, columns and tests". Tabs:

- **Overview:** description, layer, materialization, domain, contract, file path, the last full build (status, duration, failing tests) and the actions.
- **Columns:** each column with its kind and resolution; click one to focus it.
- **Tests** and **SQL:** raw and compiled SQL; when the compiled file is missing, the raw SQL shows with a note.

Actions: **Copy dbt selector** copies the selector that selects the node (its name, or `source:<source>.<table>`); **Open in Explorer** (marts only) opens `/explorer?table=<name>`. In the Explorer, **Open in Lineage** (table menu and Inspector) opens `+<table>+`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Could not load the lineage" or `catalog_unavailable` | The artifacts are missing: `make build-prod`, then Retry |
| Red text under the box: "unsupported selector" | Use a form from the table above; the message lists them |
| Empty graph | The text says why. "No node matches this selector": the name matched nothing, or it is a source table (write `source:<source>.<table>`). "No node in the layers shown": tick a layer chip. "Nothing failed in the last full build": untick Failing only |
| "No full build recorded: run make build-prod" | No full prod build is recorded: `make build-prod` |
| A node reads "not in the last build" | The last full build did not include it: `make build-prod` |
| "Build status unavailable (could not read the build record; see the ui log)" | `make build-prod` (it re-applies the grants); else see the `ui` log (`docker compose logs ui`) |
| The chevron is disabled (its tooltip says "Column lineage is unavailable" and why) | `manifest.json` or `catalog.json` cannot be read or parsed, or the first build of the lineage failed: `make build-prod`, then Retry (a missing `catalog.json` only adds a node note) |
| A node's columns are all `unresolved` | Its note (panel, above the Columns table) says why: usually its compiled SQL is not in `target-prod/compiled/` (`make build-prod`) or did not parse |
| A column you expect to be linked is not | Column lineage is select-list only; a join, filter, group by, named-window or `count(*)`-in-an-expression use is not an edge. Check the compiled SQL |
| The panel shows a note instead of compiled SQL | `make build-prod` writes `target-prod/compiled/`; then reselect the node |

## Next

- [Extending the UI](../advanced/extending-the-ui.md): add a selector atom, a node status or a column kind.
- [Building the warehouse](building-the-warehouse.md): what `make build-prod` records.
