import { expect, test } from "@playwright/test";

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
