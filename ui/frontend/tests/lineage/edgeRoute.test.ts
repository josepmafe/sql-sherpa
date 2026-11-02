import { describe, expect, it } from "vitest";

import { edgePath, edgePoints } from "../../src/screens/lineage/edgeRoute";
import type { Point } from "../../src/screens/lineage/layout";

const START: Point = { x: 0, y: 0 };
const END: Point = { x: 600, y: 300 };
// A route through two columns: up to the first gap, level through it, down to the second, level, then the landing.
const GAPS: Point[] = [
  { x: 100, y: 120 },
  { x: 320, y: 120 },
  { x: 400, y: 60 },
  { x: 620, y: 60 },
];
const NEAR = 0.5;
const SAMPLES = 1000;

describe("edgePoints", () => {
  it("passes through every waypoint", () => {
    const route = [START, ...GAPS, { x: 900, y: 10 }];

    const points = edgePoints(START, GAPS, { x: 900, y: 10 });

    for (const wanted of route) {
      expect(points.some((p) => Math.hypot(p.x - wanted.x, p.y - wanted.y) < 1e-6)).toBe(true);
    }
  });

  it("never leaves the y range of the two points it runs between", () => {
    const route = [START, ...GAPS, { x: 900, y: 10 }];

    const points = edgePoints(START, GAPS, { x: 900, y: 10 });

    for (let i = 0; i + 1 < route.length; i++) {
      const [from, to] = [route[i]!, route[i + 1]!];
      const between = points.filter((p) => p.x >= from.x && p.x <= to.x);
      expect(between.length).toBeGreaterThan(2);
      for (const p of between) {
        expect(p.y).toBeGreaterThanOrEqual(Math.min(from.y, to.y) - 1e-9);
        expect(p.y).toBeLessThanOrEqual(Math.max(from.y, to.y) + 1e-9);
      }
    }
  });

  it("runs level between a gap's left and right waypoint", () => {
    const points = edgePoints(START, GAPS, END);

    const inside = points.filter((p) => p.x >= 100 && p.x <= 320);
    expect(inside.length).toBeGreaterThan(2);
    expect(new Set(inside.map((p) => p.y))).toEqual(new Set([120]));
  });

  it("is one S-curve without waypoints, flat at both ends", () => {
    const points = edgePoints(START, [], END);

    expect(points[0]).toEqual(START);
    expect(points.at(-1)).toEqual(END);
    const slope = (a: Point, b: Point) => (b.y - a.y) / (b.x - a.x);
    const middle = points[Math.floor(points.length / 2)]!;
    const steepest = slope(points[11]!, points[13]!);
    expect(slope(points[0]!, points[1]!)).toBeLessThan(0.1 * steepest);
    expect(slope(points.at(-2)!, points.at(-1)!)).toBeLessThan(0.1 * steepest);
    expect(middle.y).toBeCloseTo(150, 0);
    expect(points.map((p) => p.y)).toEqual([...points.map((p) => p.y)].sort((a, b) => a - b));
  });

  it("stays monotone when the edge goes up", () => {
    const points = edgePoints({ x: 0, y: 300 }, [], { x: 600, y: 0 });

    expect(points.map((p) => p.y)).toEqual([...points.map((p) => p.y)].sort((a, b) => b - a));
  });
});

describe("edgePath", () => {
  it("is an SVG path whose curve matches edgePoints within half a pixel", () => {
    const d = edgePath(START, GAPS, END);

    expect(d).toMatch(/^M [-\d.]+ [-\d.]+( C( [-\d.]+){6})+$/);
    const sampled = sampleCubics(d);
    for (const wanted of edgePoints(START, GAPS, END)) {
      const nearest = Math.min(...sampled.map((p) => Math.hypot(p.x - wanted.x, p.y - wanted.y)));
      expect(nearest).toBeLessThan(NEAR);
    }
  });
});

// Samples the path's cubic Bezier commands densely.
function sampleCubics(d: string): Point[] {
  const numbers = d.match(/-?\d+(\.\d+)?/g)!.map(Number);
  let [x0, y0] = [numbers[0]!, numbers[1]!];
  const points: Point[] = [];
  for (let i = 2; i + 5 < numbers.length + 0; i += 6) {
    const [x1, y1, x2, y2, x3, y3] = numbers.slice(i, i + 6) as [number, number, number, number, number, number];
    for (let step = 0; step <= SAMPLES; step++) {
      const t = step / SAMPLES;
      const u = 1 - t;
      points.push({
        x: u ** 3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t ** 3 * x3,
        y: u ** 3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t ** 3 * y3,
      });
    }
    [x0, y0] = [x3, y3];
  }
  return points;
}
