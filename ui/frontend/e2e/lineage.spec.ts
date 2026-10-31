import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, type Locator, type Page, test } from "@playwright/test";

import { adminScalar } from "./warehouse";

interface Graph {
  nodes: { unique_id: string; name: string; domain: string | null; columns?: { name: string }[] | null }[];
  edges: { source: string; target: string }[];
  build: { state: string };
}

interface ColumnFocus {
  columns: { name: string; node: string; direction: string }[];
  edges: { parent: { node: string; column: string }; child: { node: string; column: string } }[];
}

const FAILING_ENV = "SHERPA_E2E_LINEAGE_FAILING";
const ORDERS_ID = "model.sherpa.fct_orders";
const NET_REVENUE = "net_revenue";
const REPO_ROOT = resolve(import.meta.dirname, "../../..");
// Distances between a drawn edge's end and a handle, in flow units (the handle is 6 px wide; rows are 28 apart).
const HANDLE_TOLERANCE = 8;

// A node's button in the canvas: its name followed by layer, materialization and status.
function graphNode(page: Page, name: string): Locator {
  return page.getByRole("button", { name: new RegExp(`^${name}(?!\\w)`) });
}

async function shownNames(page: Page): Promise<string[]> {
  await expect(page.locator("button[aria-pressed]").first()).toBeVisible();
  const names = await page
    .locator("button[aria-pressed]")
    .evaluateAll((buttons) =>
      buttons.map((button) => button.querySelector("span")?.textContent ?? ""),
    );
  return names.sort();
}

async function wholeGraph(page: Page): Promise<Graph> {
  return (await (await page.request.get("/api/lineage")).json()) as Graph;
}

// Every node reachable from `starts` along the edges (direction "down") or against them ("up"), the starts included.
function reach(
  graph: Graph,
  starts: string[],
  direction: "up" | "down",
): Set<string> {
  const [from, to] =
    direction === "up"
      ? (["target", "source"] as const)
      : (["source", "target"] as const);
  const seen = new Set(starts);
  const queue = [...starts];
  for (let id = queue.pop(); id !== undefined; id = queue.pop()) {
    for (const edge of graph.edges) {
      if (edge[from] === id && !seen.has(edge[to])) {
        seen.add(edge[to]);
        queue.push(edge[to]);
      }
    }
  }
  return seen;
}

function namesOf(graph: Graph, ids: Set<string>): string[] {
  return graph.nodes
    .filter((node) => ids.has(node.unique_id))
    .map((node) => node.name)
    .sort();
}

// The graph node's wrapper (its position is the node's), and a column row's button inside it.
function flowNode(page: Page, uniqueId: string): Locator {
  return page.locator(`[data-id="${uniqueId}"]`);
}

function columnRow(page: Page, uniqueId: string, column: string): Locator {
  return flowNode(page, uniqueId).locator(`button[title="${column}"]`);
}

async function expand(page: Page, name: string): Promise<void> {
  await page.getByRole("button", { name: `Columns of ${name}` }).click();
}

// Clicked like a user does.
async function focusColumn(page: Page, uniqueId: string, column: string): Promise<void> {
  const row = columnRow(page, uniqueId, column);
  await row.click();
  await expect(row).toHaveAttribute("aria-pressed", "true");
}

const SETTLE_INTERVAL_MS = 100;

// The box of an element once it has stopped moving: two reads SETTLE_INTERVAL_MS apart agree (a layout or a pan
// still running moves it between reads; the first read has nothing to agree with).
async function settledBox(locator: Locator): Promise<{ x: number; y: number; width: number; height: number }> {
  let previous: string | undefined;
  let box = await locator.boundingBox();
  await expect
    .poll(
      async () => {
        box = await locator.boundingBox();
        const current = JSON.stringify(box);
        const same = current === previous;
        previous = current;
        return same;
      },
      { intervals: [SETTLE_INTERVAL_MS] },
    )
    .toBe(true);
  return box!;
}

async function ordersColumns(page: Page): Promise<string[]> {
  const response = await page.request.get("/api/lineage?columns=true");
  const graph = (await response.json()) as Graph;
  return (graph.nodes.find((node) => node.unique_id === ORDERS_ID)?.columns ?? []).map((column) => column.name);
}

