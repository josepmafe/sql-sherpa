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

test("Lineage column focus works with every request outside the app's origin blocked", async ({ page, baseURL }) => {
  const blocked = await blockForeignRequests(page, baseURL);

  await page.goto("/lineage?select=%2Bfct_orders%2B");
  await page.getByRole("button", { name: "Columns of fct_orders" }).click();
  const row = page.locator('[data-id="model.sherpa.fct_orders"] button[title="net_revenue"]');
  await row.focus();
  await row.click();

  await expect(row).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator('[data-id="source.sherpa.shop.orders"] button[title="status"]')).toBeVisible();
  const panel = page.getByRole("complementary", { name: "Node details" });
  await expect(panel.getByRole("heading", { name: "fct_orders", level: 4 })).toBeVisible();
  expect(blocked).toEqual([]);
});
