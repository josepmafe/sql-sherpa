import "@xyflow/react/dist/style.css";

import { Alert, Text } from "@mantine/core";
import {
  type Edge,
  Controls,
  MarkerType,
  MiniMap,
  type Node,
  Panel,
  type ReactFlowState,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useStore,
} from "@xyflow/react";
import { useEffect, useMemo, useRef } from "react";

import type { components } from "../../api/schema";
import { BAND_LABEL_TYPE, BandLabel, bandLabelNodes } from "./BandLabels";
import { type FocusViewResult, nodeRows } from "./columnFocus";
import { GraphNode, type GraphNodeData, NODE_HANDLE } from "./GraphNode";
import { edgeAppearance, edgeId, FOCUS_EDGE, type Related, relatedNodes } from "./highlight";
import { type BandNode, type LayoutEdge, NODE_WIDTH, nodeHeight } from "./layout";
import { useGraphLayout } from "./useGraphLayout";

type ApiNode = components["schemas"]["LineageNode"];
type ApiEdge = components["schemas"]["LineageEdge"];

const MINIMAP_ABOVE_NODES = 60;
// A whole-project fit would zoom a one-node graph to 2x.
const FIT_MAX_ZOOM = 1;
const GRAPH_NODE = "graph";
const NODE_TYPES = { [GRAPH_NODE]: GraphNode, [BAND_LABEL_TYPE]: BandLabel };
// The width stays in screen pixels at a whole-project zoom, where 1.5 px would shrink to a hairline.
const FIXED_WIDTH = { vectorEffect: "non-scaling-stroke" } as const;
const LEGEND = "Column lineage follows the select list: columns used only in joins or filters are not linked";

interface LineageCanvasProps {
  nodes: ApiNode[];
  edges: ApiEdge[];
  selected: string | undefined;
  // The focused column of the selected node.
  column: string | undefined;
  onSelect: (uniqueId: string | undefined) => void;
  // The focused column's lineage through the nodes shown; undefined while no column is focused.
  focus: FocusViewResult | undefined;
  // The nodes the user opened.
  expanded: Set<string>;
  // While a column is focused: the related nodes that show all their columns (they show only the related ones else).
  rest: Set<string>;
  onToggle: (uniqueId: string) => void;
  onFocusColumn: (uniqueId: string, column: string) => void;
  // Why the nodes cannot expand; undefined when column lineage is available.
  columnsUnavailable: string | undefined;
  // Why an empty graph is empty.
  failingOnly: boolean;
  layersFiltered: boolean;
  buildState: "ok" | "no_build" | "unavailable";
}

export function LineageCanvas(props: LineageCanvasProps) {
  if (props.nodes.length === 0) {
    return (
      <Text c="dimmed" ta="center" py="xl">
        {emptyText(props)}
      </Text>
    );
  }
  return (
    <ReactFlowProvider>
      <Canvas {...props} />
    </ReactFlowProvider>
  );
}

