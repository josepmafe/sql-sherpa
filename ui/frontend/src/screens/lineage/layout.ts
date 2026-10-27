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

type Elk = InstanceType<(typeof import("elkjs/lib/elk.bundled.js"))["default"]>;

let elkLoad: Promise<Elk> | undefined;

export function columnOf(layer: Layer): number {
  return COLUMN[layer];
}

export async function layoutGraph(nodes: LayoutNode[], edges: LayoutEdge[]): Promise<Map<string, Point>> {
  if (nodes.length === 0) return new Map();
  const elk = await loadElk();
  const laidOut = await elk.layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.partitioning.activate": "true",
      // Packing the components side by side would pull a node without edges out of its layer's column.
      "elk.separateConnectedComponents": "false",
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

// Loaded on first use, so the other screens do not pay for it. The bundled build carries ELK's worker code inline: a
// same-origin chunk, no remote worker or CDN fetch (offline requirement, UI spec §3).
function loadElk(): Promise<Elk> {
  elkLoad ??= import("elkjs/lib/elk.bundled.js").then(
    (module) => new module.default(),
    (error: unknown) => {
      elkLoad = undefined; // a failed chunk load can be retried
      throw error;
    },
  );
  return elkLoad;
}
