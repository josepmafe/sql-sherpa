import type { Point } from "./layout";

const SAMPLES_PER_SEGMENT = 24;
// Fritsch-Carlson: a tangent that makes (tangent / secant) leave this circle can overshoot the two points it joins.
const MAX_TANGENT_RATIO_SQUARED = 9;
const PATH_DECIMALS = 3;

// The monotone cubic through the points (x increases along them), sampled: y never leaves the range of the two points
// it runs between, and the curve is horizontal at both ends, where the handles are. Between a gap's left and right
// waypoint it is level, so a long edge runs through a column inside its gap.
export function edgePoints(start: Point, waypoints: Point[], end: Point): Point[] {
  const points = [start, ...waypoints, end];
  const tangents = tangentsOf(points);
  const sampled = [start];
  for (let k = 0; k + 1 < points.length; k++) {
    for (let step = 1; step <= SAMPLES_PER_SEGMENT; step++) {
      sampled.push(
        step === SAMPLES_PER_SEGMENT
          ? points[k + 1]!
          : hermite(points[k]!, points[k + 1]!, tangents[k]!, tangents[k + 1]!, step / SAMPLES_PER_SEGMENT),
      );
    }
  }
  return sampled;
}

// The same curve as SVG cubic commands: a Hermite segment is a Bezier whose control points sit a third of the way.
export function edgePath(start: Point, waypoints: Point[], end: Point): string {
  const points = [start, ...waypoints, end];
  const tangents = tangentsOf(points);
  const commands = [`M ${number(start.x)} ${number(start.y)}`];
  for (let k = 0; k + 1 < points.length; k++) {
    const [from, to] = [points[k]!, points[k + 1]!];
    const third = (to.x - from.x) / 3;
    const c1 = { x: from.x + third, y: from.y + tangents[k]! * third };
    const c2 = { x: to.x - third, y: to.y - tangents[k + 1]! * third };
    commands.push(`C ${[c1.x, c1.y, c2.x, c2.y, to.x, to.y].map(number).join(" ")}`);
  }
  return commands.join(" ");
}

function tangentsOf(points: Point[]): number[] {
  const secants = points.slice(1).map((to, k) => (to.y - points[k]!.y) / (to.x - points[k]!.x));
  const tangents = points.map((_, k) => {
    if (k === 0 || k === points.length - 1) return 0;
    const [before, after] = [secants[k - 1]!, secants[k]!];
    return before * after <= 0 ? 0 : (before + after) / 2;
  });
  for (const [k, secant] of secants.entries()) {
    if (secant === 0) {
      tangents[k] = 0;
      tangents[k + 1] = 0;
      continue;
    }
    const [alpha, beta] = [tangents[k]! / secant, tangents[k + 1]! / secant];
    const ratio = alpha * alpha + beta * beta;
    if (ratio > MAX_TANGENT_RATIO_SQUARED) {
      const scale = 3 / Math.sqrt(ratio);
      tangents[k] = scale * alpha * secant;
      tangents[k + 1] = scale * beta * secant;
    }
  }
  return tangents;
}

function hermite(from: Point, to: Point, fromTangent: number, toTangent: number, t: number): Point {
  const width = to.x - from.x;
  const [t2, t3] = [t * t, t * t * t];
  return {
    x: from.x + width * t,
    y:
      from.y +
      (to.y - from.y) * (3 * t2 - 2 * t3) +
      fromTangent * width * (t3 - 2 * t2 + t) +
      toTangent * width * (t3 - t2),
  };
}

function number(value: number): string {
  return String(Number(value.toFixed(PATH_DECIMALS)));
}