function Canvas({
  nodes,
  edges,
  selected,
  column,
  onSelect,
  focus,
  expanded,
  rest,
  onToggle,
  onFocusColumn,
  columnsUnavailable,
}: LineageCanvasProps) {
  const { layoutNodes, layoutEdges } = useTopology(nodes, edges);
  const rows = useMemo(
    () =>
      new Map(
        nodes.map((node) => {
          // A node the focus reaches shows its related columns; the user's own expansion is back when the focus ends.
          const related = focus?.expanded.get(node.unique_id);
          const rowOptions = {
            expanded: related === undefined ? expanded.has(node.unique_id) : rest.has(node.unique_id),
            related,
            focused: node.unique_id === selected ? column : undefined,
          };
          return [node.unique_id, nodeRows(columnsUnavailable === undefined ? (node.columns ?? []) : [], rowOptions)];
        }),
      ),
    [nodes, expanded, rest, focus, selected, column, columnsUnavailable],
  );
  // A node grows by its rows (and the "+k more" row); ELK has already ordered, so only the stacking reruns.
  const heights = useMemo(
    () => new Map([...rows].map(([id, { rows: shown, more }]) => [id, nodeHeight(shown.length + (more > 0 ? 1 : 0))])),
    [rows],
  );
  const { positions, state } = useGraphLayout(layoutNodes, layoutEdges, heights);
  const { fitView } = useReactFlow();
  const measured = useStore(useMemo(() => measuredIn(layoutNodes), [layoutNodes]));
  const related = useMemo(() => relatedOf(edges, selected, focus), [edges, selected, focus]);

  // Fit once per new order of the nodes, not after a selection or a restack, and only once React Flow has measured
  // every node: a node reports its handles on its own (useUpdateNodeInternals), and a fit asked for earlier is
  // answered by the first node that reports, framing that node alone. The nodes of this layout, not of the previous one,
  // must be in the store. (useNodesInitialized never turns true here: the
  // band labels have no handles.)
  const fittedFor = useRef<unknown>(undefined);
  useEffect(() => {
    if (state !== "ready" || !measured || fittedFor.current === layoutNodes) return;
    fittedFor.current = layoutNodes;
    void fitView({ maxZoom: FIT_MAX_ZOOM });
  }, [state, layoutNodes, measured, fitView]);

  // The DOM order is the Tab order: left to right, top to bottom, as the eye reads it (a band splits into sub-columns,
  // so the band alone does not give the order). A band label sorts before its band's first node.
  const flowNodes: Node[] = useMemo(
    () => {
      const graphNodes: Node[] = nodes.flatMap((node) => {
        const position = positions?.get(node.unique_id);
        const shown = rows.get(node.unique_id);
        if (position === undefined || shown === undefined) return [];
        const height = heights.get(node.unique_id)!;
        const data: GraphNodeData = {
          name: node.name,
          layer: node.layer,
          materialization: node.materialization,
          status: node.status,
          failingTests: node.failing_tests,
          selected: node.unique_id === selected,
          dimmed: related !== undefined && !related.nodes.has(node.unique_id),
          height,
          rows: shown.rows,
          more: shown.more,
          hasColumns: node.columns == null || node.columns.length > 0,
          open: expanded.has(node.unique_id) || focus?.expanded.has(node.unique_id) === true,
          unavailable: columnsUnavailable,
          onToggle: () => onToggle(node.unique_id),
          onFocusColumn: (name) => onFocusColumn(node.unique_id, name),
        };
        return [{ id: node.unique_id, type: GRAPH_NODE, position, width: NODE_WIDTH, height, data }];
      });
      const labels = positions === undefined ? [] : bandLabelNodes(layoutNodes, positions);
      return [...labels, ...graphNodes].sort((a, b) => a.position.x - b.position.x || a.position.y - b.position.y);
    },
    [nodes, layoutNodes, positions, rows, heights, selected, related, expanded, focus, columnsUnavailable, onToggle, onFocusColumn],
  );
  const flowEdges: Edge[] = useMemo(
    () => [
      ...edges.map((edge) => {
        const look = edgeAppearance(related, edgeId(edge));
        return {
          id: edgeId(edge),
          source: edge.source,
          sourceHandle: NODE_HANDLE,
          target: edge.target,
          targetHandle: NODE_HANDLE,
          markerEnd: { type: MarkerType.ArrowClosed, color: look.stroke },
          style: { ...look, ...FIXED_WIDTH },
        };
      }),
      ...(focus?.edges ?? []).map(({ parent, child }) => ({
        id: `col:${parent.node}.${parent.column}->${child.node}.${child.column}`,
        source: parent.node,
        sourceHandle: `col:${parent.column}`,
        target: child.node,
        targetHandle: `col:${child.column}`,
        markerEnd: { type: MarkerType.ArrowClosed, color: FOCUS_EDGE.stroke },
        style: { ...FOCUS_EDGE, ...FIXED_WIDTH },
      })),
    ],
    [edges, related, focus],
  );
  const columnsShown = [...rows.values()].some((shown) => shown.rows.length > 0);

  return (
    <div style={{ height: "60vh", minHeight: 400 }}>
      {state === "error" && (
        <Alert color="red" role="alert" mb="xs">
          Could not lay out the graph.
        </Alert>
      )}
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={NODE_TYPES}
        aria-label="Lineage graph"
        colorMode="system"
        nodesDraggable={false}
        nodesConnectable={false}
        nodesFocusable={false}
        edgesFocusable={false}
        elementsSelectable={false}
        deleteKeyCode={null}
        selectionKeyCode={null}
        minZoom={0.1}
        // A click or Enter on a node button bubbles up to here (and gives the node wrapper its pointer events).
        onNodeClick={(_, node) => node.type === GRAPH_NODE && onSelect(node.id)}
        onPaneClick={() => onSelect(undefined)}
        proOptions={{ hideAttribution: true }}
      >
        <Controls showInteractive={false} fitViewOptions={{ maxZoom: FIT_MAX_ZOOM }} />
        {nodes.length > MINIMAP_ABOVE_NODES && <MiniMap pannable zoomable />}
        {columnsShown && (
          <Panel position="top-right">
            <Text size="xs" c="dimmed" maw={260}>
              {LEGEND}
            </Text>
          </Panel>
        )}
      </ReactFlow>
    </div>
  );
}

