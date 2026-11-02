import type { Layer } from "./url";

export const NODE_WIDTH = 220;
export const NODE_HEIGHT = 84;
const BAND_GAP = 90;
const SUB_COLUMN_GAP = 60;
const ROW_GAP = 24;
// A long edge reserves a gap in each column it crosses (UI spec §6.5).
const SLOT_HEIGHT = 2;
const SLOT_NODE_GAP = 12;
const SLOT_SLOT_GAP = 8;
// Placement: sweeps that reorder and fit the columns, then sweeps that only straighten the long edges.
const SWEEPS = 12;
const STRAIGHTEN_SWEEPS = 8;
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
export interface PlacedItem {
  id: string;
  // The key of the edge a gap belongs to; absent for a node.
  edge?: string;
}
// Where placeBands put things with every node collapsed: each column's x and items in order, and each item's top.
export interface BandPlacement {
  columns: { x: number; items: PlacedItem[] }[];
  tops: Map<string, number>;
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

// ELK's only output is each node's rank within its band, chosen to cross few edges; placeBands does the placing.
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

// Pure and synchronous, once per graph like ELK: where each band's columns go, which nodes and gaps stand in each
// column, and their tops with every node collapsed.
export function placeBands(nodes: BandNode[], edges: LayoutEdge[], order: Map<string, number>): BandPlacement {
  return new BandPlacer(nodes, edges, order).place();
}

// Pure and synchronous: it reruns on every height change, where placeBands and ELK do not. An item only moves down,
// when the one above it grew, so a node's expansion moves the items below it in its own column and nothing else.
// `routes` holds each long edge's inner waypoints: for each gap, the column's left and right x at the gap's y.
export function stackBands(
  placement: BandPlacement,
  heights: Map<string, number>,
): { positions: Map<string, Point>; routes: Map<string, Point[]> } {
  const positions = new Map<string, Point>();
  const routes = new Map<string, Point[]>();
  for (const { x, items } of placement.columns) {
    let previous: { item: PlacedItem; bottom: number } | undefined;
    for (const item of items) {
      const wanted = placement.tops.get(item.id)!;
      const top = previous === undefined ? wanted : Math.max(wanted, previous.bottom + spacing(previous.item, item));
      let height = SLOT_HEIGHT;
      if (item.edge === undefined) {
        height = heights.get(item.id) ?? NODE_HEIGHT;
        positions.set(item.id, { x, y: top });
      } else {
        const y = top + SLOT_HEIGHT / 2;
        routes.set(item.edge, [...(routes.get(item.edge) ?? []), { x, y }, { x: x + NODE_WIDTH, y }]);
      }
      previous = { item, bottom: top + height };
    }
  }
  return { positions, routes };
}

export function edgeKey(source: string, target: string): string {
  return `${source}->${target}`;
}

// An item of a column: a node, or the gap that an edge reserves to pass through the column.
interface Item {
  id: string;
  column: number;
  height: number;
  // The key of the edge a gap belongs to; undefined for a node.
  edge: string | undefined;
  sourceName: string | undefined;
  // The items next along each edge through this one: a node's neighbours (a gap or the other node), a gap's two.
  links: Item[];
  // A gap: every gap of its edge, itself included.
  siblings: Item[];
  centre: number;
}

class BandPlacer {
  private readonly xs: number[];
  private readonly columnOf: Map<string, number>;
  private readonly columns: Item[][];

  constructor(
    private readonly nodes: BandNode[],
    private readonly edges: LayoutEdge[],
    private readonly order: Map<string, number>,
  ) {
    ({ xs: this.xs, columnOf: this.columnOf } = columnLayout(nodes, edges));
    this.columns = this.xs.map(() => []);
  }

  place(): BandPlacement {
    this.addNodes();
    this.addGaps();
    for (const column of this.columns) {
      column.sort((a, b) => a.centre - b.centre);
      this.fit(
        column,
        column.map((item) => item.centre),
      );
    }
    for (let sweep = 0; sweep < SWEEPS + STRAIGHTEN_SWEEPS; sweep++) {
      const indexes = this.columns.map((_, index) => index);
      for (const index of sweep % 2 === 0 ? indexes : indexes.reverse()) {
        this.sweepColumn(this.columns[index]!, sweep < SWEEPS);
      }
    }
    return this.placement();
  }

  // The real nodes start from the plain stack, in ELK's order.
  private addNodes(): void {
    const ordered = [...this.nodes].sort((a, b) => rank(this.order, a) - rank(this.order, b));
    const bottoms = this.xs.map(() => 0);
    for (const node of ordered) {
      const column = this.columnOf.get(node.id)!;
      const top = bottoms[column]!;
      bottoms[column] = top + NODE_HEIGHT + ROW_GAP;
      this.columns[column]!.push({
        id: node.id,
        column,
        height: NODE_HEIGHT,
        edge: undefined,
        sourceName: node.sourceName,
        links: [],
        siblings: [],
        centre: top + NODE_HEIGHT / 2,
      });
    }
  }

