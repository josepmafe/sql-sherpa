import { useReactFlow, useStoreApi } from "@xyflow/react";
import { useCallback } from "react";

import { NODE_HEIGHT, NODE_WIDTH, type Point } from "./layout";

interface Viewport {
  x: number;
  y: number;
  zoom: number;
}

export function isInView(position: Point, viewport: Viewport, canvas: { width: number; height: number }): boolean {
  const left = position.x * viewport.zoom + viewport.x;
  const top = position.y * viewport.zoom + viewport.y;
  return (
    left >= 0 &&
    top >= 0 &&
    left + NODE_WIDTH * viewport.zoom <= canvas.width &&
    top + NODE_HEIGHT * viewport.zoom <= canvas.height
  );
}

// Centres the node at the current zoom when it is not fully in view, e.g. after the panel narrowed the canvas or
// when a keyboard focus lands on it.
export function useRevealNode(): (id: string) => void {
  const { getNode, getViewport, setCenter } = useReactFlow();
  const store = useStoreApi();
  return useCallback(
    (id: string) => {
      const node = getNode(id);
      const { width, height } = store.getState();
      if (node === undefined || width === 0) return;
      const viewport = getViewport();
      if (isInView(node.position, viewport, { width, height })) return;
      void setCenter(node.position.x + NODE_WIDTH / 2, node.position.y + NODE_HEIGHT / 2, {
        zoom: viewport.zoom,
        duration: 0,
      });
    },
    [getNode, getViewport, setCenter, store],
  );
}
