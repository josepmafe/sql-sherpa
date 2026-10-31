import { expect, test } from "@playwright/test";

import { blockForeignRequests } from "./helpers";
import { runQuery } from "./warehouse";

// UI spec §8: no runtime fetch leaves the app's own origin, so no CDN, font or icon request exists.
test("Explorer works with every request outside the app's origin blocked", async ({ page, baseURL }) => {
  const blocked = await blockForeignRequests(page, baseURL);

  await page.goto("/explorer");
  await runQuery(page, "select count(*) from marts.fct_orders");

  await expect(page.getByText(/^1 row in /)).toBeVisible();
  expect(blocked).toEqual([]);
});

test("Lineage works with every request outside the app's origin blocked", async ({ page, baseURL }) => {
  const blocked = await blockForeignRequests(page, baseURL);

  await page.goto("/lineage");
  await page.getByRole("button", { name: /^fct_orders(?!\w)/ }).click();

  const panel = page.getByRole("complementary", { name: "Node details" });
  await expect(panel.getByRole("heading", { name: "fct_orders", level: 4 })).toBeVisible();
  expect(blocked).toEqual([]);
});