  // A gap in each column an edge crosses, starting on the straight line between the edge's ends.
  private addGaps(): void {
    const byId = new Map(this.columns.flat().map((item) => [item.id, item]));
    const seen = new Set<string>();
    for (const { source, target } of this.edges) {
      const key = edgeKey(source, target);
      const [from, to] = [byId.get(source), byId.get(target)];
      if (seen.has(key) || from === undefined || to === undefined) continue;
      seen.add(key);
      const chain = [from];
      for (let column = from.column + 1; column < to.column; column++) {
        const share = (column - from.column) / (to.column - from.column);
        const gap: Item = {
          id: `gap:${key}@${column}`,
          column,
          height: SLOT_HEIGHT,
          edge: key,
          sourceName: undefined,
          links: [],
          siblings: [],
          centre: from.centre + share * (to.centre - from.centre),
        };
        this.columns[column]!.push(gap);
        chain.push(gap);
      }
      chain.push(to);
      const gaps = chain.slice(1, -1);
      for (const gap of gaps) gap.siblings = gaps;
      chain.slice(1).forEach((next, index) => {
        chain[index]!.links.push(next);
        next.links.push(chain[index]!);
      });
    }
  }

  // Each item wishes to sit at the mean of its neighbours' centres; the column is re-sorted by wish while `reorder`
  // holds, then fitted. After the reordering sweeps a gap wishes the mean of its edge's gaps, so a long edge rises once,
  // runs level and comes down once.
  private sweepColumn(column: Item[], reorder: boolean): void {
    const wishes = new Map(column.map((item) => [item, this.wish(item, !reorder)]));
    if (reorder) this.sortByWish(column, wishes);
    this.fit(
      column,
      column.map((item) => wishes.get(item)!),
    );
  }

  private wish(item: Item, straighten: boolean): number {
    const among = straighten && item.edge !== undefined ? item.siblings : item.links;
    return among.length === 0 ? item.centre : mean(among.map((other) => other.centre));
  }

  // A source system's tables stay together, as in ELK's order: by system, then by wish.
  private sortByWish(column: Item[], wishes: Map<Item, number>): void {
    column.sort((a, b) => (a.sourceName ?? "").localeCompare(b.sourceName ?? "") || wishes.get(a)! - wishes.get(b)!);
  }

  // The least-squares centres that keep the column's order and spacing: an isotonic regression on the wish less the
  // cumulative spacing.
  private fit(column: Item[], wishes: number[]): void {
    const offsets: number[] = [];
    column.forEach((item, index) => {
      const previous = column[index - 1];
      offsets.push(
        previous === undefined ? 0 : offsets[index - 1]! + previous.height / 2 + spacing(previous, item) + item.height / 2,
      );
    });
    const fitted = isotonic(wishes.map((wish, index) => wish - offsets[index]!));
    column.forEach((item, index) => {
      item.centre = fitted[index]! + offsets[index]!;
    });
  }

  private placement(): BandPlacement {
    const items = this.columns.flat();
    const smallest = Math.min(...items.map((item) => item.centre - item.height / 2));
    return {
      columns: this.xs.map((x, index) => ({
        x,
        items: this.columns[index]!.map(({ id, edge }) => (edge === undefined ? { id } : { id, edge })),
      })),
      tops: new Map(items.map((item) => [item.id, item.centre - item.height / 2 - smallest])),
    };
  }
}

// The x of each column and each node's column: a band's sub-columns, one after the other (UI spec §6.5).
function columnLayout(nodes: BandNode[], edges: LayoutEdge[]): { xs: number[]; columnOf: Map<string, number> } {
  const subColumn = subColumns(nodes, edges);
  const xs: number[] = [];
  const columnOf = new Map<string, number>();
  let bandStart = 0;
  const bands = [...groupBy(nodes, (node) => BAND[node.layer])].sort(([a], [b]) => a - b);
  for (const [, band] of bands) {
    const first = xs.length;
    const last = Math.max(...band.map((node) => subColumn.get(node.id)!));
    for (let column = 0; column <= last; column++) xs.push(bandStart + column * (NODE_WIDTH + SUB_COLUMN_GAP));
    for (const node of band) columnOf.set(node.id, first + subColumn.get(node.id)!);
    bandStart += (last + 1) * NODE_WIDTH + last * SUB_COLUMN_GAP + BAND_GAP;
  }
  return { xs, columnOf };
}

// The space between two neighbours in a column.
function spacing(above: { edge?: string | undefined }, below: { edge?: string | undefined }): number {
  if (above.edge !== undefined && below.edge !== undefined) return SLOT_SLOT_GAP;
  return above.edge !== undefined || below.edge !== undefined ? SLOT_NODE_GAP : ROW_GAP;
}

// Pool adjacent violators: the non-decreasing sequence closest to `values` in least squares.
function isotonic(values: number[]): number[] {
  const blocks: { sum: number; count: number }[] = [];
  for (const value of values) {
    blocks.push({ sum: value, count: 1 });
    while (blocks.length > 1) {
      const [before, last] = [blocks.at(-2)!, blocks.at(-1)!];
      if (before.sum / before.count < last.sum / last.count) break;
      blocks.splice(-2, 2, { sum: before.sum + last.sum, count: before.count + last.count });
    }
  }
  return blocks.flatMap(({ sum, count }) => Array<number>(count).fill(sum / count));
}

function mean(values: number[]): number {
  return values.reduce((total, value) => total + value, 0) / values.length;
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
