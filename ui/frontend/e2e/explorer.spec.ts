import { expect, test } from "@playwright/test";

import { adminScalar, runQuery } from "./warehouse";

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
];

test.beforeEach(async ({ page }) => {
  await page.goto("/explorer");
});

test("tree lists the nine marts and the Inspector shows the grain", async ({ page }) => {
  for (const mart of MARTS) {
    await expect(page.getByRole("button", { name: mart, exact: true })).toBeVisible();
  }

  await page.getByRole("button", { name: "fct_orders", exact: true }).click();

  await expect(page.getByText("Grain:")).toBeVisible();
  await expect(page.getByRole("heading", { name: "fct_orders" })).toBeVisible();
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
  await header.click();
  await header.click();

  await expect(header).toHaveAttribute("aria-sort", "descending");
  await expect(page.locator("tbody tr").first().getByRole("cell").first()).toHaveText(maxId);
});

test("a long aggregate is cancelled with the button", async ({ page }) => {
  await runQuery(
    page,
    "select count(*) from marts.fct_order_items a cross join marts.fct_order_items b cross join marts.fct_order_items c",
  );

  await page.getByRole("button", { name: "Cancel" }).first().click();

  await expect(page.getByText(/Cancelled after/)).toBeVisible();
});

test("Preview 100 rows runs from the table menu", async ({ page }) => {
  await page.getByRole("button", { name: "Actions for fct_orders" }).click();
  await page.getByRole("menuitem", { name: "Preview 100 rows" }).click();

  await expect(page.getByText(/^100 rows in /)).toBeVisible();
});
