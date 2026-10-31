import { MantineProvider } from "@mantine/core";
import { type ComponentProps, useMemo, useState } from "react";
import { vi } from "vitest";

import { focusView } from "../../src/screens/lineage/columnFocus";
import { LineageCanvas } from "../../src/screens/lineage/LineageCanvas";
import { orderNodes } from "../../src/screens/lineage/layout";
import {
  type ColumnFocusView,
  columnFocusView,
  columnView,
  focusColumn,
  type LineageEdge,
  type LineageNode,
  lineageNode,
} from "../fixtures/api";

/** The mocked `orderNodes` (see each file's vi.mock of the layout module) ranks the nodes in input order. */
export function rankInInputOrder(): void {
  vi.mocked(orderNodes)
    .mockReset()
    .mockImplementation((nodes) => Promise.resolve(new Map(nodes.map((node, index) => [node.id, index]))));
}

/** A node `model.s.<name>`; staging unless the patch says otherwise. */
export function node(name: string, patch: Partial<LineageNode> = {}): LineageNode {
  return lineageNode({
    unique_id: `model.s.${name}`,
    name,
    layer: "staging",
    domain: null,
    materialization: "view",
    ...patch,
  });
}

export const NO_NODES: LineageNode[] = [];
export const NO_EDGES: LineageEdge[] = [];

type CanvasProps = ComponentProps<typeof LineageCanvas>;

/** The props of a canvas with nothing selected, expanded or focused. */
export function canvasProps(overrides: Partial<CanvasProps> = {}): CanvasProps {
  return {
    nodes: NO_NODES,
    edges: NO_EDGES,
    selected: undefined,
    column: undefined,
    onSelect: () => {},
    focus: undefined,
    expanded: new Set(),
    onToggle: () => {},
    onFocusColumn: () => {},
    columnsUnavailable: undefined,
    failingOnly: false,
    layersFiltered: false,
    buildState: "ok",
    ...overrides,
  };
}

interface HarnessProps {
  nodes: LineageNode[];
  edges: LineageEdge[];
  initial?: string;
  initialColumn?: string;
  initialExpanded?: string[];
  // What the focus endpoint answers once a column is focused.
  focusData?: ColumnFocusView;
  columnsUnavailable?: string;
}

/** A canvas that keeps the selection, the focused column and the expanded nodes, as the screen does. */
export function CanvasHarness({
  nodes,
  edges,
  initial,
  initialColumn,
  initialExpanded = [],
  focusData,
  columnsUnavailable,
}: HarnessProps) {
  const [selected, setSelected] = useState(initial);
  const [column, setColumn] = useState(initialColumn);
  const [expanded, setExpanded] = useState(new Set(initialExpanded));
  const focus = useMemo(
    () =>
      column === undefined || focusData === undefined
        ? undefined
        : focusView(focusData, new Set(nodes.map((n) => n.unique_id))),
    [column, focusData, nodes],
  );
  return (
    <MantineProvider env="test">
      <LineageCanvas
        {...canvasProps({ nodes, edges, selected, column, focus, expanded, columnsUnavailable })}
        onSelect={(id) => {
          setSelected(id);
          setColumn(undefined);
        }}
        onToggle={(id) =>
          setExpanded((now) => {
            const next = new Set(now);
            if (!next.delete(id)) next.add(id);
            return next;
          })
        }
        onFocusColumn={(id, name) => {
          setSelected(id);
          setColumn(name);
        }}
      />
    </MantineProvider>
  );
}

// A staging and a marts node with columns, the table edge between them, and the focus of fct_orders.net_revenue (it
// reads stg_orders.amount).
export const FCT = "model.s.fct_orders";
export const STG = "model.s.stg_orders";
export const COLUMN_NODES = [
  node("stg_orders", {
    columns: [
      columnView({ name: "amount", kind: "rename" }),
      columnView({ name: "quantity", kind: "passthrough" }),
      columnView({ name: "order_id", kind: "source" }),
    ],
  }),
  node("fct_orders", {
    layer: "marts",
    columns: [
      columnView({ name: "net_revenue", kind: "transformed", resolution: "partial" }),
      columnView({ name: "units", kind: "row_count" }),
      columnView({ name: "order_id", kind: "passthrough" }),
    ],
  }),
];
export const COLUMN_EDGES: LineageEdge[] = [{ source: STG, target: FCT }];
export const COLUMN_FOCUS = columnFocusView({
  focus: { node: FCT, column: "net_revenue" },
  columns: [
    focusColumn({ node: FCT, name: "net_revenue", direction: "focus" }),
    focusColumn({ node: STG, name: "amount", direction: "upstream", kind: "rename" }),
  ],
  edges: [{ parent: { node: STG, column: "amount" }, child: { node: FCT, column: "net_revenue" } }],
});
