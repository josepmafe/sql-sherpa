import { expect, type Page, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.goto("/explorer");
});

test.describe("at 1440 x 900", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  // Two open tables make the tree scroll; its scrollbar must not sit over the right-aligned "⋯" buttons.
  test("a real click on a table's ⋯ opens its menu while the tree scrolls", async ({ page }) => {
    await page.getByRole("button", { name: "fct_orders", exact: true }).click();
    await page.getByRole("button", { name: "fct_order_items", exact: true }).click();

    await page.getByRole("button", { name: "Actions for dim_customers" }).click({ timeout: 5_000 });

    await expect(page.getByRole("menuitem", { name: "Preview 100 rows" })).toBeVisible();
  });
});

test.describe("at 1024 x 700", () => {
  test.use({ viewport: { width: 1024, height: 700 } });

  test("the editor keeps a usable width, its buttons one row and History its label", async ({ page }) => {
    const editor = await page.locator(".cm-editor").boundingBox();
    const tops = await Promise.all(
      ["Run", "Run selection", "Format"].map(async (name) => (await page.getByRole("button", { name, exact: true }).boundingBox())?.y),
    );
    const history = page.getByRole("button", { name: /^History/ });

    expect(editor?.width).toBeGreaterThanOrEqual(480);
    expect(new Set(tops).size).toBe(1);
    expect(await history.evaluate((button) => button.scrollWidth <= button.clientWidth)).toBe(true);
  });

  test("the Inspector opens as a drawer over the editor and closes with Escape", async ({ page }) => {
    await page.getByRole("button", { name: "fct_orders", exact: true }).click();

    const drawer = page.getByRole("dialog", { name: "Table details" });
    await expect(drawer.getByText("Grain:")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
  });

  test("opening the Lineage node panel keeps the selected node inside the canvas", async ({ page }) => {
    await expectSelectedNodeInCanvas(page);
  });
});

// The panel sits below the canvas and the canvas clips the graph, so neither the bands nor the panel may widen the page.
test.describe("Lineage at 1280 x 800", () => {
  test.use({ viewport: { width: 1280, height: 800 } });

  test("the whole project's bands do not scroll the page sideways and the panel stays under the canvas", async ({ page }) => {
    await page.goto("/lineage?node=model.sherpa.fct_orders");
    const panel = page.getByRole("complementary", { name: "Node details" });
    await expect(panel.getByRole("heading", { name: "fct_orders", level: 4 })).toBeVisible();

    const canvas = await page.getByLabel("Lineage graph").boundingBox();
    const panelBox = await panel.boundingBox();
    const root = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));

    expect(root.scrollWidth).toBeLessThanOrEqual(root.clientWidth);
    expect(canvas!.x + canvas!.width).toBeLessThanOrEqual(root.clientWidth);
    expect(panelBox!.x + panelBox!.width).toBeLessThanOrEqual(root.clientWidth);
    expect(panelBox!.y).toBeGreaterThanOrEqual(canvas!.y + canvas!.height);
  });

  test("neither the canvas nor the node panel overflows the page", async ({ page }) => {
    const widths = await lineageWidths(page);

    expect(widths.scrollWidth).toBeLessThanOrEqual(widths.clientWidth);
    expect(widths.panelRight).toBeLessThanOrEqual(widths.clientWidth);
    expect(widths.canvasRight).toBeLessThanOrEqual(widths.clientWidth);
  });

  test("opening the node panel keeps the selected node inside the canvas", async ({ page }) => {
    await expectSelectedNodeInCanvas(page);
  });
});

// The panel narrows the canvas under a viewport that was fitted to the full width: the node picked must not end up
// behind the panel.
async function expectSelectedNodeInCanvas(page: Page): Promise<void> {
  await page.goto("/lineage");
  await page.getByRole("button", { name: /^fct_orders(?!\w)/ }).click();
  await expect(page.getByRole("complementary", { name: "Node details" })).toBeVisible();

  await expect
    .poll(async () => {
      const canvas = await page.getByLabel("Lineage graph").boundingBox();
      const node = await page.locator('[data-id="model.sherpa.fct_orders"]').boundingBox();
      if (canvas === null || node === null) return false;
      return (
        node.x >= canvas.x &&
        node.y >= canvas.y &&
        node.x + node.width <= canvas.x + canvas.width &&
        node.y + node.height <= canvas.y + canvas.height
      );
    })
    .toBe(true);
}

async function lineageWidths(page: Page): Promise<Record<"canvasRight" | "panelRight" | "scrollWidth" | "clientWidth", number>> {
  await page.goto("/lineage?select=%2Bfct_orders%2B&node=model.sherpa.fct_orders");
  const panel = page.getByRole("complementary", { name: "Node details" });
  await expect(panel.getByRole("heading", { name: "fct_orders", level: 4 })).toBeVisible();
  const canvasBox = await page.getByLabel("Lineage graph").boundingBox();
  const panelBox = await panel.boundingBox();
  const root = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  return {
    canvasRight: (canvasBox?.x ?? 0) + (canvasBox?.width ?? 0),
    panelRight: (panelBox?.x ?? 0) + (panelBox?.width ?? 0),
    ...root,
  };
}
