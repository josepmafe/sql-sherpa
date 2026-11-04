import { expect, test, type Page } from "@playwright/test";

const TEXT_RATIO = 4.5;
const GRAPHIC_RATIO = 3;
const SOURCE_COUNT = 4;

interface Measure {
  what: string;
  ratio: number;
  minimum: number;
}

// The box in two states: warnings only, and errors; each with enough items to fold, so the button exists.
const VARIANTS = {
  warnings: { failingTests: 0, failedNodes: 0, warnings: SOURCE_COUNT },
  errors: { failingTests: 3, failedNodes: 1, warnings: SOURCE_COUNT - 2 },
} as const;

for (const scheme of ["light", "dark"] as const) {
  for (const [variant, setup] of Object.entries(VARIANTS)) {
    test(`the data-problems box (${variant}) keeps AA contrast in the ${scheme} theme`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await page.route("**/api/home", async (route) => {
        const response = await route.fetch();
        const body = (await response.json()) as {
          status: { build: Record<string, number>; freshness: { problems: unknown[] } };
        };
        Object.assign(body.status.build, { failing_tests: setup.failingTests, failed_nodes: setup.failedNodes });
        body.status.freshness.problems = Array.from({ length: setup.warnings }, (_, index) => ({
          name: `shop.source_${index}`,
          status: "warn",
          hours_ago: 49,
          max_loaded_at: "2026-10-30T12:00:00Z",
        }));
        await route.fulfill({ response, json: body });
      });
      await page.goto("/");
      await page.getByRole("region", { name: "Data problems" }).waitFor();

      const measures = await measureBox(page);

      expect(measures.map(({ what }) => what)).toEqual(
        expect.arrayContaining(["title", "icon", "button", ...(setup.failingTests > 0 ? ["link"] : [])]),
      );
      for (const { what, ratio, minimum } of measures) {
        expect(ratio, `${what} in the ${scheme} theme, ${variant}`).toBeGreaterThanOrEqual(minimum);
      }
    });
  }
}

// Contrast of the rendered colours with every translucent layer composited, so a tint is part of the background.
async function measureBox(page: Page): Promise<Measure[]> {
  return page.getByRole("region", { name: "Data problems" }).evaluate(
    (box, { text, graphic }) => {
      const canvas = document.createElement("canvas").getContext("2d")!;
      type Rgba = [number, number, number, number];
      const parse = (css: string): Rgba => {
        canvas.clearRect(0, 0, 1, 1);
        canvas.fillStyle = "#000";
        canvas.fillStyle = css;
        canvas.fillRect(0, 0, 1, 1);
        const [r, g, b, a] = canvas.getImageData(0, 0, 1, 1).data;
        return [r!, g!, b!, a! / 255];
      };
      const over = (top: Rgba, below: Rgba): Rgba => {
        const alpha = top[3] + below[3] * (1 - top[3]);
        const mix = (i: 0 | 1 | 2) => (top[i] * top[3] + below[i] * below[3] * (1 - top[3])) / alpha;
        return [mix(0), mix(1), mix(2), alpha];
      };
      const backgroundOf = (element: Element): Rgba => {
        const layers: Rgba[] = [];
        for (let node: Element | null = element; node !== null; node = node.parentElement) {
          layers.push(parse(getComputedStyle(node).backgroundColor));
        }
        return layers.reduceRight((below, layer) => over(layer, below), [255, 255, 255, 1] as Rgba);
      };
      const luminance = ([r, g, b]: Rgba): number => {
        const [lr, lg, lb] = [r, g, b].map((channel) => {
          const value = channel / 255;
          return value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
        });
        return 0.2126 * lr! + 0.7152 * lg! + 0.0722 * lb!;
      };
      // An icon is drawn with its stroke; text with its colour.
      const ratio = (element: Element): number => {
        const background = backgroundOf(element);
        const foreground = over(parse(element instanceof SVGElement ? getComputedStyle(element).stroke : getComputedStyle(element).color), background);
        const [light, dark] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
        return (light! + 0.05) / (dark! + 0.05);
      };
      const found: [string, Element, number][] = [];
      const title = box.querySelector(".mantine-Alert-title");
      const icon = box.querySelector(".mantine-Alert-icon svg");
      const button = box.querySelector("button");
      if (title) found.push(["title", title, text]);
      if (icon) found.push(["icon", icon, graphic]);
      if (button) found.push(["button", button, text]);
      for (const link of box.querySelectorAll("a")) found.push(["link", link, text]);
      for (const item of box.querySelectorAll("li")) found.push(["item", item, text]);
      return found.map(([what, element, minimum]) => ({ what, ratio: ratio(element), minimum }));
    },
    { text: TEXT_RATIO, graphic: GRAPHIC_RATIO },
  );
}
