import "@xyflow/react/dist/style.css";

import { Alert, Text } from "@mantine/core";
import {
  type Edge,
  MarkerType,
  MiniMap,
  type Node,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
} from "@xyflow/react";
import { useEffect, useMemo } from "react";

import type { components } from "../../api/schema";
import { GraphNode, type GraphNodeData } from "./GraphNode";
import { edgeId, relatedNodes } from "./highlight";
import { NODE_HEIGHT, NODE_WIDTH } from "./layout";
import { useGraphLayout } from "./useGraphLayout";

type ApiNode = components["schemas"]["LineageNode"];
type ApiEdge = components["schemas"]["LineageEdge"];

const MINIMAP_ABOVE_NODES = 60;
const DIMMED_EDGE_OPACITY = 0.15;
const NODE_TYPES = { graph: GraphNode };

interface LineageCanvasProps {
  nodes: ApiNode[];
  edges: ApiEdge[];
  selected: string | undefined;
  onSelect: (uniqueId: string | undefined) => void;
}

export function LineageCanvas(props: LineageCanvasProps) {
  if (props.nodes.length === 0) {
    return (
      <Text c="dimmed" ta="center" py="xl">
        No node matches this selector
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
  const layoutNodes = useMemo(() => nodes.map((node) => ({ id: node.unique_id, layer: node.layer })), [nodes]);
  const { positions, state } = useGraphLayout(layoutNodes, edges);
  const { fitView } = useReactFlow();
  const related = useMemo(() => (selected === undefined ? undefined : relatedNodes(edges, selected)), [edges, selected]);

  // Fit after each layout, not after a selection.
  useEffect(() => {
    if (state === "ready") void fitView();
  }, [state, positions, fitView]);

  // Esc clears the selection, unless it was meant for a text field or a menu.
  useEffect(() => {
    if (selected === undefined) return;
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (event.key === "Escape" && !target.closest("input, textarea, [role=listbox]")) onSelect(undefined);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selected, onSelect]);

  const flowNodes: Node<GraphNodeData>[] = useMemo(
    () =>
      nodes.flatMap((node) => {
        const position = positions?.get(node.unique_id);
        if (position === undefined) return [];
        return {
          id: node.unique_id,
          type: "graph",
          position,
          width: NODE_WIDTH,
          height: NODE_HEIGHT,
          data: {
            name: node.name,
            layer: node.layer,
            materialization: node.materialization,
            status: node.status,
            failingTests: node.failing_tests,
            selected: node.unique_id === selected,
            dimmed: related !== undefined && !related.nodes.has(node.unique_id),
          },
        };
      }),
    [nodes, positions, selected, related],
  );
  const flowEdges: Edge[] = useMemo(
    () =>
      edges.map((edge) => ({
        id: edgeId(edge),
        source: edge.source,
        target: edge.target,
        markerEnd: { type: MarkerType.ArrowClosed },
        style: related !== undefined && !related.edges.has(edgeId(edge)) ? { opacity: DIMMED_EDGE_OPACITY } : undefined,
      })),
    [edges, related],
  );

  return (
    <div style={{ height: "70vh", minHeight: 400 }} aria-label="Lineage graph">
      {state === "error" && (
        <Alert color="red" role="alert" mb="xs">
          Could not lay out the graph.
        </Alert>
      )}
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={NODE_TYPES}
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
        onNodeClick={(_, node) => onSelect(node.id)}
        onPaneClick={() => onSelect(undefined)}
        proOptions={{ hideAttribution: true }}
      >
        {nodes.length > MINIMAP_ABOVE_NODES && <MiniMap pannable zoomable />}
      </ReactFlow>
    </div>
  );
}
