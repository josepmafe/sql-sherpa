import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

import { formatBuildStart } from "../src/components/time";
import { describeChange, formatChange, formatCurrency, formatValue, verdictWord } from "../src/screens/home/format";
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
  net_revenue: /^€\d{1,3}(,\d{3})*$/,
  orders: /^\d{1,3}(,\d{3})*$/,
  aov: /^€\d{1,3}(,\d{3})*\.\d{2}$/,
  refund_rate: /^\d+\.\d%$/,
  new_customers: /^\d{1,3}(,\d{3})*$/,
};

test("each tile shows the value and the change the warehouse gives", async ({ page }) => {
  expect(expected.tiles).toHaveLength(5);

  for (const tile of expected.tiles) {
    const card = page.getByRole("region", { name: tile.title, exact: true });

    const shape = TILE_SHAPES[tile.id];
    expect(shape, `a shape for the ${tile.id} tile`).toBeDefined();
    await expect(card.locator("p").nth(1)).toHaveText(shape!);
    await expect(card).toContainText(formatValue(tile));
    await expect(card).toContainText(formatChange(tile));
    await expect(card).toContainText(describeChange(tile));
    const word = verdictWord(tile);
    if (word !== null) await expect(card.getByText(word, { exact: true })).toBeVisible();
  }
});

test("the heading states the window and the as-of date", async ({ page }) => {
  await expect(page.getByRole("heading", { level: 2, name: "Last 30 days" })).toBeVisible();
  await expect(page.getByText(`to ${expected.asOf}, compared with the 30 days before`)).toBeVisible();
});

test("every tile has an info button", async ({ page }) => {
  for (const tile of expected.tiles) {
    await expect(page.getByRole("button", { name: `About ${tile.title}`, exact: true })).toBeVisible();
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
    await expect(page.getByTestId("trend-chart").locator("svg.recharts-surface")).toBeAttached();

    const stops = await page.getByTestId("trend-chart").evaluate((chart) =>
      [...chart.querySelectorAll<HTMLElement>("[tabindex], a[href], button, input, select, textarea")].filter(
        (element) => element.tabIndex >= 0,
      ).length,
    );

    expect(stops).toBe(0);
  });

  test("names in its legend only the unusual kinds of week it draws", async ({ page }) => {
    const legend = page.getByRole("list", { name: "Legend" });

    await expect(legend.getByText("Partial week", { exact: true })).toHaveCount(expected.trendHasPartialWeek ? 1 : 0);
    await expect(legend.getByText("Black Friday week", { exact: true })).toHaveCount(expected.trendHasBlackFriday ? 1 : 0);
    await expect(legend.getByText("Net revenue", { exact: true })).toHaveCount(0);
  });
});

test.describe("the data-status footer and the data-problems box", () => {
  test("the footer shows the latest full prod build, and the failing tests only when there are none", async ({ page }) => {
    const footer = page.getByRole("group", { name: "Data status" });

    await expect(footer).toContainText(
      `Last full build ${formatBuildStart(expected.build.startedAt)} · ${expected.build.nodeCount} nodes`,
    );
    if (expected.build.failingTests === 0) {
      await expect(footer.getByText("No failing tests")).toBeVisible();
    } else {
      await expect(footer.getByText(/failing tests?/)).toHaveCount(0);
    }
  });

  test("the box lists the failed nodes and failing tests of the build, with links", async ({ page }) => {
    const { failedNodes, failingTests } = expected.build;
    const box = page.getByRole("region", { name: "Data problems" });

    if (failedNodes === 0) {
      await expect(page.getByText(/failed nodes?$/)).toHaveCount(0);
    } else {
      await expect(box.getByRole("link", { name: `${failedNodes} failed ${failedNodes === 1 ? "node" : "nodes"}` })).toBeVisible();
    }
    if (failingTests > 0) {
      await expect(box.getByRole("link", { name: `${failingTests} failing ${failingTests === 1 ? "test" : "tests"}` })).toBeVisible();
    }
  });

  test("shows every warn or error source of sources.json in the box, or that they are fresh in the footer", async ({ page }) => {
    const document = JSON.parse(readFileSync(SOURCES_FILE, "utf8")) as SourcesDocument;
    const problems = document.results.filter((row) => ["warn", "error", "runtime error"].includes(row.status));
    const checked = formatBuildStart(document.metadata.generated_at);

    if (problems.length === 0) {
      await expect(page.getByRole("group", { name: "Data status" })).toContainText(`Sources fresh (checked ${checked})`);
    } else {
      await expect(page.getByRole("group", { name: "Data status" })).not.toContainText("Sources fresh");
    }
    const box = page.getByRole("region", { name: "Data problems" });
    // The first three are visible; a fold hides the rest until it is opened.
    if (problems.length > 3) await box.getByRole("button", { name: /^Show \d+ more$/ }).click();
    for (const row of problems) {
      const name = row.unique_id.split(".").slice(SOURCE_PREFIX_PARTS).join(".");
      const word = row.status === "warn" ? "warning" : "error";
      const loaded =
        row.max_loaded_at_time_ago_in_s == null
          ? "never loaded"
          : `last loaded ${Math.floor(row.max_loaded_at_time_ago_in_s / SECONDS_PER_HOUR)} h ago`;

      await expect(box).toContainText(`${name}: ${word}, ${loaded} (checked ${checked})`);
    }
  });

  test("shows no box when nothing is a problem", async ({ page }) => {
    await page.route("**/api/home", async (route) => {
      const response = await route.fetch();
      const body = (await response.json()) as {
        status: {
          build: { failing_tests: number; failed_nodes: number } | null;
          freshness: { problems: unknown[] } | null;
        };
      };
      if (body.status.build !== null) Object.assign(body.status.build, { failing_tests: 0, failed_nodes: 0 });
      if (body.status.freshness !== null) body.status.freshness.problems = [];
      await route.fulfill({ response, json: body });
    });
    await page.reload();

    await expect(page.getByRole("group", { name: "Data status" })).toContainText("Sources fresh");
    await expect(page.getByRole("region", { name: "Data problems" })).toHaveCount(0);
  });

  // The warehouse may hold no failing test, so this one forces a count of 3 in the answer and follows the real link.
  test("the failing-tests link in the box opens Lineage with Failing only checked", async ({ page }) => {
    await page.route("**/api/home", async (route) => {
      const response = await route.fetch();
      const body = (await response.json()) as { status: { build: { failing_tests: number } | null } };
      if (body.status.build !== null) body.status.build.failing_tests = 3;
      await route.fulfill({ response, json: body });
    });
    await page.reload();

    await page.getByRole("region", { name: "Data problems" }).getByRole("link", { name: "3 failing tests" }).click();

    await expect(page).toHaveURL(/\/lineage\?failing=1$/);
    await expect(page.getByRole("switch", { name: "Failing only" })).toBeChecked();
  });
});
