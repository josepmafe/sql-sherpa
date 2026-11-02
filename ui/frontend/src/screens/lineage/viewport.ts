import { NODE_HEIGHT, NODE_WIDTH, type Point } from "./layout";

export interface Viewport {
  x: number;
  y: number;
  zoom: number;
}

interface Kept {
  subject: string;
  viewport: Viewport;
}

// The user's zoom and pan for the page session: it survives a redraw and leaving Lineage, not a reload. A subject is
// what the graph is about (the selector: the mode, the domain, the table and its depths); a layer, "Failing only" or an
// expanded node do not change it.
let kept: Kept | undefined;

export function keptViewport(subject: string): Viewport | undefined {
  return kept?.subject === subject ? kept.viewport : undefined;
}

export function keepView(subject: string, viewport: Viewport): void {
  kept = { subject, viewport };
}

export function forgetView(): void {
  kept = undefined;
}

// Whether any node, even partly, lies in the canvas under this viewport.
export function showsAnyNode(
  positions: Map<string, Point>,
  heights: Map<string, number>,
  viewport: Viewport,
  canvas: { width: number; height: number },
): boolean {
  return [...positions].some(([id, { x, y }]) => {
    const left = x * viewport.zoom + viewport.x;
    const top = y * viewport.zoom + viewport.y;
    const height = heights.get(id) ?? NODE_HEIGHT;
    return (
      left < canvas.width &&
      top < canvas.height &&
      left + NODE_WIDTH * viewport.zoom > 0 &&
      top + height * viewport.zoom > 0
    );
  });
}
