import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

import { formatBuildStart } from "../src/components/time";
import { describeChange, formatChange, formatCurrency, formatValue } from "../src/screens/home/format";
import { homeExpectations, type HomeExpectations } from "./warehouse";

const SOURCES_FILE = resolve(import.meta.dirname, "../../../data-platform/dbt/target-prod/sources.json");
const SECONDS_PER_HOUR = 3600;
const SOURCE_PREFIX_PARTS = 2;

interface SourcesDocument {
  metadata: { generated_at: string };
  results: { unique_id: string; status: string; max_loaded_at_time_ago_in_s?: number | null }[];
}

let expected: HomeExpectations;

test.beforeAll(() => {
  expected = homeExpectations();
});

test.beforeEach(async ({ page }) => {
  await page.goto("/");
});

// A value of the right shape, whatever the warehouse holds: the expected strings in the test come from the same formatter.
const TILE_SHAPES: Record<string, RegExp> = {
  net_revenue: /^€[\d,]+$/,
  orders: /^[\d,]+$/,
  aov: /^€[\d,]+\.\d{2}$/,
  refund_rate: /^\d+\.\d%$/,
  new_customers: /^[\d,]+$/,
};

test("each tile shows the value and the change the warehouse gives", async ({ page }) => {
  expect(expected.tiles).toHaveLength(5);

  for (const tile of expected.tiles) {
    const card = page.getByRole("region", { name: tile.title, exact: true });

    await expect(card.locator("p").nth(1)).toHaveText(TILE_SHAPES[tile.id]!);
    await expect(card).toContainText(formatValue(tile));
    await expect(card).toContainText(formatChange(tile));
    await expect(card).toContainText(describeChange(tile));
  }
});

test("the weekly trend lists the independent week count, and its last week's net revenue matches", async ({ page }) => {
  const rows = page.getByRole("table", { name: "Net revenue per week" }).locator("tbody tr");

  await expect(rows).toHaveCount(expected.trendWeeks);
  await expect(rows.last().getByRole("cell").nth(0)).toHaveText(expected.lastWeekStart);
  await expect(rows.last().getByRole("cell").nth(1)).toHaveText(formatCurrency(expected.lastWeekNetRevenue));
});

test.describe("the trend chart", () => {
  test("is no keyboard stop", async ({ page }) => {
    await expect(page.getByRole("table", { name: "Net revenue per week" })).toBeAttached();

    const stops = await page.getByTestId("trend-chart").evaluate((chart) =>
      [...chart.querySelectorAll<HTMLElement>("[tabindex], a[href], button, input, select, textarea")].filter(
        (element) => element.tabIndex >= 0,
      ).length,
    );

    expect(stops).toBe(0);
  });

  test("lists in its legend only the kinds of week it draws", async ({ page }) => {
    const chart = page.getByTestId("trend-chart");

    await expect(chart.getByText("Net revenue", { exact: true })).toBeVisible();
    await expect(chart.getByText("Partial week", { exact: true })).toHaveCount(expected.trendHasPartialWeek ? 1 : 0);
    await expect(chart.getByText("Black Friday week", { exact: true })).toHaveCount(expected.trendHasBlackFriday ? 1 : 0);
  });
});

test.describe("the data-status strip", () => {
  test("shows the as-of date, the latest full prod build and its failing tests", async ({ page }) => {
    const strip = page.getByRole("group", { name: "Data status" });

    await expect(strip).toContainText(`Data as of ${expected.asOf}`);
    await expect(strip).toContainText(
      `Last full build ${formatBuildStart(expected.build.startedAt)} · ${expected.build.nodeCount} nodes`,
    );
    const { failedNodes, failingTests } = expected.build;
    if (failedNodes === 0) {
      await expect(strip.getByText(/failed nodes?$/)).toHaveCount(0);
    } else {
      await expect(strip.getByRole("link", { name: `${failedNodes} failed ${failedNodes === 1 ? "node" : "nodes"}` })).toBeVisible();
    }
    if (failingTests === 0) {
      await expect(strip.getByText("No failing tests")).toBeVisible();
      await expect(strip.getByRole("link", { name: /failing tests?$/ })).toHaveCount(0);
    } else {
      await expect(strip.getByRole("link", { name: `${failingTests} failing ${failingTests === 1 ? "test" : "tests"}` })).toBeVisible();
    }
  });

  test("shows every warn or error source of sources.json, or that they are fresh", async ({ page }) => {
    const document = JSON.parse(readFileSync(SOURCES_FILE, "utf8")) as SourcesDocument;
    const problems = document.results.filter((row) => ["warn", "error", "runtime error"].includes(row.status));
    const strip = page.getByRole("group", { name: "Data status" });

    if (problems.length === 0) {
      await expect(strip).toContainText(`Sources fresh (checked ${formatBuildStart(document.metadata.generated_at)})`);
    }
    for (const row of problems) {
      const name = row.unique_id.split(".").slice(SOURCE_PREFIX_PARTS).join(".");
      const word = row.status === "warn" ? "warning" : "error";
      const loaded =
        row.max_loaded_at_time_ago_in_s == null
          ? "never loaded"
          : `last loaded ${Math.floor(row.max_loaded_at_time_ago_in_s / SECONDS_PER_HOUR)} h ago`;

      await expect(strip).toContainText(
        `${name}: ${word}, ${loaded} (checked ${formatBuildStart(document.metadata.generated_at)})`,
      );
    }
  });

  // The warehouse may hold no failing test, so this one forces a count of 3 in the answer and follows the real link.
  test("the failing-tests link opens Lineage with Failing only checked", async ({ page }) => {
    await page.route("**/api/home", async (route) => {
      const response = await route.fetch();
      const body = (await response.json()) as { status: { build: { failing_tests: number } | null } };
      if (body.status.build !== null) body.status.build.failing_tests = 3;
      await route.fulfill({ response, json: body });
    });
    await page.reload();

    await page.getByRole("link", { name: "3 failing tests" }).click();

    await expect(page).toHaveURL(/\/lineage\?failing=1$/);
    await expect(page.getByRole("switch", { name: "Failing only" })).toBeChecked();
  });
});