async function columnFocusOf(page: Page, uniqueId: string, column: string): Promise<ColumnFocus> {
  const response = await page.request.get(`/api/lineage/columns/${uniqueId}/${column}`);
  expect(response.ok(), `column focus answered ${response.status()}`).toBe(true);
  return (await response.json()) as ColumnFocus;
}

// The expectations come from the warehouse and the manifest, not from the last build's luck (a real warning or a
// failing build must not fail the spec). The newest full prod build is the one the app reads (build_status.py).
const LAST_FULL_BUILD =
  "select invocation_id from ops.dbt_run_results where target_name = 'prod' and command = 'build' and is_full_run " +
  "group by invocation_id order by max(run_started_at) desc, invocation_id desc limit 1";

interface RecordedBuild {
  recorded: boolean;
  // The label the app shows for fct_orders (STATUS in GraphNode.tsx).
  status: string;
}

function ordersBuild(): RecordedBuild {
  const rows = Number(
    adminScalar(`select count(*) from ops.dbt_run_results where invocation_id = (${LAST_FULL_BUILD})`),
  );
  if (rows === 0) return { recorded: false, status: "" };
  const manifest = JSON.parse(
    readFileSync(resolve(REPO_ROOT, "data-platform/dbt/target-prod/manifest.json"), "utf8"),
  ) as { nodes: Record<string, { resource_type: string; attached_node?: string }> };
  const tests = Object.entries(manifest.nodes)
    .filter(([, node]) => node.resource_type === "test" && node.attached_node === ORDERS_ID)
    .map(([id]) => `'${id}'`);
  const statuses = (ids: string[]): string[] =>
    ids.length === 0
      ? []
      : adminScalar(
          `select coalesce(string_agg(distinct status, ','), '') from ops.dbt_run_results where invocation_id = (${LAST_FULL_BUILD}) and unique_id in (${ids.join(",")})`,
        )
          .split(",")
          .filter(Boolean);
  const own = statuses([`'${ORDERS_ID}'`])[0];
  const outcomes = statuses(tests);
  const failedOwn = own === "error" || own === "runtime error" || own === "partial success";
  if (failedOwn || outcomes.some((o) => ["fail", "error", "runtime error"].includes(o))) return { recorded: true, status: "failed" };
  if (own === "skipped") return { recorded: true, status: "skipped" };
  if (outcomes.includes("warn")) return { recorded: true, status: "warned" };
  if (own === "success" || own === "no-op") return { recorded: true, status: "ok" };
  return { recorded: true, status: "not in the last build" };
}

// The upstream source columns of fct_orders.net_revenue that the warehouse test of the column lineage pins (a
// hand-checked list, ui/backend/tests/lineage/test_column_lineage_warehouse.py): read from there, not copied.
function pinnedSourceColumns(): string[] {
  const file = readFileSync(
    resolve(REPO_ROOT, "ui/backend/tests/lineage/test_column_lineage_warehouse.py"),
    "utf8",
  );
  const block = /"net_revenue",\s*\{(.*?)\n {8}\},/s.exec(file)?.[1] ?? "";
  const pinned = [...block.matchAll(/\(f"\{SHOP\}\.(\w+)", "(\w+)"\)/g)].map((m) => `${m[1]}.${m[2]}`);
  expect(pinned.length, "the net_revenue trace was not found in the warehouse test").toBeGreaterThan(0);
  return pinned.sort();
}

// Where a drawn edge starts and ends on screen, and the canvas zoom (a pixel is 1/zoom flow units).
async function edgeEnds(
  page: Page,
  edgeId: string,
): Promise<{ start: [number, number]; end: [number, number]; zoom: number }> {
  return page.evaluate((id) => {
    const group = [...document.querySelectorAll(".react-flow__edge")].find((e) => e.getAttribute("data-id") === id);
    const path = group?.querySelector<SVGPathElement>("path.react-flow__edge-path");
    if (!path) throw new Error(`edge ${id} is not drawn`);
    const matrix = path.getScreenCTM()!;
    const at = (length: number): [number, number] => {
      const point = path.getPointAtLength(length).matrixTransform(matrix);
      return [point.x, point.y];
    };
    return { start: at(0), end: at(path.getTotalLength()), zoom: matrix.a };
  }, edgeId);
}

async function handleCentre(page: Page, uniqueId: string, handle: string, side: "source" | "target"): Promise<[number, number]> {
  const box = await page
    .locator(`.react-flow__handle.${side}[data-nodeid="${uniqueId}"][data-handleid="${handle}"]`)
    .boundingBox();
  expect(box, `no ${side} handle ${handle} on ${uniqueId}`).not.toBeNull();
  return [box!.x + box!.width / 2, box!.y + box!.height / 2];
}