// Whether React Flow holds exactly the nodes of this layout, each with a measured size (after a new layout it still
// holds the old ones until its own update has run).
function measuredIn(layout: BandNode[]): (state: ReactFlowState) => boolean {
  const ids = new Set(layout.map((node) => node.id));
  return (state) => {
    const held = [...state.nodeLookup.values()].filter((node) => node.internals.userNode.type === GRAPH_NODE);
    return (
      ids.size > 0 &&
      held.length === ids.size &&
      held.every((node) => ids.has(node.id) && node.measured.width !== undefined && node.measured.height !== undefined)
    );
  };
}

// With a column focused the column edges tell the story: only the nodes that hold a related column stay lit, and every
// table edge fades. Otherwise a selected node lights its chain of nodes.
function relatedOf(edges: ApiEdge[], selected: string | undefined, focus: FocusViewResult | undefined): Related | undefined {
  if (focus !== undefined) return { nodes: new Set(focus.expanded.keys()), edges: new Set() };
  return selected === undefined ? undefined : relatedNodes(edges, selected);
}

// The layout depends on the nodes' ids and layers and on the edges, not on their statuses: a refetch that only
// changes a status keeps the same arrays, so ELK does not run again and the viewport stays.
function useTopology(nodes: ApiNode[], edges: ApiEdge[]): { layoutNodes: BandNode[]; layoutEdges: LayoutEdge[] } {
  const key = JSON.stringify([
    nodes.map((node) => [node.unique_id, node.layer, sourceName(node)]),
    edges.map((edge) => [edge.source, edge.target]),
  ]);
  return useMemo(() => {
    const [ns, es] = JSON.parse(key) as [[string, ApiNode["layer"], string | null][], [string, string][]];
    return {
      layoutNodes: ns.map(([id, layer, source]) => ({ id, layer, sourceName: source ?? undefined })),
      layoutEdges: es.map(([source, target]) => ({ source, target })),
    };
  }, [key]);
}

// "source.<project>.<source name>.<table>": a source system's tables are kept together.
function sourceName(node: ApiNode): string | null {
  return node.unique_id.startsWith("source.") ? (node.unique_id.split(".")[2] ?? null) : null;
}

function emptyText({ failingOnly, layersFiltered, buildState }: LineageCanvasProps): string {
  if (failingOnly && buildState !== "ok") return "No build status to filter on";
  if (failingOnly) return layersFiltered ? "No failed node in the layers shown" : "Nothing failed in the last full build";
  if (layersFiltered) return "No node in the layers shown";
  return "No node matches this selector";
}
