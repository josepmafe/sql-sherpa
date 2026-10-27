import type { LayoutEdge } from "./layout";

export interface Related {
  nodes: Set<string>;
  edges: Set<string>;
}

export function edgeId(edge: LayoutEdge): string {
  return `${edge.source}->${edge.target}`;
}

// Works on the edges shown only, so a chain through a hidden layer ends there, like the edges themselves. The
// highlighted edges are every shown edge between two related nodes (UI spec §6.5), not just the walked paths.
export function relatedNodes(edges: LayoutEdge[], id: string): Related {
  const nodes = new Set([id]);
  for (const [from, to] of [
    ["target", "source"],
    ["source", "target"],
  ] as const) {
    const queue = [id];
    const reached = new Set(queue);
    while (queue.length > 0) {
      const current = queue.pop()!;
      for (const edge of edges) {
        if (edge[from] !== current || reached.has(edge[to])) continue;
        reached.add(edge[to]);
        nodes.add(edge[to]);
        queue.push(edge[to]);
      }
    }
  }
  const related = edges.filter((edge) => nodes.has(edge.source) && nodes.has(edge.target));
  return { nodes, edges: new Set(related.map(edgeId)) };
}