// The edge leaves the source's handle and enters the target's, within a handle's size.
async function expectEdgeJoins(
  page: Page,
  edgeId: string,
  from: { node: string; handle: string },
  to: { node: string; handle: string },
): Promise<void> {
  const ends = await edgeEnds(page, edgeId);
  const source = await handleCentre(page, from.node, from.handle, "source");
  const target = await handleCentre(page, to.node, to.handle, "target");
  const gap = (a: [number, number], b: [number, number]): number => Math.hypot(a[0] - b[0], a[1] - b[1]) / ends.zoom;

  expect(gap(ends.start, source), `${edgeId} start vs ${from.handle}`).toBeLessThanOrEqual(HANDLE_TOLERANCE);
  expect(gap(ends.end, target), `${edgeId} end vs ${to.handle}`).toBeLessThanOrEqual(HANDLE_TOLERANCE);
}

// The fit shows every node: each node's box lies inside the canvas (a fit that framed one node leaves the rest outside).
async function expectGraphFitted(page: Page): Promise<void> {
  await expect
    .poll(async () => {
      const canvas = await page.getByLabel("Lineage graph").boundingBox();
      const boxes = await page.locator(".react-flow__node").evaluateAll((nodes) =>
        nodes.map((node) => {
          const { x, y, width, height } = node.getBoundingClientRect();
          return { id: node.getAttribute("data-id") ?? "", x, y, right: x + width, bottom: y + height };
        }),
      );
      if (canvas === null || boxes.length === 0) return ["no canvas or nodes"];
      return boxes
        .filter((b) => b.x < canvas.x - 1 || b.y < canvas.y - 1 || b.right > canvas.x + canvas.width + 1 || b.bottom > canvas.y + canvas.height + 1)
        .map((b) => b.id);
    }, { message: "nodes outside the canvas after the fit" })
    .toEqual([]);
}

// Every column edge of the focus whose two nodes are shown has a drawn path (React Flow drops an edge whose handle it
// cannot find, and only warns about it in development builds).
async function expectColumnEdgesDrawn(page: Page, focus: ColumnFocus): Promise<void> {
  const shown = new Set(await page.locator(".react-flow__node").evaluateAll((nodes) => nodes.map((n) => n.getAttribute("data-id")!)));
  const expected = focus.edges.filter((edge) => shown.has(edge.parent.node) && shown.has(edge.child.node)).length;
  expect(expected, "the focus has edges between shown nodes").toBeGreaterThan(0);
  await expect(page.locator('.react-flow__edge[data-id^="col:"] path.react-flow__edge-path')).toHaveCount(expected);
}

test("the whole project shows fct_orders and a source, with the build the warehouse records", async ({
  page,
}) => {
  const build = ordersBuild();

  await page.goto("/lineage");

  await expect(graphNode(page, "fct_orders")).toBeVisible();
  await expect(graphNode(page, "orders")).toBeVisible();
  await expect(page.getByText(/^\d+ nodes$/)).toBeVisible();
  await expect(page.getByText(/Build status unavailable/)).toBeHidden();
  const banner = page.getByText(/No full build recorded/);
  if (build.recorded) await expect(banner).toBeHidden();
  else await expect(banner).toBeVisible();
});

test("the graph is fitted after the load and after each change of selector", async ({ page }) => {
  await page.goto("/lineage");
  await expectGraphFitted(page);
  const box = page.getByRole("textbox", { name: "dbt selector" });

  await box.fill("fct_orders");
  await box.press("Enter");
  await expect(page).toHaveURL(/select=fct_orders$/);
  await expectGraphFitted(page);

  await box.fill("+fct_orders+");
  await box.press("Enter");
  await expect(graphNode(page, "stg_shop__orders")).toBeVisible();
  await expectGraphFitted(page);

  await box.fill("1+fct_orders+2");
  await box.press("Enter");
  await expect(page).toHaveURL(/select=1%2Bfct_orders%2B2/);
  await expectGraphFitted(page);

  await box.fill("");
  await box.press("Enter");
  await expect(graphNode(page, "orders")).toBeVisible();
  await expectGraphFitted(page);
});

