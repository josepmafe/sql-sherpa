import { useReactFlow, useStoreApi } from "@xyflow/react";
import { useCallback, useRef } from "react";

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

type RevealOnKeyboardFocusProps = Record<"onPointerDown" | "onMouseUp" | "onPointerCancel" | "onFocus" | "onBlur", () => void>;

// The handlers of a button that reveals itself when the keyboard focuses it. A press focuses the button too (in
// Chromium on mousedown): moving the viewport then would put the click on whatever lands under the pointer.
export function useRevealOnKeyboardFocus(reveal: () => void): RevealOnKeyboardFocusProps {
  const pressed = useRef(false);
  const release = () => {
    pressed.current = false;
  };
  return {
    onPointerDown: () => {
      pressed.current = true;
    },
    onFocus: () => {
      if (!pressed.current) reveal();
    },
    // The press ends the flag: Safari and Firefox on macOS do not focus a button on click, so no blur would. mouseup,
    // not pointerup: a touch tap focuses the button after its pointerup, between mousedown and mouseup.
    onMouseUp: release,
    onPointerCancel: release,
    onBlur: release,
  };
}
