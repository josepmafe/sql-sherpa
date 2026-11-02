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

  test("the Inspector opens as an overlay without moving the editor, and closes with Escape", async ({ page }) => {
    const editorBefore = await page.locator(".cm-editor").first().boundingBox();

    await page.getByRole("button", { name: "fct_orders", exact: true }).click();

    const overlay = page.getByRole("complementary", { name: "Table details" });
    await expect(overlay.getByText("Grain:")).toBeVisible();
    await expect(overlay.getByRole("heading", { name: "Table details" })).toBeFocused();
    expect(await page.locator(".cm-editor").first().boundingBox()).toEqual(editorBefore);
    await page.keyboard.press("Escape");
    await expect(overlay).toBeHidden();
    await expect(page.getByRole("button", { name: "fct_orders", exact: true })).toBeFocused();
  });

  test("a layout stored by a wider window does not break this one", async ({ page }) => {
    await page.evaluate(() =>
      localStorage.setItem(
        "sherpa.v1.layout.explorer",
        JSON.stringify({ sizes: { tree: 480, inspector: 520 }, collapsed: {} }),
      ),
    );
    await page.reload();

    const editor = await page.locator(".cm-editor").first().boundingBox();
    const root = await page.evaluate(() => ({
      scrollWidth: document.documentElement.scrollWidth,
      clientWidth: document.documentElement.clientWidth,
    }));
    // The centre pane's minimum (360 px) less its padding.
    expect(editor!.width).toBeGreaterThanOrEqual(340);
    expect(root.scrollWidth).toBeLessThanOrEqual(root.clientWidth);
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

  test("the screen fills the viewport without a page scrollbar and the canvas keeps its 240 px", async ({ page }) => {
    await page.evaluate(() => localStorage.removeItem("sherpa.v1.layout.lineage"));
    await page.goto("/lineage?node=model.sherpa.fct_orders");
    await expect(page.getByRole("complementary", { name: "Node details" }).getByRole("heading", { level: 4 })).toBeVisible();

    const canvas = await page.getByLabel("Lineage graph").boundingBox();
    const root = await page.evaluate(() => ({
      scrollHeight: document.documentElement.scrollHeight,
      clientHeight: document.documentElement.clientHeight,
    }));

    expect(root.scrollHeight).toBeLessThanOrEqual(root.clientHeight);
    // A rounding of the layout's own 240 px.
    expect(canvas!.height).toBeGreaterThan(239);
  });

  test("selecting a node that the folded panel's opening would hide keeps it in the canvas", async ({ page }) => {
    await page.evaluate(() => localStorage.removeItem("sherpa.v1.layout.lineage"));
    await page.goto("/lineage");
    await page.getByRole("button", { name: "Collapse the node panel" }).click();
    await expect(page.locator(".react-flow__node-graph").first()).toBeVisible();
    // The lowest node of the graph, the one the shrinking canvas hides first.
    const lowest = await page.locator(".react-flow__node-graph").evaluateAll((nodes) => {
      const bottoms = nodes.map((node) => ({ id: node.getAttribute("data-id")!, bottom: node.getBoundingClientRect().bottom }));
      return bottoms.sort((a, b) => b.bottom - a.bottom)[0]!.id;
    });

    await page.locator(`[data-id="${lowest}"] button`).first().click();

    await expect(page.getByRole("button", { name: "Collapse the node panel" })).toBeVisible();
    await expect
      .poll(async () => {
        const canvas = (await page.getByLabel("Lineage graph").boundingBox())!;
        const node = (await page.locator(`[data-id="${lowest}"]`).boundingBox())!;
        return node.y >= canvas.y && node.y + node.height <= canvas.y + canvas.height;
      })
      .toBe(true);
  });

  test("dragging the handle 100 px up and down leaves the graph's pan and zoom and the selected node in view", async ({
    page,
  }) => {
    await page.evaluate(() => localStorage.removeItem("sherpa.v1.layout.lineage"));
    await page.goto("/lineage");
    await page.getByRole("button", { name: /^fct_orders(?!\w)/ }).click();
    await expect(page.getByRole("complementary", { name: "Node details" }).getByRole("heading", { level: 4 })).toBeVisible();
    const viewport = page.locator(".react-flow__viewport");
    const transform = await viewport.evaluate((element) => (element as HTMLElement).style.transform);
    const canvasBefore = (await page.getByLabel("Lineage graph").boundingBox())!;

    // At this height the canvas starts at its minimum, so the handle goes down first, then back up.
    let offset = 0;
    for (const dy of [100, -100]) {
      const box = (await page.getByRole("separator", { name: "Resize the graph and the node panel" }).boundingBox())!;
      const x = box.x + box.width / 2;
      const y = box.y + box.height / 2;
      await page.mouse.move(x, y);
      await page.mouse.down();
      await page.mouse.move(x, y + dy, { steps: 8 });
      await page.mouse.up();
      offset += dy;
      const canvas = (await page.getByLabel("Lineage graph").boundingBox())!;

      expect(Math.abs(canvas.height - (canvasBefore.height + offset))).toBeLessThan(6);
      expect(await viewport.evaluate((element) => (element as HTMLElement).style.transform)).toBe(transform);
      expect(await selectedNodeInCanvas(page)).toBe(true);
    }
  });
});

