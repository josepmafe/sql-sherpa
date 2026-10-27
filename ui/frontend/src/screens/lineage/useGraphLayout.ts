import { useEffect, useState } from "react";

import { type LayoutEdge, type LayoutNode, layoutGraph, type Point } from "./layout";

export type LayoutState = "laying-out" | "ready" | "error";

interface Settled {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  ok: boolean;
}

// Pass stable arrays: a new array is a new layout run.
export function useGraphLayout(
  nodes: LayoutNode[],
  edges: LayoutEdge[],
): { positions: Map<string, Point> | undefined; state: LayoutState } {
  const [positions, setPositions] = useState<Map<string, Point>>();
  const [settled, setSettled] = useState<Settled>();

  useEffect(() => {
    // The cleanup marks this run superseded, so a layout that finishes late is discarded.
    let current = true;
    layoutGraph(nodes, edges).then(
      (placed) => {
        if (!current) return;
        setPositions(placed);
        setSettled({ nodes, edges, ok: true });
      },
      () => {
        // The previous positions stay.
        if (current) setSettled({ nodes, edges, ok: false });
      },
    );
    return () => {
      current = false;
    };
  }, [nodes, edges]);

  const done = settled !== undefined && settled.nodes === nodes && settled.edges === edges;
  return { positions, state: !done ? "laying-out" : settled.ok ? "ready" : "error" };
}
