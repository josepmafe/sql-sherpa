import { expect, test } from "@playwright/test";

import { adminScalar, make, runQuery } from "./warehouse";

const BUILD_TIMEOUT_MS = 15 * 60_000;
const MS_PER_DAY = 24 * 60 * 60 * 1000;

// Criterion 2: a rebuilt warehouse shows up on reload without restarting `ui`. It simulates one more day and rebuilds
// prod in the warehouse every checkout shares, which only `make reset` undoes, so it runs only when asked for
// (`make e2e-freshness`), and only while the data ends before yesterday (UTC).
test("a new build shows after a reload without restarting ui", async ({ page }) => {
  test.skip(!process.env.SHERPA_E2E_FRESHNESS, "changes the shared warehouse: run it with make e2e-freshness");
  test.setTimeout(BUILD_TIMEOUT_MS);
  const lastDay = /\.\. (\d{4}-\d{2}-\d{2})/.exec(make("status"))?.[1] ?? "";
  const yesterday = new Date(Date.now() - MS_PER_DAY).toISOString().slice(0, 10);
  test.skip(lastDay >= yesterday, "the warehouse already runs through yesterday, so there is no day left to add");

  const before = adminScalar("select count(*) from marts.fct_orders");
  await page.goto("/explorer");
  await runQuery(page, "select count(*) from marts.fct_orders");
  await expect(page.getByRole("cell", { name: before, exact: true })).toBeVisible();

  make("generate", "DAYS=1");
  make("build-prod");
  const after = adminScalar("select count(*) from marts.fct_orders");
  expect(after).not.toBe(before);

  await page.reload();
  await runQuery(page, "select count(*) from marts.fct_orders");
  await expect(page.getByRole("cell", { name: after, exact: true })).toBeVisible();
});