test("the bands run left to right and every edge points right", async ({ page }) => {
  await page.goto("/lineage");
  await expect(graphNode(page, "fct_orders")).toBeVisible();
  const graph = await wholeGraph(page);
  const intermediate = graph.nodes.find((node) => node.unique_id.startsWith("model.sherpa.int_"));
  expect(intermediate).toBeDefined();
  const xOf = async (uniqueId: string): Promise<number> => {
    const box = await flowNode(page, uniqueId).boundingBox();
    expect(box, uniqueId).not.toBeNull();
    return box!.x;
  };

  const chain = [
    "source.sherpa.shop.orders",
    "seed.sherpa.channel_mapping",
    "model.sherpa.stg_shop__orders",
    "snapshot.sherpa.snap_product_variants",
    intermediate!.unique_id,
    ORDERS_ID,
  ];
  const xs = await Promise.all(chain.map(xOf));

  for (let i = 1; i < chain.length; i++) {
    expect(xs[i - 1], `${chain[i - 1]} is left of ${chain[i]}`).toBeLessThan(xs[i]!);
  }
  expect(await xOf("model.sherpa.dim_customers")).toBeGreaterThan(xs[chain.length - 1]!);
  const allX = new Map(await Promise.all(graph.nodes.map(async (n) => [n.unique_id, await xOf(n.unique_id)] as const)));
  const backwards = graph.edges.filter((edge) => !(allX.get(edge.target)! > allX.get(edge.source)!));
  expect(backwards).toEqual([]);
});

test("the node panel sits below the canvas and selecting a node never resizes it", async ({ page }) => {
  await page.goto("/lineage");
  await expect(graphNode(page, "fct_orders")).toBeVisible();
  const canvas = page.getByLabel("Lineage graph");
  const panel = page.getByRole("complementary", { name: "Node details" });
  await expect(panel.getByText("Select a node to see its SQL, columns and tests")).toBeVisible();
  const before = await canvas.boundingBox();

  await graphNode(page, "fct_orders").click();

  await expect(panel.getByRole("heading", { name: "fct_orders", level: 4 })).toBeVisible();
  expect(await canvas.boundingBox()).toEqual(before);
  const panelBox = await panel.boundingBox();
  expect(panelBox!.y).toBeGreaterThanOrEqual(before!.y + before!.height);
});

test("focusing fct_orders.net_revenue lights its source columns and dims the rest; Esc twice clears", async ({
  page,
}) => {
  await page.goto("/lineage?select=%2Bfct_orders%2B");
  await expect(graphNode(page, "fct_orders")).toBeVisible();
  const graph = await wholeGraph(page);
  const focus = await columnFocusOf(page, ORDERS_ID, NET_REVENUE);
  const inChain = new Set([ORDERS_ID, ...focus.columns.map((column) => column.node)]);
  const chainNodes = new Set([
    ...reach(graph, [ORDERS_ID], "up"),
    ...reach(graph, [ORDERS_ID], "down"),
  ]);
  const outside = graph.nodes.find((node) => chainNodes.has(node.unique_id) && !inChain.has(node.unique_id));
  expect(outside, "a node of +fct_orders+ outside the column's chain").toBeDefined();

  await expand(page, "fct_orders");
  await focusColumn(page, ORDERS_ID, NET_REVENUE);

  const litSources = await page
    .locator('[data-id^="source."] button[aria-pressed][title]')
    .evaluateAll((rows) =>
      rows.map((row) => `${row.closest("[data-id]")!.getAttribute("data-id")!.split(".").pop()}.${row.getAttribute("title")}`),
    );
  expect(litSources.sort()).toEqual(pinnedSourceColumns());
  await expect(graphNode(page, outside!.name)).toHaveAttribute("data-dimmed", "");
  await expect(graphNode(page, "stg_shop__order_items")).not.toHaveAttribute("data-dimmed", "");
  await expect(page).toHaveURL(new RegExp(`column=${NET_REVENUE}`));
  await expectColumnEdgesDrawn(page, focus);

  await page.keyboard.press("Escape");

  await expect(page).not.toHaveURL(/column=/);
  await expect(page).toHaveURL(new RegExp(`node=${encodeURIComponent(ORDERS_ID)}`));
  await expect(graphNode(page, outside!.name)).not.toHaveAttribute("data-dimmed", "");
  await expect(graphNode(page, "fct_orders")).toBeFocused();

  await page.keyboard.press("Escape");

  await expect(page).not.toHaveURL(/node=/);
  await expect(graphNode(page, "fct_orders")).toHaveAttribute("aria-pressed", "false");
});

