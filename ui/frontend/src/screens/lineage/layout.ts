// The bundled build carries ELK's worker code inline: no remote worker or CDN fetch (offline requirement, UI spec §3).
import ELK from "elkjs/lib/elk.bundled.js";

import type { Layer } from "./url";

export const NODE_WIDTH = 220;
export const NODE_HEIGHT = 84;
const COLUMN_GAP = 90;
const ROW_GAP = 24;

export interface LayoutNode {
  id: string;
  layer: Layer;
}
export interface LayoutEdge {
  source: string;
  target: string;
}
export interface Point {
  x: number;
  y: number;
}

// The columns, left to right (UI spec §6.5). ELK's partitioning keeps a layer's nodes out of an earlier column.
const COLUMN: Record<Layer, number> = {
  source: 0,
  seed: 0,
  staging: 1,
  snapshot: 1,
  intermediate: 2,
  other: 2,
  marts: 3,
  exposure: 4,
};

const elk = new ELK();

export async function layoutGraph(nodes: LayoutNode[], edges: LayoutEdge[]): Promise<Map<string, Point>> {
  if (nodes.length === 0) return new Map();
  const laidOut = await elk.layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.partitioning.activate": "true",
      "elk.layered.spacing.nodeNodeBetweenLayers": String(COLUMN_GAP),
      "elk.spacing.nodeNode": String(ROW_GAP),
    },
    children: nodes.map((node) => ({
      id: node.id,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      layoutOptions: { "elk.partitioning.partition": String(COLUMN[node.layer]) },
    })),
    edges: edges.map((edge, index) => ({ id: `e${index}`, sources: [edge.source], targets: [edge.target] })),
  });
  return new Map((laidOut.children ?? []).map((child) => [child.id, { x: child.x ?? 0, y: child.y ?? 0 }]));
}
