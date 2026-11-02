import type { Edge, ReactFlowProps } from "@xyflow/react";
import { render, screen } from "@testing-library/react";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { COLUMN_EDGES, COLUMN_FOCUS, COLUMN_NODES, CanvasHarness, FCT, node, rankInInputOrder, STG } from "./canvasHarness";

// React Flow draws no edge in jsdom without sizes; the handles an edge names are what the canvas decides, so the
// edges it hands to React Flow are read here.
let edgesGiven: Edge[] = [];
vi.mock("@xyflow/react", async (importOriginal) => {
  const original = await importOriginal<typeof import("@xyflow/react")>();
  return {
    ...original,
    ReactFlow: (props: ReactFlowProps) => {
      edgesGiven = props.edges ?? [];
      return createElement(original.ReactFlow, props);
    },
  };
});
vi.mock("../../src/screens/lineage/layout", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/screens/lineage/layout")>()),
  orderNodes: vi.fn(),
}));

describe("LineageCanvas edge handles", () => {
  beforeEach(() => {
    rankInInputOrder();
    edgesGiven = [];
  });

  it("a table edge uses the nodes' own handles, never a column row's", async () => {
    render(<CanvasHarness nodes={COLUMN_NODES} edges={COLUMN_EDGES} initialExpanded={[STG, FCT]} />);
    await screen.findByRole("button", { name: /^net_revenue\b/ });

    expect(edgesGiven).toHaveLength(1);
    expect(edgesGiven[0]).toMatchObject({ source: STG, target: FCT, sourceHandle: "node", targetHandle: "node" });
    expect(document.querySelector('[data-nodeid="' + STG + '"][data-handleid="node"].source')).not.toBeNull();
    expect(document.querySelector('[data-nodeid="' + FCT + '"][data-handleid="node"].target')).not.toBeNull();
  });

  it("a column edge joins the two column rows", async () => {
    render(<CanvasHarness nodes={COLUMN_NODES} edges={COLUMN_EDGES} initial={FCT} initialColumn="net_revenue" focusData={COLUMN_FOCUS} />);
    await screen.findByRole("button", { name: /^net_revenue\b/ });

    expect(edgesGiven.filter((edge) => edge.sourceHandle !== "node")).toMatchObject([
      { source: STG, sourceHandle: "col:amount", target: FCT, targetHandle: "col:net_revenue" },
    ]);
  });

  it("table and column edges are routed, and a column edge takes its table edge's waypoints", async () => {
    const between = node("int_orders", { layer: "intermediate" });
    const edges = [...COLUMN_EDGES, { source: STG, target: between.unique_id }, { source: between.unique_id, target: FCT }];
    render(<CanvasHarness nodes={[...COLUMN_NODES, between]} edges={edges} initial={FCT} initialColumn="net_revenue" focusData={COLUMN_FOCUS} />);
    await screen.findByRole("button", { name: /^net_revenue\b/ });

    const skipping = edgesGiven.find((edge) => edge.id === `${STG}->${FCT}`)!;
    const column = edgesGiven.find((edge) => edge.id.startsWith("col:"))!;
    const adjacent = edgesGiven.find((edge) => edge.id === `${STG}->${between.unique_id}`)!;
    expect([skipping.type, column.type, adjacent.type]).toEqual(["routed", "routed", "routed"]);
    // The edge from staging to marts skips the intermediate column: one gap, left and right.
    expect(skipping.data!.waypoints).toHaveLength(2);
    expect(column.data!.waypoints).toEqual(skipping.data!.waypoints);
    expect(adjacent.data!.waypoints).toEqual([]);
  });
});