test("a column edge joins the two rows, a table edge of an expanded node the node handles", async ({ page }) => {
  await page.goto("/lineage?select=%2Bfct_orders%2B");
  await expect(graphNode(page, "fct_orders")).toBeVisible();
  const focus = await columnFocusOf(page, ORDERS_ID, NET_REVENUE);
  const intoOrders = focus.edges.find((edge) => edge.child.node === ORDERS_ID);
  expect(intoOrders, "a column edge into fct_orders").toBeDefined();
  const { parent, child } = intoOrders!;
  await expand(page, "fct_orders");

  await focusColumn(page, ORDERS_ID, NET_REVENUE);

  await expectEdgeJoins(
    page,
    `col:${parent.node}.${parent.column}->${child.node}.${child.column}`,
    { node: parent.node, handle: `col:${parent.column}` },
    { node: child.node, handle: `col:${child.column}` },
  );
  await expectColumnEdgesDrawn(page, focus);

  await page.keyboard.press("Escape");
  await expect(page).not.toHaveURL(/column=/);

  // Out of the expanded orders node and into it: both ends sit on the node-level handle, not on a column row.
  const [firstColumn] = await ordersColumns(page);
  const firstRow = await handleCentre(page, ORDERS_ID, `col:${firstColumn}`, "source");
  const leaving = await edgeEnds(page, `${ORDERS_ID}->model.sherpa.fct_order_items`);
  expect(Math.hypot(leaving.start[0] - firstRow[0], leaving.start[1] - firstRow[1]) / leaving.zoom).toBeGreaterThan(
    HANDLE_TOLERANCE,
  );
  await expectEdgeJoins(
    page,
    `${ORDERS_ID}->model.sherpa.fct_order_items`,
    { node: ORDERS_ID, handle: "node" },
    { node: "model.sherpa.fct_order_items", handle: "node" },
  );
  await expectEdgeJoins(
    page,
    `model.sherpa.stg_shop__orders->${ORDERS_ID}`,
    { node: "model.sherpa.stg_shop__orders", handle: "node" },
    { node: ORDERS_ID, handle: "node" },
  );
});

test("keyboard focus on a row far down a tall node scrolls it into the canvas", async ({ page }) => {
  await page.goto("/lineage?select=fct_orders");
  await expect(graphNode(page, "fct_orders")).toBeVisible();
  const columns = await ordersColumns(page);
  await expand(page, "fct_orders");
  const last = columnRow(page, ORDERS_ID, columns[columns.length - 1]!);
  const canvas = page.getByLabel("Lineage graph");
  const inside = async (): Promise<boolean> => {
    const box = await last.boundingBox();
    const pane = await canvas.boundingBox();
    return box !== null && pane !== null && box.y >= pane.y && box.y + box.height <= pane.y + pane.height;
  };
  expect(await inside(), "the last row starts outside the canvas").toBe(false);

  await last.focus();

  await expect.poll(inside).toBe(true);
});

test("a mouse click on a row half outside the canvas focuses its column and leaves the viewport alone", async ({ page }) => {
  await page.goto("/lineage?select=fct_orders");
  await expect(graphNode(page, "fct_orders")).toBeVisible();
  const columns = await ordersColumns(page);
  const name = columns[columns.length - 1]!;
  await expand(page, "fct_orders");
  const row = columnRow(page, ORDERS_ID, name);
  // The canvas' bottom edge must be on screen for the mouse to click next to it.
  await page.getByLabel("Lineage graph").scrollIntoViewIfNeeded();
  const pane = (await page.getByLabel("Lineage graph").boundingBox())!;
  const rowBox = await settledBox(row);
  // Pan the canvas by dragging an empty corner, until the row's middle sits on the canvas' bottom edge.
  const shift = pane.y + pane.height - (rowBox.y + rowBox.height / 2);
  await page.mouse.move(pane.x + 5, pane.y + 5);
  await page.mouse.down();
  await page.mouse.move(pane.x + 5, pane.y + 5 + shift, { steps: 5 });
  await page.mouse.up();
  const half = await settledBox(row);
  expect(half.y, "the row starts half outside").toBeLessThan(pane.y + pane.height);
  expect(half.y + half.height, "the row starts half outside").toBeGreaterThan(pane.y + pane.height);
  const viewport = page.locator(".react-flow__viewport");
  const before = await viewport.getAttribute("style");

  await page.mouse.click(half.x + 20, pane.y + pane.height - 4);

  await expect(page).toHaveURL(new RegExp(`column=${name}`));
  await expect(row).toHaveAttribute("aria-pressed", "true");
  expect(await viewport.getAttribute("style")).toBe(before);
});

