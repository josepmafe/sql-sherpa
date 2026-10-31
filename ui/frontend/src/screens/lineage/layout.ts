import type { Layer } from "./url";

export const NODE_WIDTH = 220;
export const NODE_HEIGHT = 84;
const BAND_GAP = 90;
const SUB_COLUMN_GAP = 60;
const ROW_GAP = 24;
// One column row of an expanded node (UI spec §6.5).
export const COLUMN_ROW_HEIGHT = 28;

export interface BandNode {
  id: string;
  layer: Layer;
  // The source system of a source table.
  sourceName?: string | undefined;
}
export interface LayoutEdge {
  source: string;
  target: string;
}
export interface Point {
  x: number;
  y: number;
}

// A node lists its rows under its header; stackBands gets this height for it.
export function nodeHeight(rows: number): number {
  return NODE_HEIGHT + rows * COLUMN_ROW_HEIGHT;
}

// The bands, left to right (UI spec §6.5). ELK's partitioning keeps a band's nodes out of an earlier band.
export const BAND: Record<Layer, number> = {
  source: 0,
  seed: 1,
  staging: 2,
  snapshot: 3,
  intermediate: 4,
  other: 4,
  marts: 5,
  exposure: 6,
};

type Elk = InstanceType<(typeof import("elkjs/lib/elk.bundled.js"))["default"]>;

let elkLoad: Promise<Elk> | undefined;

// ELK's only output is each node's rank within its band, chosen to cross few edges; stackBands does the placing.
export async function orderNodes(nodes: BandNode[], edges: LayoutEdge[]): Promise<Map<string, number>> {
  if (nodes.length === 0) return new Map();
  const elk = await loadElk();
  const laidOut = await elk.layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.partitioning.activate": "true",
      // Packing the components side by side would pull a node without edges out of its band.
      "elk.separateConnectedComponents": "false",
      "elk.layered.spacing.nodeNodeBetweenLayers": String(BAND_GAP),
      "elk.spacing.nodeNode": String(ROW_GAP),
    },
    children: nodes.map((node) => ({
      id: node.id,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
      layoutOptions: { "elk.partitioning.partition": String(BAND[node.layer]) },
    })),
    edges: edges.map((edge, index) => ({ id: `e${index}`, sources: [edge.source], targets: [edge.target] })),
  });
  const y = new Map((laidOut.children ?? []).map((child) => [child.id, child.y ?? 0]));
  const ranks = new Map<string, number>();
  for (const band of groupBy(nodes, (node) => BAND[node.layer]).values()) {
    // A source system's tables stay together; ELK's y orders the rest.
    band
      .sort((a, b) => (a.sourceName ?? "").localeCompare(b.sourceName ?? "") || y.get(a.id)! - y.get(b.id)!)
      .forEach((node, rank) => ranks.set(node.id, rank));
  }
  return ranks;
}

// Pure and synchronous: it reruns on every height change, where ELK does not.
export function stackBands(
  nodes: BandNode[],
  edges: LayoutEdge[],
  order: Map<string, number>,
  heights: Map<string, number>,
): Map<string, Point> {
  const subColumn = subColumns(nodes, edges);
  const positions = new Map<string, Point>();
  let bandStart = 0;
  const bands = [...groupBy(nodes, (node) => BAND[node.layer])].sort(([a], [b]) => a - b);
  for (const [, band] of bands) {
    const columns = groupBy(band, (node) => subColumn.get(node.id)!);
    for (const [column, members] of columns) {
      let y = 0;
      members.sort((a, b) => rank(order, a) - rank(order, b));
      for (const node of members) {
        positions.set(node.id, { x: bandStart + column * (NODE_WIDTH + SUB_COLUMN_GAP), y });
        y += (heights.get(node.id) ?? NODE_HEIGHT) + ROW_GAP;
      }
    }
    const last = Math.max(...columns.keys());
    bandStart += (last + 1) * NODE_WIDTH + last * SUB_COLUMN_GAP + BAND_GAP;
  }
  return positions;
}

// The length of each node's longest chain of parents from its own band, among the edges shown.
function subColumns(nodes: BandNode[], edges: LayoutEdge[]): Map<string, number> {
  const bandOf = new Map(nodes.map((node) => [node.id, BAND[node.layer]]));
  const parents = new Map<string, string[]>();
  for (const { source, target } of edges) {
    if (bandOf.has(source) && bandOf.get(source) === bandOf.get(target)) {
      parents.set(target, [...(parents.get(target) ?? []), source]);
    }
  }
  const depth = new Map<string, number>();
  const visiting = new Set<string>();
  const columnOf = (id: string): number => {
    const known = depth.get(id);
    if (known !== undefined) return known;
    if (visiting.has(id)) return 0; // a dbt graph has no cycle; this only stops a malformed one from looping
    visiting.add(id);
    const column = Math.max(-1, ...(parents.get(id) ?? []).map(columnOf)) + 1;
    visiting.delete(id);
    depth.set(id, column);
    return column;
  };
  return new Map(nodes.map((node) => [node.id, columnOf(node.id)]));
}

function rank(order: Map<string, number>, node: BandNode): number {
  return order.get(node.id) ?? Number.MAX_SAFE_INTEGER;
}

function groupBy<T>(items: T[], key: (item: T) => number): Map<number, T[]> {
  const groups = new Map<number, T[]>();
  for (const item of items) {
    const group = groups.get(key(item));
    if (group === undefined) groups.set(key(item), [item]);
    else group.push(item);
  }
  return groups;
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
