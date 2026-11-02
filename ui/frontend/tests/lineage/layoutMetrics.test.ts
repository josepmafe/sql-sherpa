import { describe, expect, it } from "vitest";

import { type Curve, defaultBezier, layoutMetrics, type Rect } from "./layoutMetrics";

const line = (source: string, target: string, ...points: [number, number][]): Curve => ({
  source,
  target,
  points: points.map(([x, y]) => ({ x, y })),
});
const rect = (id: string, x: number, y: number, width = 10, height = 10): Rect => ({ id, x, y, width, height });

describe("layoutMetrics", () => {
  it("counts two crossing edges once", () => {
    const curves = [line("a", "b", [0, 0], [100, 100]), line("c", "d", [0, 100], [100, 0])];

    expect(layoutMetrics([], curves).crossings).toBe(1);
  });

  it("counts no crossing between two parallel edges", () => {
    const curves = [line("a", "b", [0, 0], [100, 0]), line("c", "d", [0, 10], [100, 10])];

    expect(layoutMetrics([], curves).crossings).toBe(0);
  });

  it("never counts edges that share a node", () => {
    const curves = [line("a", "b", [0, 0], [100, 100]), line("a", "d", [0, 100], [100, 0])];

    expect(layoutMetrics([], curves).crossings).toBe(0);
  });

  it("counts an edge through a third node as behind it, and not an edge beside it", () => {
    const rects = [rect("a", 0, 0), rect("b", 100, 0), rect("mid", 40, -5, 20, 20)];
    const through = line("a", "b", [10, 5], [50, 5], [100, 5]);
    const beside = line("a", "b", [10, 5], [50, 40], [100, 5]);

    expect(layoutMetrics(rects, [through]).edgesBehindNodes).toBe(1);
    expect(layoutMetrics(rects, [beside]).edgesBehindNodes).toBe(0);
  });

  it("sums the vertical travel and measures the height", () => {
    const curves = [line("a", "b", [0, 0], [10, 30], [20, 10])];

    const metrics = layoutMetrics([rect("a", 0, 5), rect("b", 50, 100, 10, 20)], curves);

    expect(metrics.edgeTravel).toBe(50);
    expect(metrics.height).toBe(115);
  });
});

describe("defaultBezier", () => {
  it("runs from the start to the end with horizontal tangents", () => {
    const points = defaultBezier({ x: 0, y: 0 }, { x: 100, y: 200 });

    expect(points[0]).toEqual({ x: 0, y: 0 });
    expect(points.at(-1)).toEqual({ x: 100, y: 200 });
    expect(points[1]!.y).toBeLessThan(2);
  });
});
