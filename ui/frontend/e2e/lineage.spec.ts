import { expect, type Locator, type Page, test } from "@playwright/test";

import { adminScalar } from "./warehouse";

interface Graph {
  nodes: { unique_id: string; name: string; domain: string | null }[];
  edges: { source: string; target: string }[];
  build: { state: string };
}

const FAILING_ENV = "SHERPA_E2E_LINEAGE_FAILING";
const ORDERS_ID = "model.sherpa.fct_orders";

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

test("the whole project shows fct_orders and a source, with no build error", async ({
  page,
}) => {
  await page.goto("/lineage");

  await expect(graphNode(page, "fct_orders")).toBeVisible();
  await expect(graphNode(page, "orders")).toBeVisible();
  await expect(page.getByText(/^\d+ nodes$/)).toBeVisible();
  await expect(
    page.getByText(/No full build recorded|Build status unavailable/),
  ).toBeHidden();
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
  await expect(panel.getByTestId("node-status")).toHaveText("ok");
  await panel.getByRole("tab", { name: "Compiled SQL" }).click();
  const compiled = panel
    .getByRole("tabpanel", { name: "Compiled SQL" })
    .locator(".cm-content");
  await expect(compiled).toContainText("select");
  await expect(compiled).not.toContainText("{{");

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

  await expect(page.getByRole("complementary", { name: "Node details" })).toBeHidden();
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