test("a focus that reaches outside the selector says so and links to +fct_orders+", async ({ page }) => {
  await page.goto("/lineage?select=fct_orders");
  await expect(graphNode(page, "fct_orders")).toBeVisible();
  await expand(page, "fct_orders");

  await focusColumn(page, ORDERS_ID, NET_REVENUE);

  const panel = page.getByRole("complementary", { name: "Node details" });
  const note = panel.getByRole("note").filter({ hasText: "outside this view" });
  await expect(note).toBeVisible();
  await note.getByRole("link", { name: "+fct_orders+" }).click();
  await expect(page).toHaveURL(/select=%2Bfct_orders%2B/);
  await expect(page.getByRole("textbox", { name: "dbt selector" })).toHaveValue("+fct_orders+");
  await expect(graphNode(page, "stg_shop__orders")).toBeVisible();
  await expect(panel.getByRole("note").filter({ hasText: "outside this view" })).toBeHidden();
});

test("the Table path depth controls limit the chain on each side", async ({ page }) => {
  await page.goto("/lineage");
  await expect(graphNode(page, "fct_orders")).toBeVisible();
  const graph = await wholeGraph(page);
  const withinDepth = (direction: "up" | "down", depth: number): Set<string> => {
    const [from, to] = direction === "up" ? (["target", "source"] as const) : (["source", "target"] as const);
    const seen = new Set([ORDERS_ID]);
    let frontier = [ORDERS_ID];
    for (let level = 0; level < depth; level++) {
      frontier = graph.edges.filter((edge) => frontier.includes(edge[from])).map((edge) => edge[to]).filter((id) => !seen.has(id));
      frontier.forEach((id) => seen.add(id));
    }
    return seen;
  };

  await page.getByRole("radiogroup", { name: "Selection mode" }).getByText("Table path", { exact: true }).click();
  await page.getByRole("combobox", { name: "Table", exact: true }).fill("fct_orders");
  await page.getByRole("option", { name: "fct_orders", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "dbt selector" })).toHaveValue("+fct_orders+");
  await page.getByRole("combobox", { name: "Upstream" }).click();
  await page.getByRole("option", { name: "1", exact: true }).click();
  await page.getByRole("combobox", { name: "Downstream" }).click();
  await page.getByRole("option", { name: "2", exact: true }).click();

  await expect(page.getByRole("textbox", { name: "dbt selector" })).toHaveValue("1+fct_orders+2");
  const expected = new Set([...withinDepth("up", 1), ...withinDepth("down", 2)]);
  await expect.poll(() => shownNames(page)).toEqual(namesOf(graph, expected));
});

test("Open in Lineage from the Explorer shows only the chain of the table", async ({
  page,
}) => {
  await page.goto("/explorer");
  await page.getByRole("button", { name: "fct_orders", exact: true }).click();
  await page
    .getByRole("complementary", { name: "Table details" })
    .getByRole("link", { name: "Open in Lineage" })
    .click();

  await expect(page).toHaveURL(/\/lineage\?select=%2Bfct_orders%2B$/);
  await expect(page.getByRole("textbox", { name: "dbt selector" })).toHaveValue(
    "+fct_orders+",
  );
  const graph = await wholeGraph(page);
  const chain = new Set([
    ...reach(graph, [ORDERS_ID], "up"),
    ...reach(graph, [ORDERS_ID], "down"),
  ]);
  await expect.poll(() => shownNames(page)).toEqual(namesOf(graph, chain));
});

test("the domain mode shows the sales marts and their ancestors only", async ({
  page,
}) => {
  await page.goto("/lineage");
  await page
    .getByRole("radiogroup", { name: "Selection mode" })
    .getByText("Domain", { exact: true })
    .click();
  await page.getByRole("combobox", { name: "Domain", exact: true }).click();
  await page.getByRole("option", { name: "sales", exact: true }).click();

  await expect(page.getByRole("textbox", { name: "dbt selector" })).toHaveValue(
    "+config.meta.domain:sales",
  );
  const graph = await wholeGraph(page);
  const sales = graph.nodes
    .filter((node) => node.domain === "sales")
    .map((node) => node.unique_id);
  expect(sales.length).toBeGreaterThan(0);
  await expect
    .poll(() => shownNames(page))
    .toEqual(namesOf(graph, reach(graph, sales, "up")));
});

