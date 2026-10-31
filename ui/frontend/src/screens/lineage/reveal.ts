import { useReactFlow, useStoreApi } from "@xyflow/react";
import { useCallback } from "react";

import { COLUMN_ROW_HEIGHT, NODE_HEIGHT, NODE_WIDTH, type Point } from "./layout";

interface Viewport {
  x: number;
  y: number;
  zoom: number;
}

// `position` is the box's top-left corner: the node's header, or one of its column rows (`height`).
export function isInView(
  position: Point,
  viewport: Viewport,
  canvas: { width: number; height: number },
  height = NODE_HEIGHT,
): boolean {
  const left = position.x * viewport.zoom + viewport.x;
  const top = position.y * viewport.zoom + viewport.y;
  return (
    left >= 0 &&
    top >= 0 &&
    left + NODE_WIDTH * viewport.zoom <= canvas.width &&
    top + height * viewport.zoom <= canvas.height
  );
}

// What a keyboard focus must keep in view: the node's header, or the slot of its `row`-th column row under it.
export function rowBox(node: Point, row: number | undefined): { position: Point; height: number } {
  if (row === undefined) return { position: node, height: NODE_HEIGHT };
  return { position: { x: node.x, y: node.y + NODE_HEIGHT + row * COLUMN_ROW_HEIGHT }, height: COLUMN_ROW_HEIGHT };
}

// Centres what took the focus at the current zoom when it is not fully in view. React Flow cancels the browser's own
// scrolling to a focused element.
export function useRevealNode(): (id: string, row?: number) => void {
  const { getNode, getViewport, setCenter } = useReactFlow();
  const store = useStoreApi();
  return useCallback(
    (id: string, row?: number) => {
      const node = getNode(id);
      const { width, height } = store.getState();
      if (node === undefined || width === 0) return;
      const viewport = getViewport();
      const box = rowBox(node.position, row);
      if (isInView(box.position, viewport, { width, height }, box.height)) return;
      void setCenter(box.position.x + NODE_WIDTH / 2, box.position.y + box.height / 2, {
        zoom: viewport.zoom,
        duration: 0,
      });
    },
    [getNode, getViewport, setCenter, store],
  );
}
