import { useEffect, useMemo, useState } from "react";

import { type BandNode, type BandPlacement, type LayoutEdge, orderNodes, placeBands, type Point, stackBands } from "./layout";

export type LayoutState = "laying-out" | "ready" | "error";

interface Settled {
  nodes: BandNode[];
  edges: LayoutEdge[];
  ok: boolean;
}

// Pass stable arrays: a new array is a new ELK run and placement. A height change only restacks, so pass a new `heights`
// map when a node's height changes; a node without an entry has NODE_HEIGHT.
export function useGraphLayout(
  nodes: BandNode[],
  edges: LayoutEdge[],
  heights: Map<string, number>,
): { positions: Map<string, Point> | undefined; routes: Map<string, Point[]> | undefined; state: LayoutState } {
  const [placement, setPlacement] = useState<BandPlacement>();
  const [settled, setSettled] = useState<Settled>();

  useEffect(() => {
    // The cleanup marks this run superseded, so an order that arrives late is discarded.
    let current = true;
    orderNodes(nodes, edges).then(
      (order) => {
        if (!current) return;
        setPlacement(placeBands(nodes, edges, order));
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

  const stacked = useMemo(() => (placement === undefined ? undefined : stackBands(placement, heights)), [placement, heights]);
  const done = settled !== undefined && settled.nodes === nodes && settled.edges === edges;
  return { positions: stacked?.positions, routes: stacked?.routes, state: !done ? "laying-out" : settled.ok ? "ready" : "error" };
}
