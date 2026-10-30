import "@xyflow/react/dist/style.css";

import { Alert, Text } from "@mantine/core";
import {
  type Edge,
  Controls,
  MarkerType,
  MiniMap,
  type Node,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useStore,
} from "@xyflow/react";
import { useEffect, useMemo } from "react";

import type { components } from "../../api/schema";
import { BAND_LABEL_TYPE, BandLabel, bandLabelNodes } from "./BandLabels";
import { GraphNode, type GraphNodeData } from "./GraphNode";
import { edgeAppearance, edgeId, relatedNodes } from "./highlight";
import { type BandNode, type LayoutEdge, NODE_HEIGHT, NODE_WIDTH } from "./layout";
import { useRevealNode } from "./reveal";
import { useGraphLayout } from "./useGraphLayout";

type ApiNode = components["schemas"]["LineageNode"];
type ApiEdge = components["schemas"]["LineageEdge"];

const MINIMAP_ABOVE_NODES = 60;
// A whole-project fit would zoom a one-node graph to 2x.
const FIT_MAX_ZOOM = 1;
const NODE_TYPES = { graph: GraphNode, [BAND_LABEL_TYPE]: BandLabel };
// No node reports a height yet: every node is NODE_HEIGHT.
const NO_HEIGHTS = new Map<string, number>();

interface LineageCanvasProps {
  nodes: ApiNode[];
  edges: ApiEdge[];
  selected: string | undefined;
  onSelect: (uniqueId: string | undefined) => void;
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

function Canvas({ nodes, edges, selected, onSelect }: LineageCanvasProps) {
  const { layoutNodes, layoutEdges } = useTopology(nodes, edges);
  const { positions, state } = useGraphLayout(layoutNodes, layoutEdges, NO_HEIGHTS);
  const { fitView } = useReactFlow();
  const reveal = useRevealNode();
  const canvasWidth = useStore((flow) => flow.width);
  const canvasHeight = useStore((flow) => flow.height);
  const related = useMemo(() => (selected === undefined ? undefined : relatedNodes(edges, selected)), [edges, selected]);

  // Fit after each new order of the nodes, not after a selection or a restack.
  useEffect(() => {
    if (state === "ready") void fitView({ maxZoom: FIT_MAX_ZOOM });
  }, [state, layoutNodes, layoutEdges, fitView]);

  // The panel opening narrows the canvas under the viewport: keep the selected node in sight.
  useEffect(() => {
    if (selected !== undefined) reveal(selected);
  }, [selected, canvasWidth, canvasHeight, reveal]);

  // The DOM order is the Tab order: left to right, top to bottom, as the eye reads it (a band splits into sub-columns,
  // so the band alone does not give the order). A band label sorts before its band's first node.
  const flowNodes: Node[] = useMemo(
    () => {
      const graphNodes: Node[] = nodes.flatMap((node) => {
        const position = positions?.get(node.unique_id);
        if (position === undefined) return [];
        const data: GraphNodeData = {
          name: node.name,
          layer: node.layer,
          materialization: node.materialization,
          status: node.status,
          failingTests: node.failing_tests,
          selected: node.unique_id === selected,
          dimmed: related !== undefined && !related.nodes.has(node.unique_id),
        };
        return [{ id: node.unique_id, type: "graph", position, width: NODE_WIDTH, height: NODE_HEIGHT, data }];
      });
      const labels = positions === undefined ? [] : bandLabelNodes(layoutNodes, positions);
      return [...labels, ...graphNodes].sort((a, b) => a.position.x - b.position.x || a.position.y - b.position.y);
    },
    [nodes, layoutNodes, positions, selected, related],
  );
  const flowEdges: Edge[] = useMemo(
    () =>
      edges.map((edge) => {
        const look = edgeAppearance(related, edgeId(edge));
        return {
          id: edgeId(edge),
          source: edge.source,
          target: edge.target,
          markerEnd: { type: MarkerType.ArrowClosed, color: look.stroke },
          // The width stays in screen pixels at a whole-project zoom, where 1.5 px would shrink to a hairline.
          style: { ...look, vectorEffect: "non-scaling-stroke" },
        };
      }),
    [edges, related],
  );

  return (
    <div style={{ height: "70vh", minHeight: 400 }}>
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
        onNodeClick={(_, node) => node.type === "graph" && onSelect(node.id)}
        onPaneClick={() => onSelect(undefined)}
        proOptions={{ hideAttribution: true }}
      >
        <Controls showInteractive={false} fitViewOptions={{ maxZoom: FIT_MAX_ZOOM }} />
        {nodes.length > MINIMAP_ABOVE_NODES && selected === undefined && <MiniMap pannable zoomable />}
      </ReactFlow>
    </div>
  );
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