// The panel narrows the canvas under a viewport that was fitted to the full width: the node picked must not end up
// behind the panel.
async function expectSelectedNodeInCanvas(page: Page): Promise<void> {
  await page.goto("/lineage");
  await page.getByRole("button", { name: /^fct_orders(?!\w)/ }).click();
  await expect(page.getByRole("complementary", { name: "Node details" })).toBeVisible();

  await expect.poll(() => selectedNodeInCanvas(page)).toBe(true);
}

async function selectedNodeInCanvas(page: Page): Promise<boolean> {
  const canvas = await page.getByLabel("Lineage graph").boundingBox();
  const node = await page.locator('[data-id="model.sherpa.fct_orders"]').boundingBox();
  if (canvas === null || node === null) return false;
  return (
    node.x >= canvas.x &&
    node.y >= canvas.y &&
    node.x + node.width <= canvas.x + canvas.width &&
    node.y + node.height <= canvas.y + canvas.height
  );
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

test.describe("the navigation rail at 1440 x 900", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  test("stays collapsed after a reload, and hovering it opens the labels without moving the editor", async ({ page }) => {
    await page.evaluate(() => localStorage.removeItem("sherpa.v1.layout.shell"));
    await page.reload();
    await page.getByRole("button", { name: "Collapse the navigation" }).click();
    await page.reload();
    const rail = page.getByRole("navigation", { name: "Main" });
    const link = page.getByRole("link", { name: "Lineage" });
    await expect(page.getByRole("button", { name: "Open the navigation" })).toBeVisible();
    await expect(link).not.toHaveText("Lineage");
    const editorBefore = await page.locator(".cm-editor").boundingBox();
    const railBefore = await rail.boundingBox();

    await rail.hover();

    await expect(link).toHaveText("Lineage");
    expect((await rail.boundingBox())!.width).toBeGreaterThan(railBefore!.width);
    expect((await page.locator(".cm-editor").boundingBox())!.x).toBe(editorBefore!.x);
  });
});

