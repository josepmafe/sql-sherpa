import { expect, test } from "@playwright/test";

import { runQuery } from "./warehouse";

// UI spec §8: no runtime fetch leaves the app's own origin, so no CDN, font or icon request exists.
test("Explorer works with every request outside the app's origin blocked", async ({ page, baseURL }) => {
  const origin = new URL(baseURL as string).origin;
  const blocked: string[] = [];
  await page.route("**/*", (route) => {
    const url = route.request().url();
    if (new URL(url).origin === origin) return route.continue();
    blocked.push(url);
    return route.abort();
  });

  await page.goto("/explorer");
  await runQuery(page, "select count(*) from marts.fct_orders");

  await expect(page.getByText(/^1 rows in /)).toBeVisible();
  expect(blocked).toEqual([]);
});
