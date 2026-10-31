# ui-3 — Column Lineage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lineage at column level, like dbt Catalog: a node expands into its columns, and focusing a column highlights every column it comes from and feeds across models. The same milestone gives the graph a schema-band layout (raw, seeds, staging, snapshots, intermediate, marts) and moves the node panel below the graph, so selecting a node never resizes it.

**Architecture:**
- **Backend (`sherpa_ui`):** a pure `ColumnLineageBuilder` runs `sqlglot.lineage` over each model's and snapshot's compiled SQL, with the column types from `catalog.json`, and labels every column with a kind and a resolution. An I/O loader builds it once per artifacts change. The graph endpoint gains `columns=true`, and a new endpoint returns a column's focus: its upstream and downstream columns and the edges between them.
- **Frontend:**
  - The layout splits in two. ELK only orders the nodes, once per topology change; a pure, synchronous `stackBands` places them on the band grid with each node's current height.
  - Nodes expand into column rows with one React Flow handle per column, so column edges join row to row.
  - The node panel becomes a full-width, always-present tab set under the canvas.

**Tech Stack:** nothing new. `sqlglot` 30.24.0 (already pinned and in the `ui` image's `mcp` group), FastAPI, React Flow 12 and `elkjs` (ui-2), Mantine, TanStack Query, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-24-sql-sherpa-ui-design.md`: §4 (the lineage endpoints), §6.5 (Lineage: layout, column lineage, node panel, URL), §8 and the ui-3 row of §10. This plan's commit writes the decisions into the spec and renumbers the UI milestones (Home ui-4, Ask ui-5, demo ui-6). **`CLAUDE.md` is binding.**

**Base:** `main` with ui-2 (#14), and from Task 4 on the UI test audit (the `ui-test-audit` plan: typed API builders in `ui/frontend/tests/fixtures/api.ts`, `renderWithProviders`, explicit Vitest timeouts, and the ELK entry point mocked outside layout tests). Rebase onto it before Task 4; its ELK mock then targets `orderNodes`, which replaced `layoutGraph` in Task 3. The column-lineage research prototype (`%TEMP%/collin/collin.py` on the owner's machine) proved the approach on this project:
- 0 parse errors over 41 nodes
- all 377 columns traced
- about 2 s for the whole project
- the same 442 edges as dbt-colibri

Use it for reference, not as code to copy.

**Model selection:** every task names its implementer and reviewer models. Dispatch exactly those. Fix rounds 4–5 go one tier up (`haiku` → `sonnet` → `opus`). The final whole-branch review is Task 7's panel (Step 2).

## Global Constraints

- **Incremental commits (CLAUDE.md):** each task adds only what it uses. Every commit leaves `make lint`, `make test` and `cd ui/frontend && npm test` green.
- **A worktree has none of the gitignored files.** Before Task 1, copy `.env` from the main checkout, then run `uv sync`, `cd ui/frontend && npm ci` and `make build-prod`. The last writes this checkout's `target-prod/` (its `compiled/` and `catalog.json` feed column lineage) and rebuilds the shared `sherpa` prod schemas.
- **sqlglot stays at 30.24.0.** Its parser is the SQL guard's view of a query and now also column lineage's. Use only the public `sqlglot.lineage.lineage`, `sqlglot.parse_one`, `sqlglot.exp` and `sqlglot.schema.MappingSchema`, never its internals.
- **Pure vs I/O (CLAUDE.md):** `lineage/column_lineage.py` is pure (strings and mappings in, a `ColumnLineage` out). `lineage/column_lineage_loader.py` does the file reads and the caching.
- **Names and SQL only, never data.** Column lineage reads only artifacts and runs no SQL against the warehouse.
- **Identifiers:** Postgres folds unquoted names to lower case, and dbt quotes its relation names. Compare relations and columns as Postgres would: case-folded unless quoted. The catalog's names are the reference.
- **Kind and resolution names** are the spec's exactly: `passthrough`, `rename`, `transformed`, `row_count`, `constant`, `generated`, `source`; `full`, `partial`, `unresolved`. They are `StrEnum`s in Python and the generated types in TypeScript.
- **Accessibility:** a column row is a button. Kind and resolution are icon plus text, never colour alone. Dimmed nodes and rows stay focusable.

## Review Focus

1. **Artifacts out of step:** `dbt ls` or `dbt parse` with the prod target rewrites `manifest.json` but leaves `compiled/` and `catalog.json` from the last build. A column the manifest lists may then be missing from the compiled SQL, and a relation may have no node. Both become notes on the column, never an error or a 500. Tests: `TestColumnLineageBuilder::test_a_column_missing_from_the_compiled_output_is_noted`, `test_an_unknown_relation_is_noted` (Task 1).
2. **Two requests during the first build:** the second must wait for the same build, not start another. A failed rebuild keeps the last good lineage, like the manifest loader. Tests: `TestColumnLineageLoader::test_concurrent_first_calls_build_once` and `test_keeps_the_last_lineage_when_a_rebuild_fails` (Task 2).
3. **Hostile or broken SQL:** a compiled file that doesn't parse, nests deeply enough to hit Python's recursion limit, or is empty. The node's columns are all `unresolved` with a node-level note, and every other node is unaffected. Test: `test_unparseable_sql_marks_only_its_node_unresolved` (parametrized over a syntax error, a `RecursionError` and an empty file) (Task 1).
4. **A focus that reaches outside the view:** a related column sits in a node that the selector or a hidden layer leaves out. It is counted in a note with a link to `+<node>+`, and no edge is drawn to a node that isn't shown. Test: Vitest `column focus counts related columns outside the view` (Task 5).
5. **Expanding a node while ELK is still ordering a new topology:** the stacking must use the newest order and the newest heights, and a late ELK result must not undo either. Test: Vitest `useGraphLayout restacks on expansion without re-running ELK and ignores a superseded order` (Task 3).

---

## File Structure

```
ui/backend/src/sherpa_ui/lineage/
  manifest_graph.py         # GraphNode gains `relation`
  column_lineage.py         # pure: ColumnLineageBuilder, ColumnLineage, kinds and resolution
  column_lineage_loader.py  # I/O: catalog + compiled SQL, cache, single flight
  lineage_service.py        # columns=true, node columns, column focus
ui/backend/src/sherpa_ui/api/lineage.py   # GET /api/lineage/columns/{unique_id}/{column}
ui/backend/tests/lineage/   # test_column_lineage.py, test_column_lineage_loader.py, warehouse traces
ui/frontend/src/screens/lineage/
  layout.ts                 # ELK order + stackBands (bands, marts sub-columns)
  useGraphLayout.ts         # order by topology, stack by heights
  BandLabels.tsx            # the band headers
  GraphNode.tsx             # expandable, column rows with handles
  ColumnRow.tsx
  columnFocus.ts            # pure: what to expand, show and dim for a focus
  NodePanel.tsx             # below the graph, tabs
  LineageScreen.tsx, LineageCanvas.tsx, url.ts
ui/frontend/src/api/queries.ts            # useLineage(columns), useColumnFocus
ui/frontend/tests/lineage/, ui/frontend/e2e/lineage.spec.ts
```

---

### Task 1: The column lineage builder (pure)

**Model:** implementer `sonnet` · reviewer **`opus`** (sqlglot's lineage tree, identifier folding and the classification rules are where it goes subtly wrong)

**Files:** `lineage/column_lineage.py`; `lineage/manifest_graph.py` (`relation`); tests in `tests/lineage/test_column_lineage.py`.

**Interfaces — Produces:**
- **`GraphNode.relation: Relation | None`**: `Relation(database, schema, identifier)`, frozen, from the manifest's `database`, `schema` and `alias` (sources: `database`, `schema`, `identifier`). It is `None` for an ephemeral model, which has no relation. The relation is how a table in compiled SQL maps back to a node.
- **`ColumnKind`** (`StrEnum`): `PASSTHROUGH`, `RENAME`, `TRANSFORMED`, `ROW_COUNT`, `CONSTANT`, `GENERATED`, `SOURCE`. **`Resolution`**: `FULL`, `PARTIAL`, `UNRESOLVED`.
- **`ColumnRef(unique_id, column)`** and **`ColumnEdge(parent: ColumnRef, child: ColumnRef)`**, frozen. The edge shape is dbt's own `column_lineage` table, so the source could be swapped later.
- **`ColumnInfo`** (frozen): `ref, kind, resolution, notes: tuple[str, ...], counts: str | None`. `counts` is the `unique_id` a `row_count` column counts.
- **`ColumnLineage`:**
  - `columns_of(unique_id) -> tuple[ColumnInfo, ...]` (catalog order)
  - `info(ref) -> ColumnInfo | None`
  - `node_note(unique_id) -> str | None`
  - `focus(ref) -> ColumnFocus`: a breadth-first walk both ways over the edges; `ColumnFocus(focus, upstream: frozenset[ColumnRef], downstream: frozenset[ColumnRef], edges: tuple[ColumnEdge, ...])`, where the edges are every edge between two columns of the closure
- **`ColumnLineageBuilder(graph: ManifestGraph, columns_by_node: Mapping[str, Sequence[str]])`** with `build(compiled_sql: Mapping[str, str | None]) -> ColumnLineage`.
  - **Inputs:** `columns_by_node` holds each node's catalog columns in order (the loader falls back to the YAML columns). `compiled_sql` holds each model's and snapshot's compiled SQL (`None` when missing).
  - **Algorithm:**
    1. Build a `MappingSchema` (Postgres dialect) from the relations and their columns.
    2. Each ephemeral model gets a pseudo-relation `"__ephemeral__"."__ephemeral__"."<name>"`.
    3. Walk the models and snapshots in topological order. For each:
       1. Parse the SQL.
       2. Cut every `__dbt__cte__<name>` CTE out of the `WITH`, and repoint its references at the ephemeral pseudo-relation, keeping the reference's alias. That stops each node at its direct parents.
       3. Call `lineage(None, tree, schema=…, dialect="postgres")` for all output columns at once.
       4. Walk each column's lineage tree to its `exp.Table` leaves, and map each leaf (relation, column) to a `ColumnRef`.
    4. An ephemeral model's columns, absent from the catalog, come from its own output and are added to the schema before its children, which is why the walk is topological.
  - **Kind rules,** first match wins:
    - a source or seed column → `SOURCE`
    - a snapshot's `dbt_scd_id`, `dbt_updated_at`, `dbt_valid_from`, `dbt_valid_to` (and `dbt_is_deleted`) → `GENERATED`
    - no input column, and the expression contains `count(*)` → `ROW_COUNT`, with `counts` set to the relation in that select's `FROM`
    - no input column → `CONSTANT`
    - one input, and the expression is the bare column or a `CAST`/`::` of it → `PASSTHROUGH` with the same name, `RENAME` otherwise; a cast adds the note "cast to <type>"
    - anything else → `TRANSFORMED`
  - **Resolution:**
    - `FULL` unless the column has a problem. A self-read (an incremental model's `{{ this }}`) is not a problem: it adds the note "also reads its own previous rows (incremental)" and no edge.
    - `PARTIAL` when some inputs resolved and others didn't: an unknown relation, a column the parent doesn't have, an unexpanded `*`.
    - `UNRESOLVED` when none did, or for every column of a node whose SQL is missing ("the compiled SQL is not in the dbt target"), doesn't parse ("the compiled SQL did not parse"), or raises `RecursionError`; the node gets a `node_note`.
    - A catalog column missing from the SQL's output is `UNRESOLVED`, with "not in the compiled SQL's output".

- [ ] **Step 1: Tests first** (hand-written mini manifests and SQL; no files, no database):
  - `TestManifestGraph::test_relation_from_database_schema_and_alias` and `test_ephemeral_model_has_no_relation`.
  - **`TestColumnLineageBuilder`:**
    - one test per kind rule, named for it (e.g. `test_a_cast_of_one_column_is_a_passthrough_with_a_note`, `test_count_star_is_a_row_count_of_its_from_relation`)
    - `test_an_ephemeral_parent_is_its_own_node` (the CTE is cut and the edge points at the ephemeral model)
    - `test_star_expands_from_the_schema`
    - `test_quoted_and_unquoted_identifiers_meet`
    - `test_an_incremental_self_read_is_a_note_not_a_failure`
    - `test_an_unknown_relation_is_noted`
    - `test_a_column_missing_from_the_compiled_output_is_noted`
    - `test_unparseable_sql_marks_only_its_node_unresolved` (parametrized: syntax error, `RecursionError` via a monkeypatched parse, empty string)
    - `test_missing_sql_marks_the_node_unresolved_with_its_note`
  - **`TestColumnLineage`:**
    - `test_focus_walks_both_ways`
    - `test_focus_edges_are_those_inside_the_closure`
    - `test_focus_of_a_source_column_is_only_downstream`
- [ ] **Step 2:** Run (FAIL) → implement → run (PASS) → `make lint`. Commit `feat: column lineage builder (sqlglot over compiled SQL)`.

---

### Task 2: Loader, service and API

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `lineage/column_lineage_loader.py`, `lineage/lineage_service.py`, `api/lineage.py`, `api/errors.py` (the `column_not_found` description), wiring in `app.py`, `ui/frontend/src/api/schema.d.ts` (`make ui-client`), tests.

**Interfaces — Produces:**
- **`ColumnLineageLoader(target_path: Path, manifests: ManifestLoader, node_sql: NodeSql)`:** `load() -> ColumnLineage`, blocking.
  - It reads `catalog.json` (each node's and source's columns, in `index` order), takes the graph from `manifests.load()` and the compiled SQL from `node_sql.compiled(node)` for every model and snapshot, then builds.
  - **Cache:** keyed by (manifest mtime, catalog mtime). A threading lock makes concurrent first calls wait for one build. A failed rebuild keeps the last lineage with a warning; only a loader that never built one raises `CatalogUnavailable`.
  - A missing `catalog.json` falls back to each node's YAML columns, and the node notes say so.
- **`LineageService`:**
  - `graph(..., columns: bool)`: with `columns`, each `LineageNode` carries `columns: list[ColumnView(name, kind, resolution)]` and the graph `column_lineage: Literal["ok", "unavailable"]` (`unavailable` with a reason when the loader raises).
  - `node(...)`'s `columns` become `ColumnDetail(name, data_type, description, kind, resolution, notes, counts)`.
  - `column_focus(unique_id, column) -> ColumnFocusView | None`: the focus as `{focus, columns: [ColumnView + node, direction: "focus" | "upstream" | "downstream", notes], edges: [{parent: {node, column}, child: {node, column}}]}`. `None` for an unknown node or column.
- **Routes:** `GET /api/lineage` gains `columns: bool = False`. `GET /api/lineage/columns/{unique_id}/{column}` → `ColumnFocusView`, or `column_not_found` (404).

- [ ] **Step 1: Tests first.**
  - **`TestColumnLineageLoader`** (temp files; a fake graph and SQL reader):
    - `test_builds_once_per_artifacts_change`
    - `test_concurrent_first_calls_build_once` (two threads, a builder slowed by an event)
    - `test_keeps_the_last_lineage_when_a_rebuild_fails`
    - `test_falls_back_to_yaml_columns_without_a_catalog`
  - **`TestLineageService`:** `test_graph_with_columns_carries_kinds`, `test_column_lineage_unavailable_keeps_the_graph`, `test_column_focus_unknown_column_is_none`.
  - **`TestLineageApi`:** `test_unknown_column_is_404`, `test_columns_flag_adds_columns`.
  - **`warehouse`** (the real prod artifacts):
    - `test_every_model_column_has_a_kind`
    - `test_traces_match_the_hand_checked_sources`, parametrized over the research traces and pinned exactly. Check each source set by hand against the SQL before pinning it; where it differs from below, the SQL wins and the test says why.
      - `fct_orders.net_revenue` → `source shop.orders.status`, `shop.order_items.{id, order_id, quantity, unit_price}`, `shop.order_discounts.amount`, `shop.refunds.{amount, reason}`
      - `fct_orders.marketing_channel` → `source events.events.context`, `shop.orders.sales_channel`, seed `channel_mapping.marketing_channel`
      - `fct_order_items.cogs`, upstream of which is `snap_product_variants.cost_price`
      - `stg_events__events.occurred_at` (a passthrough of the source column, with a cast note)
- [ ] **Step 2:** Run (FAIL) → implement → run (PASS) → `make ui-client` (commit `schema.d.ts`) → `make lint`. Commit `feat: column lineage API (columns on the graph and column focus)`.

---

### Task 3: Schema-band layout

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `layout.ts`, `useGraphLayout.ts`, `BandLabels.tsx`, wiring in `LineageCanvas.tsx`, tests.

**Interfaces — Produces:**
- **`BAND`:** `source` 0, `seed` 1, `staging` 2, `snapshot` 3, `intermediate` 4 (`other` sits there too), `marts` 5, `exposure` 6. A band missing from the graph takes no space.
- **`orderNodes(nodes, edges) -> Promise<Map<string, number>>`:** ELK `layered` with partitioning by band (as ui-2). Its only output is each node's rank within its band, read from ELK's `y`. Sources rank by (source name, ELK's `y`), so a source system's tables stay together.
- **`BandNode`:** `{id, layer, sourceName: string | undefined}` (the API node's `layer` and, for a source, its source name).
- **`stackBands(nodes: BandNode[], edges: LayoutEdge[], order: Map<string, number>, heights: Map<string, number>) -> Map<string, Point>`** (pure, synchronous):
  - **`x`:** the band's start plus a sub-column offset, so edges run left to right wherever possible. In every band, a node's sub-column is the length of its longest chain of parents from the same band, among `edges` (the graph shown). `dim_customers` (reads `fct_orders`) sits right of `fct_orders`, and an intermediate model that reads another intermediate model sits right of it. A band whose nodes don't read each other stays one column.
  - **`y`:** the nodes are stacked in rank order with `ROW_GAP`, each taking its own height.
  - A band's width is `NODE_WIDTH` times its sub-columns, plus gaps.
- **`useGraphLayout(nodes, edges, heights)`:** runs `orderNodes` on a topology change only, discarding a superseded run (ui-2's guard). It re-runs `stackBands` whenever the order or the heights change, without ELK.
- **`BandLabels`:** a header above each band shown, reading "Raw", "Seeds", "Staging", "Snapshots", "Intermediate", "Marts", "Exposures". It's a non-interactive React Flow node: not focusable, not counted as a graph node.

- [ ] **Step 1: Vitest first.**
  - `stackBands`:
    - `places bands left to right in the spec's order`
    - `skips a band the graph does not have`
    - `a mart that reads a mart sits one sub-column to the right`
    - `an intermediate model that reads another sits one sub-column to the right`
    - `a band without reads inside it stays one column`
    - `every edge between two bands runs left to right`
    - `stacks nodes of different heights without overlap`
    - `a node without edges stays in its band`
  - `orderNodes`: `keeps a source system's tables together`.
  - `useGraphLayout`: `restacks on expansion without re-running ELK and ignores a superseded order` (Review Focus 5).
  - `BandLabels`: `labels only the bands shown`.
- [ ] **Step 2:** Run (FAIL) → implement → run (PASS). In the browser (`make ui-dev`), the whole project reads raw → seeds → staging → snapshots → intermediate → marts. `make lint`. Commit `feat: Lineage schema-band layout`.

---

### Task 4: The node panel below the graph

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `LineageScreen.tsx`, `NodePanel.tsx`, `reveal.ts` (the panel no longer narrows the canvas), tests.

**Behaviour** (UI spec §6.5):
- **Size:** the canvas has a fixed height (`60vh`, at least 400 px) and the full width. The panel sits under it, always rendered, full width, scrolling on its own (at most `50vh`).
- **Empty state:** with no node selected, the panel shows "Select a node to see its SQL, columns and tests".
- **Tabs:**
  - **Overview:** description, layer, materialization, domain, contract, file path, the last full build, and the actions "Open in Explorer" and "Copy dbt selector".
  - **Columns:** name, type, kind, resolution and notes. A click focuses the column (Task 5).
  - **Tests:** as in ui-2.
  - **SQL:** raw and compiled, as in ui-2.
- **State:** the tab persists while switching nodes. Closing the selection (Esc, the panel's clear button, a background click) keeps the panel with its empty state.
- **Cleanup:** remove the "the panel narrows the canvas" reveal trigger. The keyboard-focus reveal stays.
- **Minimap:** it no longer hides while a node is selected (it did, to give the panel beside the graph room; `LineageCanvas.tsx` still has `selected === undefined` in its condition). It still appears only above `MINIMAP_ABOVE_NODES`.

- [ ] **Step 1: Vitest first.**
  - `selecting a node does not change the canvas size` (the canvas container's style height and width are unchanged)
  - `the panel shows its empty state with no node selected`
  - `the tab persists across node selections`
  - `Esc clears the selection and keeps the panel`
  - `the Columns tab lists kind and resolution`
  - `focus returns to the node after a background click clears the selection` (a gap the UI test audit found)
  - `the minimap appears above the node threshold, whether or not a node is selected, and not below it` (audit mutant F27)

  Adapt the ui-2 panel tests to the tabs, and move the lineage tests that build payloads by hand (`LineageScreen`, `SelectorBar`, `NodePanel`) to the typed builders (`lineageNode`, `lineageGraph`, `nodeDetails`). Their mocks omit required fields today (`selector`, `materialization`, `status`, `failing_tests`).
- [ ] **Step 2:** Run (FAIL) → implement → run (PASS) → `make lint`. Commit `feat: Lineage node panel below the graph, always present`.

---

### Task 5: Expandable nodes and the column focus

**Model:** implementer `sonnet` · reviewer **`opus`** (the focus state, handles on variable-height nodes, and the out-of-view rules interact)

**Files:** `GraphNode.tsx`, `ColumnRow.tsx`, `columnFocus.ts`, `LineageCanvas.tsx`, `LineageScreen.tsx`, `url.ts`, `api/queries.ts`, tests.

**Interfaces — Produces:**
- **`url.ts`:** `LineageParams` gains `column: string | undefined`, meaningful only with `node`. Like `node`, a change replaces the history entry.
- **`useLineage(params, {columns})`:** requests `columns=true` once any node is expanded or a column is focused; the screen keeps asking for columns after that. **`useColumnFocus(node, column)`** → the focus endpoint.
- **`columnFocus.ts`** (pure): `focusView(focus: ColumnFocusView, shown: Set<string>) -> {expanded: Map<string, Set<string>>, edges: ColumnEdge[], outside: number}`.
  - `expanded`: each shown node with related columns, mapped to the set of columns to show
  - `edges`: the column edges whose two ends are both shown
  - `outside`: the related columns in nodes not shown
- **`GraphNode`:**
  - a chevron expands or collapses the node; expanded, it lists its columns as `ColumnRow`s
  - **`ColumnRow`:** a button with the column name, the kind (icon and text) and a resolution badge when it isn't `full`; a target handle on the left and a source handle on the right, with id `col:<column>`
  - the node's height is `NODE_HEIGHT + rows × COLUMN_ROW_HEIGHT`, and it reports its height for `stackBands`
  - while a column is focused, a related node shows only its related columns plus "+k more"
- **The focus:**
  - **Starting it:** a click on a column row, or on a column in the panel's Columns tab, sets `node` and `column`.
  - **Drawing it:** the canvas expands the related nodes (`focusView`), draws the column edges handle to handle in the primary colour, and dims every other node, row and table edge. The focused row is emphasised.
  - **Out of view:** the panel shows "k related columns are outside this view" with a link to the selector `+<node>+`.
  - **Legend:** "Column lineage follows the select list: columns used only in joins or filters are not linked" sits at the canvas corner while columns are shown.
  - **Esc:** clears `column` first, then `node`.
- **"Expand all" / "Collapse all"** in the layer-filter bar act on the nodes shown.
- **Column lineage `unavailable`:** the chevrons are disabled, with the reason as their description.

- [ ] **Step 1: Vitest first.**
  - **`focusView`:**
    - `expands each shown node with its related columns`
    - `draws only edges between shown nodes`
    - `column focus counts related columns outside the view` (Review Focus 4)
  - **`GraphNode`:**
    - `expanding lists the columns with kind and resolution`
    - `a focused column's node shows only related columns and +k more`
    - `a column row is a keyboard-reachable button`
  - **Canvas:**
    - `clicking a column focuses it and dims the unrelated nodes`
    - `Esc clears the column before the node`
    - `column= in the URL restores the focus after a reload`
    - `Expand all expands the nodes shown`
    - `unavailable column lineage disables expanding and says why`
  - **Limits the UI test audit found unasserted** (each must fail under its mutant):
    - `the initial fit never zooms past 1x` (F26)
    - `an empty node= is no selection` (`url.ts`, F08)
    - `useGraphLayout: a superseded order that fails leaves the newer positions and state alone` (F07b)
    - move the canvas and node tests to the typed builders as well
- [ ] **Step 2:** Run (FAIL) → implement → run (PASS). In the browser: focus `fct_orders.net_revenue` and check it by eye against Task 2's pinned trace. `make lint`. Commit `feat: expandable lineage nodes and column focus`.

---

### Task 6: End-to-end and acceptance

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `e2e/lineage.spec.ts`, and cases in `e2e/layout.spec.ts` and `e2e/offline.spec.ts`.

- [ ] **Step 1: Playwright** against the compose stack (`make up && make build-prod`):
  - **bands:** in the whole project, the x position of a source < a seed < `stg_shop__orders` < `snap_product_variants` < an intermediate model < `fct_orders`; `dim_customers` is right of `fct_orders`; and every drawn edge's target is right of its source
  - **panel below:** the canvas bounding box is the same before and after selecting `fct_orders`, and the panel's empty state shows before any selection
  - **column focus:** expanding `fct_orders` and clicking `net_revenue` highlights exactly the pinned upstream source columns (the warehouse test's list) and dims a node outside the chain; Esc twice clears both
  - **out of view:** under the selector `fct_orders` alone, focusing `net_revenue` shows the "outside this view" note, and its link opens `+fct_orders+`
  - **expectations from the warehouse,** not from the last build's luck: read the expected status of `fct_orders` and whether a build is recorded from `ops.dbt_run_results` through `adminScalar`, instead of asserting "ok" (the spec otherwise fails after any real warning or failing build)
  - **the Table-path depth controls** driven once end to end (the audit found them undriven)
- [ ] **Step 2:** `offline.spec.ts`: a column focus with every request outside the origin blocked, through the shared `blockForeignRequests` helper of the UI test audit. `layout.spec.ts`: at 1280 px, the bands and the panel don't overflow horizontally (the canvas pans; the page doesn't scroll sideways).
- [ ] **Step 3: Acceptance** (UI spec §10): `make e2e`. Commit `test: column lineage end-to-end flows`.

---

### Task 7: Documentation and renumbering

**Model:** implementer `sonnet` · reviewer `haiku`

- [ ] **Step 1:** Docs, within `CLAUDE.md`'s guide rule; run every command once while writing.
  - **`basic/using-lineage.md`:** the bands, the panel below, expanding nodes, focusing a column, the kinds and resolutions in one table, the select-list-only limit, and the symptom → fix rows (no columns: run `make build-prod`; a node all unresolved: its note says why).
  - **`advanced/extending-the-ui.md`:** adding or changing a kind rule (the builder, its tests, the warehouse trace list, the spec) and what a sqlglot bump now has to rerun.
  - **`CLAUDE.md`:**
    - the sqlglot pin note says a bump also changes column lineage (rerun the guard's hostile-input tests and the column-lineage tests)
    - the UI backend's pure/I-O line gains `lineage/column_lineage` (pure) and `lineage/column_lineage_loader` (I/O)
    - the plan status line
  - **`README.md`:** the status line.
  - **The renumbering left in code comments:**
    - `ui/frontend/src/app/routes.tsx` ("until Home arrives (ui-4)")
    - `ui/backend/src/sherpa_ui/health/health_service.py` ("join in ui-5"); then run `make ui-client`, because the docstring reaches the OpenAPI schema
    - `ui/backend/src/sherpa_ui/build/build_status.py` ("ui-4 reuses")

    `git grep -n -E "\bui-[3-6]\b"` must show only the new numbers.
  - Commit `docs: column lineage guide, CLAUDE.md, README and milestone renumbering`.
- [ ] **Step 2: Panel review** (the procedure is in `CLAUDE.md`). Every reviewer gets `git diff main...HEAD`, its focus, this plan and UI spec §4 and §6.5:
  - **Data engineer:** the lineage against dbt's semantics (ephemeral models, snapshots, incremental branches in compiled SQL, `*`, casts), the kind rules, the warehouse traces.
  - **Analytics domain expert:** whether the kinds and their wording help an analyst trace a metric, and whether the select-list-only limit is stated honestly where it matters (e.g. `customer_segment`).
  - **Frontend engineer:** the band layout (edges left to right, sub-columns) and its restacking, handles on variable-height nodes, focus state and Esc order, accessibility of rows and kinds, performance with everything expanded.
  - **Software engineer:** the builder's purity, the loader's cache and lock, the API and OpenAPI, the tests.
  - **Devil's advocate**, after the others.

  No security reviewer: the milestone adds no role, query, PII path or network surface; it parses our own artifacts.
- [ ] **Step 3:** Triage, fix, re-verify (`make lint`, `make test`, `npm test`, `make e2e`). The triage table goes in the PR description.
