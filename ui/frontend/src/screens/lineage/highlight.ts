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

// 3:1 against the canvas in both themes (non-text contrast, WCAG 1.4.11).
const EDGE_COLOR = "light-dark(var(--mantine-color-gray-6), var(--mantine-color-dark-2))";
export const PRIMARY_EDGE_COLOR = "var(--mantine-primary-color-filled)";
const EDGE_WIDTH = 1.5;
const RELATED_EDGE_WIDTH = 2;
const DIMMED_EDGE_OPACITY = 0.15;

export interface EdgeAppearance {
  stroke: string;
  strokeWidth: number;
  opacity: number;
}

// A column edge of the focus, and an edge of a selected node's chain.
export const FOCUS_EDGE: EdgeAppearance = { stroke: PRIMARY_EDGE_COLOR, strokeWidth: RELATED_EDGE_WIDTH, opacity: 1 };

// With a node selected its chain stands out in the primary colour and the other edges fade.
export function edgeAppearance(related: Related | undefined, id: string): EdgeAppearance {
  if (related === undefined) return { stroke: EDGE_COLOR, strokeWidth: EDGE_WIDTH, opacity: 1 };
  if (related.edges.has(id)) return FOCUS_EDGE;
  return { stroke: EDGE_COLOR, strokeWidth: EDGE_WIDTH, opacity: DIMMED_EDGE_OPACITY };
}