test("selecting fct_orders dims what is outside its chain and opens the panel", async ({
  page,
}) => {
  const build = ordersBuild();
  await page.goto("/lineage");
  await graphNode(page, "fct_orders").click();

  await expect(graphNode(page, "fct_orders")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page).toHaveURL(
    new RegExp(`node=${encodeURIComponent(ORDERS_ID)}`),
  );
  const graph = await wholeGraph(page);
  const ancestors = reach(graph, [ORDERS_ID], "up");
  const chain = new Set([...ancestors, ...reach(graph, [ORDERS_ID], "down")]);
  const outside = graph.nodes.find((node) => !chain.has(node.unique_id));
  const staging = graph.nodes.find(
    (node) => node.name.startsWith("stg_") && ancestors.has(node.unique_id),
  );
  expect(outside).toBeDefined();
  expect(staging).toBeDefined();
  await expect(graphNode(page, outside?.name ?? "")).toHaveAttribute(
    "data-dimmed",
    "",
  );
  await expect(graphNode(page, staging?.name ?? "")).not.toHaveAttribute(
    "data-dimmed",
    "",
  );
  await expect(graphNode(page, "dim_customers")).not.toHaveAttribute(
    "data-dimmed",
    "",
  );

  const panel = page.getByRole("complementary", { name: "Node details" });
  await expect(
    panel.getByRole("heading", { name: "fct_orders", level: 4 }),
  ).toBeVisible();
  if (build.recorded) await expect(panel.getByTestId("node-status")).toHaveText(new RegExp(`^${build.status}`));
  else await expect(panel.getByText("No build status.")).toBeVisible();
  await panel.getByRole("tab", { name: "SQL", exact: true }).click();
  await panel.getByRole("tab", { name: "Compiled SQL" }).click();
  const compiled = panel
    .getByRole("tabpanel", { name: "Compiled SQL" })
    .locator(".cm-content");
  await expect(compiled).toContainText("select");
  await expect(compiled).not.toContainText("{{");

  await panel.getByRole("tab", { name: "Overview" }).click();
  await panel.getByRole("link", { name: "Open in Explorer" }).click();

  await expect(page).toHaveURL(/\/explorer\?table=fct_orders/);
  await expect(
    page
      .getByRole("complementary", { name: "Table details" })
      .getByRole("heading", { name: "fct_orders" }),
  ).toBeVisible();
});

test("an unsupported selector shows the supported forms and keeps the previous graph", async ({
  page,
}) => {
  await page.goto("/lineage?select=%2Bfct_orders%2B");
  const before = await shownNames(page);
  expect(before.length).toBeGreaterThan(1);
  const box = page.getByRole("textbox", { name: "dbt selector" });

  await box.fill("fct_orders,dim_customers");
  await box.press("Enter");

  await expect(
    page.getByRole("alert").filter({ hasText: "supported selectors:" }),
  ).toBeVisible();
  await expect.poll(() => shownNames(page)).toEqual(before);
});