test.describe("the Explorer panes", () => {
  const widths = async (page: Page) => ({
    tree: (await page
      .getByRole("button", { name: "Collapse the table tree" })
      .locator("xpath=ancestor::*[@data-panel]")
      .boundingBox())!.width,
    editor: (await page.locator(".cm-editor").first().boundingBox())!.width,
  });
  const dragBy = async (page: Page, name: string, dx: number, dy = 0) => {
    const box = (await page.getByRole("separator", { name }).boundingBox())!;
    expect(Math.min(box.width, box.height)).toBe(6);
    const x = box.x + box.width / 2;
    const y = box.y + box.height / 2;
    await page.mouse.move(x, y);
    await page.mouse.down();
    await page.mouse.move(x + dx, y + dy, { steps: 8 });
    await page.mouse.up();
  };

  for (const viewport of [
    { width: 1280, height: 800 },
    { width: 1440, height: 900 },
  ]) {
    test.describe(`at ${viewport.width} x ${viewport.height}`, () => {
      test.use({ viewport });

      test.beforeEach(async ({ page }) => {
        await page.evaluate(() => localStorage.removeItem("sherpa.v1.layout.explorer"));
        await page.reload();
      });

      test("dragging the tree's handle by 100 px widens the tree by about 100 px, and a reload keeps it", async ({
        page,
      }) => {
        const before = await widths(page);

        await dragBy(page, "Resize the tree", 100);

        const after = await widths(page);
        expect(Math.abs(after.tree - before.tree - 100)).toBeLessThan(6);
        await page.reload();
        expect(Math.abs((await widths(page)).tree - after.tree)).toBeLessThan(2);
      });

      test("the largest stored tree and Inspector still leave the editor its minimum width and the page no scrollbar", async ({
        page,
      }) => {
        await page.evaluate(() =>
          localStorage.setItem(
            "sherpa.v1.layout.explorer",
            JSON.stringify({ sizes: { tree: 480, inspector: 520 }, collapsed: {} }),
          ),
        );
        await page.reload();

        const editor = await page.locator(".cm-editor").first().boundingBox();
        const root = await page.evaluate(() => ({
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: document.documentElement.clientWidth,
        }));
        expect(editor!.width).toBeGreaterThanOrEqual(340);
        expect(root.scrollWidth).toBeLessThanOrEqual(root.clientWidth);
      });

      test("the tree folds to a strip that opens over the editor on hover without moving it", async ({ page }) => {
        await page.getByRole("button", { name: "Collapse the table tree" }).click();
        await page.reload();
        const editorBefore = await page.locator(".cm-editor").first().boundingBox();
        await expect(page.getByRole("button", { name: "fct_orders", exact: true })).toBeHidden();

        await page.getByRole("button", { name: "Open the table tree" }).hover();
        await expect(page.getByRole("button", { name: "fct_orders", exact: true })).toBeVisible();

        expect(await page.locator(".cm-editor").first().boundingBox()).toEqual(editorBefore);
        await page.getByRole("button", { name: "fct_orders", exact: true }).click();
        await expect(page.getByRole("complementary", { name: "Table details" }).getByText("Grain:")).toBeVisible();
        await page.mouse.move(viewport.width - 5, viewport.height - 5);
        await expect(page.getByRole("button", { name: "fct_orders", exact: true })).toBeHidden();
      });

      test("a second worksheet's editor fills its pane and its handle moves it", async ({ page }) => {
        await page.getByRole("button", { name: "New worksheet" }).click();
        const editor = page.locator("[data-worksheet]:visible .cm-editor");
        await expect(editor).toBeVisible();
        const before = (await editor.boundingBox())!.height;

        await dragBy(page, "Resize the editor and the results", 0, 60);

        expect(before).toBeGreaterThan(100);
        expect((await editor.boundingBox())!.height).toBeGreaterThan(before + 40);
      });

      test("dragging the editor's handle down makes the editor taller, and the editor and its text survive", async ({
        page,
      }) => {
        const editor = page.locator(".cm-editor").first();
        await page.locator(".cm-content").first().fill("select 1");
        const before = (await editor.boundingBox())!.height;

        await dragBy(page, "Resize the editor and the results", 0, 60);

        expect((await editor.boundingBox())!.height).toBeGreaterThan(before + 40);
        await expect(page.locator(".cm-content").first()).toHaveText("select 1");
      });
    });
  }
});
