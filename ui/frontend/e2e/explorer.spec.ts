import { expect, test } from "@playwright/test";

import { adminScalar, runQuery } from "./warehouse";

// The UI's login, which `make e2e` passes from .env (compose's default otherwise).
const UI_USER = process.env.SHERPA_UI_USER ?? "sherpa_ui";
const SESSION_GONE_MS = 5_000;

const MARTS = [
  "dim_customers",
  "dim_date",
  "dim_products",
  "dim_promotions",
  "fct_order_discounts",
  "fct_order_items",
  "fct_orders",
  "fct_refunds",
  "fct_returns",
  "fct_sessions",
];

test.beforeEach(async ({ page }) => {
  await page.goto("/explorer");
});

test("tree lists every mart and the Inspector shows the grain", async ({ page }) => {
  for (const mart of MARTS) {
    await expect(page.getByRole("button", { name: mart, exact: true })).toBeVisible();
  }

  await page.getByRole("button", { name: "fct_orders", exact: true }).click();

  // At 1280 px the Inspector has its own column (the hover tooltip also says "Grain:").
  const inspector = page.getByRole("complementary", { name: "Table details" });
  await expect(inspector.getByText("Grain:")).toBeVisible();
  await expect(inspector.getByRole("heading", { name: "fct_orders" })).toBeVisible();
});

test("a count matches the admin psql count", async ({ page }) => {
  const expected = Number(adminScalar("select count(*) from marts.fct_orders"));

  await runQuery(page, "select count(*) from marts.fct_orders");

  await expect(page.getByRole("cell", { name: String(expected), exact: true })).toBeVisible();
});

test("the guard shows its reason for a delete", async ({ page }) => {
  await runQuery(page, "delete from marts.fct_orders");

  await expect(page.getByText("Query not allowed")).toBeVisible();
  await expect(page.getByText("the statement must be a SELECT query, not DELETE")).toBeVisible();
});

test("a truncated result sorts on the server", async ({ page }) => {
  const maxId = adminScalar("select max(order_item_id) from marts.fct_order_items");

  await runQuery(page, "select * from marts.fct_order_items");
  await expect(page.getByText("truncated", { exact: true })).toBeVisible();
  await expect(page.getByText(/Truncated at .* rows: sorting and filtering run on the server/)).toBeVisible();

  const header = page.getByRole("columnheader", { name: "order_item_id" });
  await header.getByRole("button").click();
  await header.getByRole("button").click();

  await expect(header).toHaveAttribute("aria-sort", "descending");
  await expect(page.locator("tbody tr").first().getByRole("cell").first()).toHaveText(maxId);
});

test("a small result sorts in the browser without another request", async ({ page }) => {
  const sql = "select order_id from marts.fct_orders order by order_id limit 20";
  const maxId = adminScalar(`select max(order_id) from (${sql}) as t`);
  const queryPosts: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && new URL(request.url()).pathname === "/api/queries") queryPosts.push(request.url());
  });

  await runQuery(page, sql);
  await expect(page.getByText(/^20 rows in /)).toBeVisible();
  const header = page.getByRole("columnheader", { name: "order_id" });
  await header.getByRole("button").click();
  await header.getByRole("button").click();

  await expect(header).toHaveAttribute("aria-sort", "descending");
  await expect(page.locator("tbody tr").first().getByRole("cell").first()).toHaveText(maxId);
  expect(queryPosts).toHaveLength(1);
});

test("a long aggregate is cancelled with the button and its session ends", async ({ page }) => {
  // The health checks' short sessions come and go, so the baseline is the lowest of a few counts.
  const before = Math.min(uiSessions(), uiSessions(), uiSessions());
  await runQuery(
    page,
    "select count(*) from marts.fct_order_items a cross join marts.fct_order_items b cross join marts.fct_order_items c",
  );
  await expect.poll(uiSessions).toBeGreaterThan(before); // the query holds a connection

  await page.getByRole("button", { name: "Cancel" }).first().click();

  await expect(page.getByText(/Cancelled after/)).toBeVisible();
  await expect.poll(uiSessions, { timeout: SESSION_GONE_MS }).toBeLessThanOrEqual(before);
});

test("Preview 100 rows runs from the table menu", async ({ page }) => {
  await page.getByRole("button", { name: "Actions for fct_orders" }).click();
  await page.getByRole("menuitem", { name: "Preview 100 rows" }).click();

  await expect(page.getByText(/^100 rows in /)).toBeVisible();
});

// By user name only: Postgres shows the admin the state and query of another role's sessions as NULL.
function uiSessions(): number {
  return Number(adminScalar(`select count(*) from pg_stat_activity where usename = '${UI_USER.replaceAll("'", "''")}'`));
}