test("reload and back/forward restore the view, selected node included", async ({
  page,
}) => {
  await page.goto("/lineage?select=%2Bfct_orders%2B");
  await graphNode(page, "fct_orders").click();
  await expect(
    page.getByRole("complementary", { name: "Node details" }),
  ).toBeVisible();
  const withNode = page.url();
  const chain = await shownNames(page);

  await page.reload();

  await expect(page).toHaveURL(withNode);
  await expect(graphNode(page, "fct_orders")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(
    page.getByRole("complementary", { name: "Node details" }),
  ).toBeVisible();
  expect(await shownNames(page)).toEqual(chain);

  const box = page.getByRole("textbox", { name: "dbt selector" });
  await box.fill("dim_customers");
  await box.press("Enter");
  await expect(page).toHaveURL(/select=dim_customers/);
  await expect(graphNode(page, "fct_orders")).toBeHidden();

  await page.goBack();

  await expect(box).toHaveValue("+fct_orders+");
  await expect(graphNode(page, "fct_orders")).toBeVisible();
  await expect(page).toHaveURL(withNode);
  await expect(page.getByRole("complementary", { name: "Node details" })).toBeVisible();
  await expect(graphNode(page, "fct_orders")).toHaveAttribute("aria-pressed", "true");

  await page.goForward();

  await expect(box).toHaveValue("dim_customers");
  await expect(graphNode(page, "fct_orders")).toBeHidden();
});

test("closing the panel with Esc returns the focus to the node", async ({ page }) => {
  await page.goto("/lineage?select=%2Bfct_orders%2B");
  await graphNode(page, "fct_orders").click();
  await expect(page.getByRole("complementary", { name: "Node details" })).toBeVisible();
  await page.getByRole("button", { name: "Close the node panel" }).focus();

  await page.keyboard.press("Escape");

  const panel = page.getByRole("complementary", { name: "Node details" });
  await expect(panel.getByText("Select a node to see its SQL, columns and tests")).toBeVisible();
  await expect(panel.getByRole("heading", { name: "fct_orders", level: 4 })).toBeHidden();
  await expect(graphNode(page, "fct_orders")).toBeFocused();
});

// The shared warehouse must not hold a really failing build, so this inserts a synthetic full prod build with the
// shape of the audit hook's rows (data-platform/dbt/macros/record_run_results.sql), copied from the newest real one,
// and deletes it afterwards. Run it on purpose: SHERPA_E2E_LINEAGE_FAILING=1. A run that is killed before its cleanup
// leaves a fake newest build behind; the next run clears it first, or by hand as the admin:
//   delete from ops.dbt_run_results where invocation_id like 'e2e-lineage-%';
const LEFTOVERS_SQL = "delete from ops.dbt_run_results where invocation_id like 'e2e-lineage-%'";
test("a failing test turns its node red and a warning stays out of Failing only", async ({
  page,
}) => {
  test.skip(
    !process.env[FAILING_ENV],
    `writes to the shared warehouse: run it with ${FAILING_ENV}=1`,
  );
  const invocation = `e2e-lineage-${Date.now()}`;
  const firstNotNullTestOf = (model: string) =>
    `(select unique_id from ops.dbt_run_results where invocation_id = '${invocation}' ` +
    `and unique_id like 'test.sherpa.not_null_${model}\\_%' order by unique_id limit 1)`;
  const setStatus = (model: string, status: string) =>
    adminScalar(
      `update ops.dbt_run_results set status = '${status}', failures = 1 ` +
        `where invocation_id = '${invocation}' and unique_id = ${firstNotNullTestOf(model)}`,
    );

  adminScalar(LEFTOVERS_SQL);
  try {
    const inserted = adminScalar(
      `insert into ops.dbt_run_results select '${invocation}', target_name, command, is_full_run, unique_id, ` +
        "resource_type, status, failures, execution_time_s, message, now() from ops.dbt_run_results where " +
        "invocation_id = (select invocation_id from ops.dbt_run_results where target_name = 'prod' and " +
        "command = 'build' and is_full_run group by invocation_id order by max(run_started_at) desc, " +
        "invocation_id desc limit 1)",
    );
    expect(inserted, "no real full prod build to copy: run make build-prod").toMatch(/INSERT 0 [1-9]\d*$/);
    expect(setStatus("fct_orders", "fail")).toBe("UPDATE 1");
    expect(setStatus("dim_customers", "warn")).toBe("UPDATE 1");

    await page.goto("/lineage");

    await expect(graphNode(page, "fct_orders")).toContainText("failed");
    await expect(graphNode(page, "dim_customers")).toContainText("warned");

    // click, not check(): the switch follows the URL, so its state changes a render after the click.
    await page.getByRole("switch", { name: "Failing only" }).click();
    await expect(page.getByRole("switch", { name: "Failing only" })).toBeChecked();

    await expect(graphNode(page, "fct_orders")).toBeVisible();
    await expect(graphNode(page, "dim_customers")).toBeHidden();
    // The status line of a passing node reads exactly "ok"; a node's name never does.
    await expect(
      page.locator("button[aria-pressed]").filter({ has: page.getByText("ok", { exact: true }) }),
    ).toHaveCount(0);
  } finally {
    adminScalar(
      `delete from ops.dbt_run_results where invocation_id = '${invocation}'`,
    );
    expect(
      adminScalar(
        `select count(*) from ops.dbt_run_results where invocation_id = '${invocation}'`,
      ),
    ).toBe("0");
  }
});
