import type { LayoutEdge } from "./layout";

export interface Related {
  nodes: Set<string>;
  edges: Set<string>;
}

export function edgeId(edge: LayoutEdge): string {
  return `${edge.source}->${edge.target}`;
}

// Works on the edges shown only, so a chain through a hidden layer ends there, like the edges themselves.
export function relatedNodes(edges: LayoutEdge[], id: string): Related {
  const nodes = new Set([id]);
  const related = new Set<string>();
  for (const [from, to] of [
    ["target", "source"],
    ["source", "target"],
  ] as const) {
    const reached = new Set([id]);
    const queue = [id];
    while (queue.length > 0) {
      const current = queue.pop()!;
      for (const edge of edges) {
        if (edge[from] !== current) continue;
        related.add(edgeId(edge));
        nodes.add(edge[to]);
        if (!reached.has(edge[to])) {
          reached.add(edge[to]);
          queue.push(edge[to]);
        }
      }
    }
  }
  return { nodes, edges: related };
}
