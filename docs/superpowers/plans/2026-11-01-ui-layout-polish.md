# UI Layout Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resizable, collapsible panes on the Explorer and Lineage screens, a collapsible navigation rail with hover-peek, a Lineage layout where no edge passes behind a node, and a richer node panel (disabled empty tabs, column types for every node, test definitions in a dialog), an Explorer whose tree columns line up, and a Lineage that keeps its zoom and pan across redraws and starts with the exposures hidden.

**Architecture:** one small pane toolkit (`src/components/panes/`, on `react-resizable-panels`) used by the shell, the Explorer and Lineage. Sizes persist in `localStorage` under `sherpa.v1.layout.*` and are clamped when read. The Lineage layout reserves a gap for each long edge in every column it crosses, places nodes and gaps with a pure least-squares fit that then closes the holes under tables nothing reads, and draws each edge as one monotone curve through its gaps. The canvas keeps the user's view for a subject (the selector) in memory. Column types and test definitions are read by the existing lineage service from `catalog.json` and the dbt artifacts; one new endpoint serves a test's content.

**Tech Stack:** React 19, TypeScript, Mantine 9, `react-resizable-panels` (4.x at planning time), `@xyflow/react`, `elkjs`, Vitest, Playwright; FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-10-24-sql-sherpa-ui-design.md`: §4 (the tests endpoint), §6.1 (rail and panes), §6.4 (Explorer), §6.5 (Lineage: modes, layout, node panel), §7 (layout storage), §8 (tests). **This plan's commit changes that spec.** The edits are the items above plus the Inspector below 1280 px (an overlay).

**Decisions (owner, 2026-11-01), all of the recommended options:**
- **Collapse (1):** the rail, the Explorer's tree and Inspector, and Lineage's node panel collapse. Hover-peek applies to the rail and the tree only.
- **Narrow screens (2):** below 1280 px the Inspector is a collapsed pane that opens as an overlay and takes no layout width.
- **Splitters (3):** also a horizontal handle between the editor and the results, and one between the graph and the node panel.
- **Sizes (4):** remembered in `localStorage`, double-click resets, stored values are clamped on read.
- **Layout (5):** measured and rendered before the plan. The owner chose B straightened (gaps for long edges, no edge behind a node), plus a time-boxed attempt at curves that bend inside the columns (Task 8).
- **Also:** `sherpa › marts` is the tree heading with the domains smaller and dimmed (2), modes in the order Whole project · Domain · Table path (5), column types for every node (7), empty tabs disabled with a reason (8), tests that open in a dialog with their definition (9).

**Not a numbered milestone:** like the audits it is named by date, so Home stays ui-4. Build it before Home, so Home is made in the final shell.

**`CLAUDE.md` is binding.**

**Base:** `main` at `c494a6b` (#21, data-platform-4). Its marts (`dim_campaigns`, `fct_ad_spend_daily`, the `fct_leads` pair) and the `sherpa_agent` exposure are in the graph every task and e2e sees, and its CI gates this branch's pull request.

**Model selection:** every task names its implementer and reviewer models. Dispatch exactly those. Fix rounds 4–5 go one tier up. The final whole-branch review is Task 10's panel.

## Global Constraints

- **Offline (UI spec §3):** the dependency is bundled by Vite and installed from the public registry (`make lint` checks the lockfile). No CDN, no remote font or icon.
- **Accessibility:** WCAG AA. The handles are `role="separator"` with an accessible name and the arrow keys, and have a focus ring and a hover colour at 3:1 against the background in both themes (1.4.11). Collapse buttons have an `aria-expanded` and a name that says what they fold. Nothing opens by hover alone: focus opens the same things.
- **Layouts that must hold:** 1024 × 700, 1280 × 800 and 1440 × 900 (`e2e/layout.spec.ts`): the editor keeps a usable width at 1024 px, and no screen scrolls horizontally.
- **Storage:** `sherpa.v1.layout.<id>`, JSON, read inside a `try` (blocked site data throws `SecurityError`, as in `worksheetStore.ts`). A bad value means the defaults. Nothing but sizes and collapsed flags is stored.
- **Test rules (`CLAUDE.md`):** payloads come from the typed builders in `tests/fixtures/api.ts`, with `renderWithProviders` / `renderRoutes`. No sleep proves that something did not happen: use fake timers. Assert what the user sees or what goes over the wire, not a render count or a CSS value. Vitest restores spies, and ELK is mocked outside layout tests.
- **Backend:** the production code follows `CLAUDE.md`. New behaviour is a pure function or a method of the class that owns it, and the API gets a Pydantic model. After any API change, `make ui-client` and commit `schema.d.ts`.
- **Shared warehouse:** tests only read. `make e2e` needs `make up` from this worktree (it replaces the running `ui` and `mcp` containers; `make up` from the main checkout puts them back) and `make build-prod`. If `make build-prod` stops at the freshness gate, ask the owner before `make advance`.
- **Running one Playwright spec:** `cd ui/frontend && SHERPA_UI_PORT=<port> SHERPA_UI_USER=<user> npx playwright test <spec>`, with the values `make e2e` passes (`.env` or the defaults 8090 and `sherpa_ui`). The whole `make e2e` runs at the end of Task 10.
- **CI** (`.github/workflows/ci.yml`) runs `make lint`, `make test` and `npm test` on the pull request, on a fresh checkout with no `target-prod`. A default-suite test that needs the built warehouse is marked `warehouse`, and the Vitest layout fixture is committed.
- **Every commit** leaves `make lint`, `make test` and `cd ui/frontend && npm test` green. Commit as `Josep Martinez <57620224+josepmafe@users.noreply.github.com>`.

## Review Focus

1. **Hover-peek flickers or traps.** Crossing from the strip onto the opened overlay must not close it, a stray pass over the strip must not open it, and keyboard focus must open the same overlay and keep it. Tests: `usePeek` on fake timers (Task 1) and the shell's focus case (Task 2).
2. **A stored size breaks a smaller window or bad storage breaks the page.** A 700 px tree stored on a big monitor, `{"x":`, a number as a string, or storage that throws. Expected: sizes clamped to the pane's limits, or the defaults. Tests: `layoutStorage` (Task 1) and one per screen (Tasks 2–4).
3. **Dragging the Lineage handle moves the graph or hides the selected node.** Expected: the pan and zoom stay as they were. Tests: Task 4's e2e (the viewport transform is unchanged after a drag and the selected node is still in view).
4. **A disabled tab is the open one.** On Tests, the user clicks a node with no tests. Expected: Overview opens, never a blank panel. Test: Task 5.
5. **The tests endpoint reads outside `compiled/` or fails on a long test name.** Expected: the path never leaves `compiled/`; a generic test whose name dbt shortened (a hash suffix) still resolves; an unknown id is `test_not_found`. Tests: Task 7 (crafted path; every test of the real prod manifest resolves, as a `warehouse` test).

---

## Environment (before Task 1)

The worktree has `node_modules`. Before Task 1:
1. Copy `.env` from the main checkout.
2. Run `UV_LINK_MODE=copy uv sync`.
3. Run `make build-prod`, so this worktree has its own `target-prod` with #21's nodes.
4. Run `make up` from this worktree before the first e2e. The running `ui` container serves another checkout's artifacts.

If `make build-prod` stops at the source-freshness gate, ask the owner whether to run `make advance`.

---

## File Structure

```
ui/frontend/src/
  components/panes/                 # new: PaneGroup.tsx, Pane.tsx, PaneHandle.tsx, CollapseToggle.tsx, usePeek.ts, layoutStorage.ts
  app/AppShell.tsx, navItems.ts     # collapsible rail, icons
  screens/explorer/ExplorerScreen.tsx, CatalogTree.tsx, WorksheetPanel.tsx
  screens/lineage/LineageScreen.tsx, SelectorBar.tsx, NodePanel.tsx, layout.ts, useGraphLayout.ts, LineageCanvas.tsx
  screens/lineage/edgeRoute.ts, RoutedEdge.tsx   # new: the monotone edge curve, the routed edge
  api/queries.ts, schema.d.ts       # useTestDetails; regenerated client
ui/frontend/tests/panes/, tests/lineage/layoutMetrics.ts, tests/fixtures/lineage_project.json
ui/frontend/e2e/layout.spec.ts, lineage.spec.ts, explorer.spec.ts
ui/backend/src/sherpa_ui/lineage/   # manifest_graph.py (test fields), node_sql.py, lineage_service.py, column_lineage_loader.py, api router
ui/backend/tests/lineage/
```

---

### Task 1: The pane toolkit

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `ui/frontend/package.json` and lockfile; `src/components/panes/*`; `tests/panes/*.test.tsx`.

**Interfaces — Produces** (`src/components/panes/`):
- **`PaneGroup({ id, direction, children })`:** a horizontal or vertical group whose sizes persist under `sherpa.v1.layout.<id>`.
- **`Pane({ id, defaultSize, minSize, maxSize, collapsedSize, children })`:** sizes in pixels. With a `collapsedSize` it can fold to a strip.
- **`PaneHandle({ label })`:** the separator. The label is its accessible name.
- **`CollapseToggle({ collapsed, onToggle, foldLabel, openLabel })`:** an icon button with `aria-expanded`.
- **`usePeek(collapsed: boolean): { peeking: boolean; peekProps: { onPointerEnter, onPointerLeave, onFocus, onBlur } }`:** true after the pointer has been on the strip for `PEEK_DELAY_MS = 200`, or immediately on focus; it stays while the pointer or focus is inside, and ends `PEEK_LEAVE_MS = 300` after both have left; never true when not collapsed.
- **`layoutStorage`:** `readLayout(id, limits)` and `writeLayout(id, value)`, where `limits` is each pane's `{ min, max }`.

- [ ] **Step 1: Check the library** (`npm install react-resizable-panels`; read its installed types and README). Before writing code, confirm it has:
  - pixel sizes with a minimum and maximum
  - collapsible panels with an imperative collapse and expand
  - a keyboard-operable separator with `aria-valuenow`
  - persistence through a storage object we pass

  If one is missing, stop and report to the controller: do not hand-roll. If it lacks Home/End on the separator, remove "Home and End" from spec §6.1 in this task's commit.
- [ ] **Step 2: Tests first** (`tests/panes/`):
  - **`layoutStorage`:**
    - absent → `undefined`
    - `{"x":` → `undefined` and a `console.warn` naming the key
    - `Storage.prototype.getItem` throwing → `undefined`
    - a stored 5000 with `max 480` → 480, and a stored 10 with `min 200` → 200
    - a write goes to `sherpa.v1.layout.<id>` and nothing else
  - **`usePeek`** (fake timers): not peeking at 199 ms of pointer-enter, peeking at 200; focus peeks at once; leaving closes at 300 ms, not before; re-entering inside the 300 ms keeps it; not collapsed → never; an unmounted component leaves no timer.
  - **`PaneHandle`:** it is a separator named by `label`; ArrowRight and ArrowLeft change `aria-valuenow` by one step and stay inside the limits; a double-click restores the default sizes.
  - **`CollapseToggle`:** `aria-expanded` follows `collapsed`, and its name follows `foldLabel`/`openLabel`; Enter and Space toggle.

  Run them. Expected: FAIL.
- [ ] **Step 3: Implement** the components and the storage wrapper. The handle's look: a 1 px line with a 6 px hit area, a hover colour and a focus ring from the Mantine theme tokens (both themes).
- [ ] **Step 4:** `npm test`, `npm run lint`, `npm run typecheck`, `make lint` (lockfile registry check). Commit `feat: pane toolkit (resizable, collapsible, peek, remembered sizes)`.

---

### Task 2: The collapsible navigation rail

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `src/app/AppShell.tsx`, `src/app/navItems.ts`, `tests/AppShell.test.tsx`, `e2e/layout.spec.ts`.

- [ ] **Step 1: Tests first** (`AppShell.test.tsx`):
  - every nav item has an icon (`@tabler/icons-react`, already a dependency) and its label; the toggle at the rail's foot collapses it to the icons; the links keep their accessible names (`getByRole("link", { name: "Lineage" })` still works).
  - the collapsed state is written to `sherpa.v1.layout.shell` and read back on the next render; corrupt storage means expanded.
  - collapsed, focusing a link opens the rail (peek) and Esc closes it; the pin button keeps it open until clicked again. The timing cases are Task 1's.
- [ ] **Step 2: Implement.**
  - `NAV_WIDTH = 140` expanded and `NAV_COLLAPSED_WIDTH = 56`.
  - `NAV_ITEMS` gains `icon`: `IconTable` for Explorer and `IconBinaryTree2` for Lineage (Home and Ask add theirs when they exist).
  - A peek is an overlay: it widens the navbar element only, so `AppShell.Main` keeps its offset.
- [ ] **Step 3: e2e** (`layout.spec.ts`): collapse the rail, reload, it is still collapsed; hover it and the editor's left edge (`boundingBox().x`) does not move while the labels appear. Run the `layout` Playwright spec.
- [ ] **Step 4:** `npm test`, lint. Commit `feat: collapsible navigation rail with peek`.

---

### Task 3: Explorer panes

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `src/screens/explorer/ExplorerScreen.tsx`, `CatalogTree.tsx`, `WorksheetPanel.tsx`; `tests/ExplorerScreen.test.tsx`, `tests/Explorer.test.tsx`; `e2e/explorer.spec.ts`, `e2e/layout.spec.ts`.

- [ ] **Step 1: Tests first.**
  - **Three panes (from 1280 px, `wideScreen`):** the tree (default 380, or 280 below 1280 px; min 200, max 480; collapsible to a 40 px strip with its icon), the centre (min 360), the Inspector (default 300, min 240, max 520, collapsible). Each handle is named: "Resize the tree", "Resize the Inspector". Keyboard arrows change `aria-valuenow`; the collapse buttons set `aria-expanded` and hide the content.
  - **Tree peek:** the collapsed tree strip opens over the editor on focus; clicking a table in it selects the table and the strip closes.
  - **Remembered:** a resized tree is restored on the next render from `sherpa.v1.layout.explorer`; a stored 5000 is clamped; corrupt JSON gives the defaults.
  - **Narrow (below 1280 px):** the Inspector takes no layout width; clicking a table opens it as an overlay and moves the focus to its heading ("Table details"); Esc and the toggle close it and return the focus to the table's row.
  - **Editor and results:** a vertical group with a handle named "Resize the editor and the results" (editor min 120, results min 160); after the handle moves, the last result is still shown and the query is not re-run (`api` called once).
  - **Heading:** the existing assertions on the tree's text stay; the heading change is typography only, with no test (checked by eye and in the panel).
- [ ] **Step 2: Implement.**
  - The breadcrumb `sherpa › marts` becomes the tree's heading (`size="sm"`, weight 600). The domain labels become `size="xs"`, dimmed.
  - The `wide` query decides layout versus overlay.
  - CodeMirror and the memoized result grid must survive resizing (`ResultGrid.test.tsx` stays green).
- [ ] **Step 3: e2e.** Drag the tree handle by 100 px: the tree is about 100 px wider and a reload keeps it. At 1024 × 700 the Inspector is an overlay and the editor is at least the width the existing layout spec expects. Update `layout.spec.ts` for the Inspector overlay.
- [ ] **Step 4:** `npm test`, lint, the `explorer` and `layout` Playwright specs. Commit `feat: Explorer panes (tree, editor and results, Inspector) with remembered sizes`.

---

### Task 4: Lineage panes and the mode order

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `src/screens/lineage/LineageScreen.tsx`, `SelectorBar.tsx`; `tests/lineage/LineageScreen.test.tsx`, `tests/lineage/canvasHarness.tsx`; `e2e/lineage.spec.ts`, `e2e/layout.spec.ts`.

- [ ] **Step 1: Tests first.**
  - **Mode order:** the segmented control lists Whole project, Domain, Table path, in that order; the default stays Whole project.
  - **Panes:** the graph and the node panel are a vertical group with a handle named "Resize the graph and the node panel". The panel (default 260 px, min 120, max 2000; folds to its 40 px title bar) has a collapse button with `aria-expanded`. Selecting a node opens a collapsed panel. The selection, `Esc` behaviour and "Select a node…" text are unchanged.
  - **Remembered / clamped:** as in Task 3, under `sherpa.v1.layout.lineage`.
- [ ] **Step 2: Implement.**
  - `MODE_LABELS` in the new order.
  - The screen fills the viewport below the controls (no page scroll at 1280 × 800); the canvas pane has a minimum of 240 px.
  - The canvas keeps its pan and zoom when its container resizes.
- [ ] **Step 3: e2e** (Review Focus 3): with a node selected, drag the handle 100 px up and down: `.react-flow__viewport`'s transform is unchanged and the selected node is still in view. Update the Lineage layout specs (1280 × 800, 1024 × 700) to the new structure, keeping their intents: the panel is below, and selecting a node does not resize the canvas.
- [ ] **Step 4:** `npm test`, lint, the `lineage` and `layout` Playwright specs. Commit `feat: Lineage graph and node panel as panes; modes in the order project, domain, path`.

---

### Task 5: Node panel tabs that say when they are empty

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `src/screens/lineage/NodePanel.tsx`; `tests/lineage/NodePanel.test.tsx`; `e2e/lineage.spec.ts`.

- [ ] **Step 1: Tests first** (builders `nodeDetails({...})`):
  - **Columns tab:** with no columns, disabled with "This node has no columns." With columns, named `Columns (n)`.
  - **Tests tab:** with no tests, disabled with "No test is defined on this node."; otherwise `Tests (n)`.
  - **SQL tab:** with `raw_sql` and `sql_note` both null, disabled with "Sources, seeds and exposures have no SQL."; a node with only a `sql_note` is enabled (a note is content).
  - **How it is disabled:** the tab has `aria-disabled="true"` and stays focusable. Hover and focus show the reason. Clicking or pressing Enter does not select it.
  - **Review Focus 4:** with Tests open, selecting a node whose `tests` is empty shows Overview.
- [ ] **Step 2: Implement** the disabled state on the Mantine tab (not the `disabled` attribute, which removes it from the focus order). `aria-describedby` points at a visually hidden copy of the reason. The effective tab is derived: if the chosen tab is disabled, Overview.
- [ ] **Step 3:** update the tab-name selectors in the tests and the e2e (`Tests (n)`). `npm test`, lint, the `lineage` Playwright spec. Commit `feat: node panel tabs are disabled, with a reason, when empty`.

---

### Task 6: Column types for every node

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `ui/backend/src/sherpa_ui/lineage/column_lineage_loader.py`, `column_lineage.py`, `lineage_service.py`; `ui/backend/tests/lineage/test_column_lineage_loader.py`, `test_lineage_service.py`, `test_column_lineage_warehouse.py`.

**Rule (spec §6.5):** a column's `data_type` is the one `catalog.json` records for its relation (the database's own name, like the Explorer's), else the YAML's `data_type`, else `None`. An ephemeral model is never in the catalog, so it has none unless its YAML says.

- [ ] **Step 1: Tests first.**
  - **Loader:** a catalog fixture with two relations gives each column's type; a column matches case-insensitively (`_find_column` already does).
  - **Service:** `test_column_type_comes_from_the_catalog_then_the_yaml`: a node in the catalog with a YAML `data_type` of `timestamptz` returns the catalog's `timestamp with time zone`; a node absent from the catalog returns its YAML type; an ephemeral node with neither returns `None`.
  - **Warehouse twin** (against the built prod artifacts, literals): `fct_orders.order_id` → `bigint`; the source column `raw_shop.orders.id` → `bigint`; every column of `int_orders__status_pivoted` (ephemeral) → `None`.

  Run them. Expected: FAIL.
- [ ] **Step 2: Implement.** The loader keeps the catalog's types (it already reads `catalog.json`); `ColumnLineage` answers `data_type(ref)`; `_column_detail` applies the rule.
- [ ] **Step 3:** `make test`, `make lint`; in the UI, a staging node's Columns tab shows types. Commit `feat: column types for every node, from the catalog`.

---

### Task 7: Tests that open in a dialog with their definition

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:**
- backend: `manifest_graph.py` (`NodeTest`), `node_sql.py`, `lineage_service.py` (`TestInfo`, new `TestDetails`, `LineageService.test_details`), the lineage router under `ui/backend/src/sherpa_ui/api/`
- frontend: `src/api/queries.ts` (`useTestDetails`), `NodePanel.tsx` (`TestList`, `TestRow`, `TestBody`), regenerated `schema.d.ts`
- tests: `ui/backend/tests/lineage/test_manifest_graph.py`, `test_node_sql.py`, `test_lineage_service.py`, `ui/backend/tests/api/`, `tests/lineage/NodePanel.test.tsx`, `tests/fixtures/api.ts`, `e2e/lineage.spec.ts`

**Interfaces — Produces:**
- `TestInfo` gains `unique_id: str`.
- `TestDetails` (frozen Pydantic): `unique_id`, `name`, `kind`, `severity`, `column`, `file_path`, `selector` (the test's name), `raw_sql: str | None`, `compiled_sql: str | None`, `sql_note: str | None`, `definition: str | None`.
- `GET /api/lineage/tests/{unique_id}` → `TestDetails`, or `test_not_found` (same error shape as `node_not_found`).
- `useTestDetails(uniqueId: string | undefined)`, enabled only when an id is given. The `tests/fixtures/api.ts` builder is `testDetails(overrides)`.

By kind:
- **Singular:** `raw_sql` is the manifest's `raw_code`, `compiled_sql` the file under `compiled/<package>/<original_file_path>`, no definition.
- **Generic:** `raw_sql` is null (it is only a macro call). `definition` is JSON text with the test's name and namespace, its arguments without `model` and `column_name`, and the `config` keys that are set (`severity`, `where`, `tags`). `compiled_sql` is `compiled/<package>/<original_file_path>/<path>`, using the manifest's `path`, which differs from `name` when dbt shortens a long test name with a hash.
- **Unit:** no SQL. `definition` is JSON text of `model`, `given`, `expect` and `overrides` as written in the YAML.

- [ ] **Step 1: Backend tests first.**
  - **`NodeTest`:** built for each kind from a synthetic manifest: file path with `/`, `path`, the definitions above, and a long generic test whose `path` differs from `name`.
  - **`NodeSql`:** a generic test's compiled file resolves by `path`; a crafted `path` of `../../x` returns `None` (Review Focus 5); a missing file gives a `sql_note`, as for models.
  - **Service and route:** the details of one test of each kind; `test_not_found` for an unknown id and for a node id; every test of `node(...)` carries its `unique_id`.
  - **Warehouse** (real prod artifacts): every test of the graph resolves: each singular test has `raw_sql` and `compiled_sql`, each generic test has `definition` and `compiled_sql`, each unit test has a `definition`. One sample per kind passes `dbt ls --select <selector>` (in-process, scratch target path, as `test_selector_oracle.py` does).

  Run them. Expected: FAIL.
- [ ] **Step 2: Implement** the backend, then `make ui-client` and commit `schema.d.ts`.
- [ ] **Step 3: Frontend tests first** (`NodePanel.test.tsx`): each test row is an eye button ("Show <name>", `aria-haspopup="dialog"`) before the name as plain text; opening it calls the endpoint once (not before) and shows, in a dialog titled with the name, by kind, the SQL viewer (raw and compiled tabs), the definition block, or the given and expected text; it shows the file path and a "Copy dbt selector" button (the name); closing and opening again does not call twice; Esc, the close button and a click outside close it, the focus returns to the eye button, and selecting another node closes it; a failed request shows an error with retry inside the dialog.
- [ ] **Step 4: Implement** `TestList` (a Mantine `Modal` per row) with `ReadOnlySql` for SQL and a read-only block for the definition.
- [ ] **Step 5: e2e:** select `dim_date`, open Tests, open `assert_dim_date_black_friday_weeks` and see its SQL in the dialog; close it with Esc; open a `unique` test and see its definition.
- [ ] **Step 6:** `make test`, `npm test`, `make lint`, the `lineage` Playwright spec. Commit `feat: tests open in a pop-up from an eye button`.

---

### Task 7b: Explorer polish

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `src/screens/explorer/CatalogTree.tsx`, `ExplorerScreen.tsx`, `Inspector.tsx`; `tests/Explorer.test.tsx`, `tests/ExplorerScreen.test.tsx`.

- **Aligned column rows:** the columns of an expanded table are one grid (name, type, markers) with no separators, so types and badges line up across rows.
- **Tree width:** 380 px by default from 1280 px up, 280 px below, between 200 and 480.
- **Inspector:** the domain is small, dimmed text; there is no Tests section (Lineage's node panel has the tests).
- Commit `feat: Explorer tree columns align, quieter Inspector domain, no Tests section`.

---

### Task 8: A Lineage layout where no edge passes behind a node

**Model:** implementer `sonnet` · reviewer **`opus`** (the placement and routing carry invariants the reviewer re-derives)

**Files:**
- Modify: `src/screens/lineage/layout.ts`, `src/screens/lineage/useGraphLayout.ts`, `src/screens/lineage/LineageCanvas.tsx`
- Create: `src/screens/lineage/edgeRoute.ts`, `src/screens/lineage/RoutedEdge.tsx`
- Test: `tests/lineage/layout.test.ts`, `tests/lineage/edgeRoute.test.ts`, `tests/lineage/LineageCanvasEdges.test.tsx`; new `tests/lineage/layoutMetrics.ts`, `tests/fixtures/lineage_project.json`

**Measured** on this project's whole graph (71 nodes, 109 edges, collapsed; the `sherpa_agent` exposure reads all 13 visible marts). The edges are measured as drawn: today's React Flow bezier, and B's routes through their gaps. The owner chose **B straightened** (renders kept outside the repo):

| | crossings | edges behind a node | vertical edge travel (px) | height (px) |
|---|---|---|---|---|
| plain stack (today) | 402 | 53 | 43,416 | 2,244 |
| placement near neighbours only | 143 | 28 | 19,336 | 2,253 |
| B: gaps for long edges, reordering | 128 | 0 | 24,873 | 2,283 |
| **B straightened (this task)** | **128** | **0** | **26,575** | **2,310** |

The exposure's 13 edges converge on one node, so they keep most of the vertical travel that is left.
Straightening is about the look (one rise, a level run, one landing), not the numbers.
Today's single curves drawn on B's positions brought a third of the hidden edges back on this graph, so the curve follows the gaps.

**Interfaces — Produces** (`layout.ts`, `edgeRoute.ts`):
- `orderNodes(nodes, edges)` is unchanged: ELK gives the starting order.
- `placeBands(nodes: BandNode[], edges: LayoutEdge[], order: Map<string, number>): BandPlacement`, pure and synchronous. It runs once per graph, as ELK does. `BandPlacement` holds the x of each column, each column's items in order (node ids and gap ids), and each item's collapsed top y.
- `stackBands(placement: BandPlacement, heights: Map<string, number>): { positions: Map<string, Point>; routes: Map<string, Point[]> }`, pure. It reruns on every height change. `routes` is keyed by `edgeKey(source, target)` and holds an edge's inner waypoints: for each gap, the column's left and right x at the gap's y. It is empty for an edge between adjacent columns.
- `useGraphLayout(nodes, edges, heights)` returns `{ positions, routes, state }`.
- `edgeRoute.ts`:
  - `edgePoints(start: Point, waypoints: Point[], end: Point): Point[]`, the monotone cubic (Fritsch–Carlson) through the points, sampled
  - `edgePath(start, waypoints, end): string`, the same curve as SVG cubic commands
- `RoutedEdge`: a custom React Flow edge. `BaseEdge` keeps the `react-flow__edge-path` class and the marker. Its path is `edgePath({sourceX, sourceY}, data.waypoints, {targetX, targetY})`, so the ends stay on the real handles. Table edges and column-focus edges both use it; a column edge takes the waypoints of its table edge (parent node → child node).

**Algorithm** (constants at the top of `layout.ts`: `SLOT_HEIGHT = 2`, `SLOT_NODE_GAP = 12`, `SLOT_SLOT_GAP = 8`, `SWEEPS = 12`, `STRAIGHTEN_SWEEPS = 8`; `ROW_GAP` and `BAND_GAP` unchanged):
1. **Columns.** Each band's sub-columns, with today's x.
2. **Gaps.** An edge from column a to column b > a + 1 gets a gap item (height `SLOT_HEIGHT`) in each column between them, one per edge.
3. **Start.** The real nodes start from the plain stack in ELK's order. Each gap starts on the straight line between its edge's ends.
4. **Sweeps.** `SWEEPS` times, alternating left-to-right and right-to-left, for each column:
   - each item's wish is the mean centre-y of its neighbours: a node's linked nodes or first and last gaps, a gap's two neighbours along its edge
   - the column is re-sorted by wish; a source system's tables stay together, as today
   - the column is fitted: the least-squares centres that keep its order and its spacing (`ROW_GAP` between nodes, `SLOT_NODE_GAP` between a node and a gap, `SLOT_SLOT_GAP` between gaps). This is isotonic regression (pool adjacent violators) on `wish − cumulative offset`.
5. **Straighten.** `STRAIGHTEN_SWEEPS` more sweeps without re-sorting, in which a gap's wish is the mean y of all gaps of its own edge, so a long edge rises once, runs level and comes down once.
6. **Close holes** (`closeHoles`). A run of items linked to nothing outside it (a table nothing reads, its gaps and the node they lead to, all at one y) slides up as far as the spacing rules of every column it crosses allow, for at most `COMPACT_PASSES` passes.
7. **Normalise.** Shift everything so the smallest top is 0.
8. **Heights** (`stackBands`). In each column, from the top, push an item down when it would overlap the one above, with that pair's spacing. Nothing moves upwards and no other column moves, so expanding a node moves only the nodes and gaps below it in its own column.

No edge passes behind a node, by construction: a route is level through a column, inside its gap, and the monotone curve between two columns stays between its two waypoints' y.

- [ ] **Step 1: Fixture and metrics.**
  - **Fixture:** save the whole-project graph from the running stack (`curl http://127.0.0.1:8090/api/lineage`) as `tests/fixtures/lineage_project.json`. Keep only `{ nodes: [{ id, layer, sourceName }], edges: [{ source, target }] }`: names only.
  - **Metrics:** in `layoutMetrics.ts` write `layoutMetrics(rects, curves) → { crossings, edgesBehindNodes, edgeTravel, height }`. Each curve is a sampled polyline with its source and target. Crossings count segment intersections between edges that share no node; an edge is behind a node when a sample point falls strictly inside a node it doesn't touch.
  - **Helper tests:** two crossing edges give 1; two parallel give 0; edges sharing a node never cross; an edge through a third node counts as behind it.
- [ ] **Step 2: Baseline.** With the plain stack and today's bezier (React Flow's default: control points at half the horizontal distance, horizontal tangents), compute the metrics on the fixture with the real `orderNodes`. The acceptance test measures them in the same run (ELK's ordering differs a little across platforms) and keeps these numbers only as a ±5% check of the helper. On the planning graph they are 402 / 53 / 43,416 / 2,244. The fixture comes from the stack started in the Environment step, so it has #21's nodes. If yours differ much, check the helper against the table first.
- [ ] **Step 3: Tests first.**
  - **`edgeRoute.test.ts`:**
    - the curve passes through every waypoint
    - between two consecutive points its y stays between theirs (no overshoot)
    - it is level between a gap's left and right waypoint
    - with no waypoints it is one S-curve, start and end tangents horizontal
    - `edgePath` is valid SVG whose sampled points match `edgePoints` within 0.5 px
  - **`layout.test.ts`, acceptance (the fixture, the edges as drawn):**
    - `edgesBehindNodes === 0`
    - `crossings ≤ 0.40 × BASELINE.crossings` (0.32 measured)
    - `edgeTravel ≤ 0.70 × BASELINE.edgeTravel` (0.61 measured)
    - `height ≤ 1.10 × BASELINE.height` (1.03 measured)
    - with every node expanded to 10 rows, `edgesBehindNodes === 0` still
  - **`layout.test.ts`, placement:**
    - a node whose only parent sits at y = 500 is centred on it
    - in every column the spacing rules hold
    - a source system's tables are contiguous
    - the same input gives the same output
    - the smallest top is 0; no `NaN`; no nodes gives empty maps
    - every node's x equals today's
    - an edge between adjacent columns has no waypoints
    - an edge skipping two columns has exactly 4 waypoints, level in pairs
    - an empty `order` (the ELK mock other tests use) still lays out
  - **`layout.test.ts`, expansion:** expanding one node changes the y only of the items below it in its own column.
  - **`LineageCanvasEdges.test.tsx`:** table and column edges are of the routed type and carry their waypoints; a column edge carries its table edge's waypoints. The existing handle assertions stay.

  Run them. Expected: FAIL.
- [ ] **Step 4: Implement,** keeping `layout.ts` top-down (public first) and the constants named. `useGraphLayout` runs `orderNodes` and then `placeBands` when the nodes or edges change, and `stackBands` when the heights change. The superseded-run guard stays.
- [ ] **Step 5: Verify.** `npm test` and lint, then the `lineage` and `layout` Playwright specs; the e2e reads edge paths through `react-flow__edge-path`, which `BaseEdge` keeps. Take browser screenshots of the whole project, a domain and a table path for the PR description. Commit `feat: Lineage edges keep clear of nodes, through gaps the layout reserves for them`.
- [ ] **Step 6: Time-boxed attempt, "curves that bend inside columns too"** (owner, 2026-11-01). One implementer attempt, no fix rounds, on a scratch branch off this commit.
  - **Idea:** today's look, one long S-curve, with the same guarantee: a gap's height is the stretch of the edge's curve that crosses its column, nodes keep clear of it, and the edge is one smooth curve through the gaps' centres. The update is damped and stops when no item moves more than 1 px.
  - **Outcome:** dropped: sloped gaps need height ∝ slope, ~30 long edges cross each column, the update loop's gain exceeds 1 (not converging at damping 0.02).

---

### Task 8b: Lineage keeps its view; exposures hidden by default

**Model:** implementer `sonnet` · reviewer `sonnet`

**Files:** `src/screens/lineage/LineageCanvas.tsx`, `viewport.ts`, `url.ts`, `LayerFilter.tsx`; `tests/lineage/LineageCanvasViewport.test.tsx`, `url.test.tsx`, `LayerFilter.test.tsx`, `layout.test.ts`.

- **Kept view:** the user's zoom and pan are kept in memory (`viewport.ts`) for the page session, never in `localStorage`, under the graph's subject (its selector). A redraw with the same subject (a layer chip, "Failing only", a refetch) restores them; a new subject (another mode, domain, or table-path node or depth) fits again, and so does a kept view that shows no node.
- **Zoom floor:** `MIN_ZOOM = 0.05`, so the whole project fits the canvas.
- **Exposures:** with no `layers` in the URL, every layer but the exposures is shown, in every mode; the exposures chip switches them on.
- **Holes:** the layout test "closes the hole under a table nothing reads" and "fits the whole project in the smallest canvas above the minimum zoom" pin step 6 of Task 8's algorithm and the zoom floor.
- Commit `feat: Lineage keeps its zoom and pan across redraws; exposures start hidden`.

---

### Task 9: Documentation

**Model:** docs implementer `sonnet` · reviewer `haiku`

- [ ] **Step 1: `CLAUDE.md`.**
  - **Layout:** `src/components/panes/` in the UI's description.
  - **Tests:** one line each:
    - a pane or handle is tested by keyboard in Vitest, and by a real drag only in Playwright;
    - hover and focus timing is tested on fake timers;
    - stored layout is read defensively and clamped, so every pane has a clamp test.
- [ ] **Step 2: Guides** (short; one symptom → fix row each).
  - **`basic/using-the-explorer.md`:** the panes, resizing (drag, keys, double-click to reset), collapsing, the tree's peek, the Inspector overlay below 1280 px. Symptom: "the layout is odd after a resize" → double-click the handle, or clear `sherpa.v1.layout.*` in the browser's site data.
  - **`basic/using-lineage.md`:** how to read the layout (a long edge runs level through the gaps reserved for it, so no edge hides behind a node), the mode order, the panel's handle and fold, disabled tabs and their reasons, column types, opening a test. Symptom: a type column is empty → the node is an ephemeral model.
  - **`advanced/extending-the-ui.md`:** adding a pane (the toolkit, the storage id, its clamp test), the layout metrics test and how to read it, and the tests endpoint.
- [ ] **Step 3:** `make lint`. Commit `docs: panes, layout, node panel and test definitions`.

---

### Task 10: Panel review

**Model:** every reviewer and the devil's advocate on `opus`, dispatched by the controller (`CLAUDE.md`). Fixes go through the usual loop.

- [ ] **Step 1: Reviewers,** in parallel. Each gets `git diff main...HEAD`, this plan, its focus and the spec sections named:
  - **Frontend engineer** (spec §6.1, §6.4, §6.5): the toolkit's API and accessibility (separators, focus, peek), the Explorer and Lineage panes, CodeMirror and the grid under resize, the tests' honesty and flakiness, the 1024/1280/1440 layouts.
  - **Software engineer:** the tests endpoint and service, `NodeSql` path safety, the column types rule, the backend tests and the `warehouse` twins, and the API client regeneration.
  - **Data engineer:** `NodeTest` against dbt's real artifacts (generic, singular and unit tests, shortened names, `original_file_path` versus `path`), `catalog.json` types versus contract types, and the selector of a test.
  - **Security engineer:** the new endpoint (what it reads and returns, unit-test rows, id handling), `localStorage` contents, the dependency.
  - **Frontend engineer, layout:** the placement and routing invariants (spacing, no edge behind a node also when expanded, determinism), the metrics helper's honesty, and the edge rendering against React Flow's handles and markers.
  - **Devil's advocate:** what a user does that the tests don't (stored layout from an older build, two browser tabs, a 3-monitor setup, a very long test name), whether the placement beats a simpler tweak, and which tests restate the code.
- [ ] **Step 2: Devil's advocate,** with the diff and every finding.
- [ ] **Step 3: Triage and fix:** `make lint`, `make test`, `npm test` three times, `make e2e` all green. The triage table, the three layout screenshots, the metrics table (before and after) and the result of Task 8's time-boxed attempt go in the PR description.
