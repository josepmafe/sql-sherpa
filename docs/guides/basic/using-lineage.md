# Using Lineage

Lineage draws the dbt project as a flowchart from sources to marts: one table's path, a whole domain or everything, with each node's SQL and the status it got in the last full build. It reads dbt's `manifest.json` and the build record in the database, and shows names and SQL, never data ([UI spec §6.5](../../superpowers/specs/2026-10-24-sql-sherpa-ui-design.md)). Start it as for the Explorer ([Using the Explorer](using-the-explorer.md#starting-it)); `make build-prod` writes the manifest the graph is drawn from and the build status it shows. Open <http://127.0.0.1:8090/lineage>.

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

Anything else (`tag:`, `,`, `@`, or a folder, package or test name such as `marts`) is refused under the box with the list of supported forms, and the last good graph stays; use the layer chips for a folder. **Copy dbt selector** copies the applied selector, ready for dbt (the `dbt` prefix is explained in [Running dbt directly](building-the-warehouse.md#running-dbt-directly)). `dbt ls` rewrites `manifest.json` in its target path, so give it a scratch one:

```bash
dbt ls --select "+fct_orders" --target prod --target-path target-scratch
dbt build --select "+fct_orders" --target prod --target-path target-prod
```

## Reading the graph

Nodes sit left to right by layer: sources and seeds; staging and snapshots; intermediate; marts. Each shows its name, a layer colour, its materialization and a status as an icon and a label (never colour alone).

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
- **Build status unavailable** (database down, missing grant, timeout): the graph still draws from the manifest, with a note.

Click a node (or focus it and press Enter) to emphasise it, highlight its ancestors and descendants among the nodes shown, and dim the rest. Esc, the panel's close button or a click on the background clears it.

## The node panel

Selecting a node opens a panel beside the canvas: description, layer, materialization, domain, contract, file path, the last full build (status, duration, failing tests), the tests, the columns, and the raw and compiled SQL. When the compiled file is missing, the raw SQL shows with a note.

- **Copy dbt selector** copies the selector that selects the node: its name, or `source:<source>.<table>` for a source.
- **Open in Explorer** (marts only) opens `/explorer?table=<name>`.
- In the Explorer, **Open in Lineage** (table menu and Inspector) opens `+<table>+`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "Could not load the lineage" or `catalog_unavailable` | The artifacts are missing: `make build-prod`, then Retry |
| Red text under the box: "unsupported selector" | Use a form from the table above; the message lists them |
| Empty graph | The name matched nothing, or it is a source table: write `source:<source>.<table>` |
| "No full build recorded: run make build-prod" | No full prod build is recorded: `make build-prod` |
| A node reads "not in the last build" | The last full build did not include it: `make build-prod` |
| "Build status unavailable" | `make build-prod` (it re-applies the grants); else see the `ui` log (`docker compose logs ui`) |
| The panel shows a note instead of compiled SQL | `make build-prod` writes `target-prod/compiled/`; then reselect the node |

## Next

- [Extending the UI](../advanced/extending-the-ui.md): add a selector atom or a node status.
- [Building the warehouse](building-the-warehouse.md): what `make build-prod` records.
