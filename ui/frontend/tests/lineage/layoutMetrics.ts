import type { Point } from "../../src/screens/lineage/layout";

export interface Rect {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
}
// An edge as drawn: a sampled polyline from the source node to the target node.
export interface Curve {
  source: string;
  target: string;
  points: Point[];
}
export interface Metrics {
  crossings: number;
  edgesBehindNodes: number;
  edgeTravel: number;
  height: number;
}

const BEZIER_SAMPLES = 60;

export function layoutMetrics(rects: Rect[], curves: Curve[]): Metrics {
  return {
    crossings: countCrossings(curves),
    edgesBehindNodes: curves.filter((curve) => isBehindANode(curve, rects)).length,
    edgeTravel: curves.reduce((total, { points }) => total + verticalTravel(points), 0),
    height: height(rects),
  };
}

// React Flow's default edge: horizontal tangents, control points at half the horizontal distance.
export function defaultBezier(start: Point, end: Point): Point[] {
  const offset = (end.x - start.x) * 0.5;
  const c1 = { x: start.x + offset, y: start.y };
  const c2 = { x: end.x - offset, y: end.y };
  return Array.from({ length: BEZIER_SAMPLES + 1 }, (_, index) => {
    const t = index / BEZIER_SAMPLES;
    const u = 1 - t;
    return {
      x: u ** 3 * start.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t ** 3 * end.x,
      y: u ** 3 * start.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t ** 3 * end.y,
    };
  });
}

// Segment intersections between edges that share no node.
function countCrossings(curves: Curve[]): number {
  let crossings = 0;
  for (let i = 0; i < curves.length; i++) {
    for (let j = i + 1; j < curves.length; j++) {
      const a = curves[i]!;
      const b = curves[j]!;
      if (a.source === b.source || a.source === b.target || a.target === b.source || a.target === b.target) continue;
      for (let p = 0; p + 1 < a.points.length; p++) {
        for (let q = 0; q + 1 < b.points.length; q++) {
          if (segmentsCross(a.points[p]!, a.points[p + 1]!, b.points[q]!, b.points[q + 1]!)) crossings++;
        }
      }
    }
  }
  return crossings;
}

function segmentsCross(a: Point, b: Point, c: Point, d: Point): boolean {
  return side(a, b, c) * side(a, b, d) < 0 && side(c, d, a) * side(c, d, b) < 0;
}

function side(a: Point, b: Point, p: Point): number {
  return Math.sign((b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x));
}

function isBehindANode(curve: Curve, rects: Rect[]): boolean {
  return rects
    .filter((rect) => rect.id !== curve.source && rect.id !== curve.target)
    .some((rect) =>
      curve.points.some(
        (p) => p.x > rect.x && p.x < rect.x + rect.width && p.y > rect.y && p.y < rect.y + rect.height,
      ),
    );
}

function verticalTravel(points: Point[]): number {
  return points.slice(1).reduce((total, point, index) => total + Math.abs(point.y - points[index]!.y), 0);
}

function height(rects: Rect[]): number {
  if (rects.length === 0) return 0;
  return Math.max(...rects.map((r) => r.y + r.height)) - Math.min(...rects.map((r) => r.y));
}
