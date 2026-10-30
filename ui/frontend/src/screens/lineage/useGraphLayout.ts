import { useEffect, useMemo, useState } from "react";

import { type BandNode, type LayoutEdge, orderNodes, type Point, stackBands } from "./layout";

export type LayoutState = "laying-out" | "ready" | "error";

interface Ordered {
  nodes: BandNode[];
  edges: LayoutEdge[];
  order: Map<string, number>;
}
interface Settled {
  nodes: BandNode[];
  edges: LayoutEdge[];
  ok: boolean;
}

// Pass stable arrays: a new array is a new ELK run. A height change only restacks (no ELK), so pass a new `heights`
// map when a node's height changes; a node without an entry has NODE_HEIGHT.
export function useGraphLayout(
  nodes: BandNode[],
  edges: LayoutEdge[],
  heights: Map<string, number>,
): { positions: Map<string, Point> | undefined; state: LayoutState } {
  const [ordered, setOrdered] = useState<Ordered>();
  const [settled, setSettled] = useState<Settled>();

  useEffect(() => {
    // The cleanup marks this run superseded, so an order that arrives late is discarded.
    let current = true;
    orderNodes(nodes, edges).then(
      (order) => {
        if (!current) return;
        setOrdered({ nodes, edges, order });
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

  const positions = useMemo(
    () => (ordered === undefined ? undefined : stackBands(ordered.nodes, ordered.edges, ordered.order, heights)),
    [ordered, heights],
  );
  const done = settled !== undefined && settled.nodes === nodes && settled.edges === edges;
  return { positions, state: !done ? "laying-out" : settled.ok ? "ready" : "error" };
}
