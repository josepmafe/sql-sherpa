import { Text, VisuallyHidden } from "@mantine/core";
import {
  IconAlertTriangle,
  IconChevronDown,
  IconChevronRight,
  IconCircleCheck,
  IconCircleDashed,
  IconCircleX,
  IconPlayerSkipForward,
  IconSquareDashed,
} from "@tabler/icons-react";
import { Handle, type Node, type NodeProps, Position, useUpdateNodeInternals } from "@xyflow/react";
import { type ComponentType, type MouseEvent, useEffect, useId } from "react";

import type { components } from "../../api/schema";
import { KindLabel, ResolutionLabel } from "./ColumnLabels";
import type { RowView } from "./columnFocus";
import { COLUMN_ROW_HEIGHT, NODE_HEIGHT, NODE_WIDTH } from "./layout";
import { useRevealNode } from "./reveal";
import type { Layer } from "./url";

type NodeStatus = components["schemas"]["NodeStatus"];

export interface GraphNodeData extends Record<string, unknown> {
  name: string;
  layer: Layer;
  materialization: string | null;
  status: NodeStatus;
  failingTests: number;
  selected: boolean;
  dimmed: boolean;
  // The node's height: NODE_HEIGHT plus its rows. The canvas hands the same number to the layout.
  height: number;
  // Column rows, from the node's columns: none while collapsed. `more` counts the columns a focus leaves out.
  rows: RowView[];
  more: number;
  // The chevron: shown for a node with columns; `open` when the user (or a focus) opened it; disabled, with the
  // reason as its description, while column lineage is unavailable.
  hasColumns: boolean;
  open: boolean;
  unavailable: string | undefined;
  onToggle: () => void;
  onFocusColumn: (column: string) => void;
}

const STRIPE_WIDTH = 6;

// Shade 8 for the stripe on white and shade 4 on the dark body keep it above 3:1 (non-text contrast); the text never
// sits on a layer colour, so its contrast is the theme's own.
const LAYER_COLOR: Record<Layer, string> = {
  source: "gray",
  seed: "cyan",
  staging: "blue",
  snapshot: "violet",
  intermediate: "indigo",
  marts: "green",
  exposure: "pink",
  other: "gray",
};

export interface StatusView {
  label: string;
  Icon: ComponentType<{ size?: number; "aria-hidden"?: boolean }>;
  // The icon's colour (3:1 is enough for a graphic); the label stays in the theme's text colour, or its AA dimmed grey.
  color: string;
  grey?: boolean;
}
export const RED = "light-dark(var(--mantine-color-red-9), var(--mantine-color-red-4))";
export const ORANGE = "light-dark(var(--mantine-color-orange-9), var(--mantine-color-orange-4))";
export const GREEN = "light-dark(var(--mantine-color-green-9), var(--mantine-color-green-4))";
export const GREY = "var(--mantine-color-dimmed)";
export const STATUS: Partial<Record<NodeStatus, StatusView>> = {
  failed: { label: "failed", Icon: IconCircleX, color: RED },
  warned: { label: "warned", Icon: IconAlertTriangle, color: ORANGE },
  skipped: { label: "skipped", Icon: IconPlayerSkipForward, color: GREY, grey: true },
  ok: { label: "ok", Icon: IconCircleCheck, color: GREEN },
  not_in_build: { label: "not in the last build", Icon: IconCircleDashed, color: GREY, grey: true },
  inlined: { label: "inlined", Icon: IconSquareDashed, color: GREY, grey: true },
};

// A dimmed node fades its stripe, border and background; the text keeps the theme's contrast.
const DIMMED_STRIPE = "light-dark(var(--mantine-color-gray-4), var(--mantine-color-dark-4))";
const DIMMED_BORDER = "light-dark(var(--mantine-color-gray-3), var(--mantine-color-dark-5))";
const DIMMED_BACKGROUND = "light-dark(var(--mantine-color-gray-0), var(--mantine-color-dark-7))";

export function GraphNode({ id, data }: NodeProps<Node<GraphNodeData>>) {
  const reveal = useRevealNode();
  const updateNodeInternals = useUpdateNodeInternals();
  const status = STATUS[data.status];
  const failed = data.status === "failed";
  const stripe = LAYER_COLOR[data.layer];
  const border = failed ? 2 : 1;
  // A handle belongs to a row, so React Flow measures it again when the rows change, not only when the size does.
  const rowsKey = data.rows.map((row) => row.column.name).join("|");
  useEffect(() => {
    updateNodeInternals(id);
  }, [id, rowsKey, updateNodeInternals]);

  return (
    <>
      <Handle type="target" position={Position.Left} isConnectable={false} style={{ opacity: 0, top: NODE_HEIGHT / 2 }} />
      <div
        style={{
          width: NODE_WIDTH,
          height: data.height,
          boxSizing: "border-box",
          display: "flex",
          flexDirection: "column",
          background: data.dimmed
            ? DIMMED_BACKGROUND
            : "light-dark(var(--mantine-color-white), var(--mantine-color-dark-6))",
          // Longhands: a shorthand set after the stripe would reset it when the status changes.
          borderStyle: "solid",
          borderWidth: border,
          borderColor: data.dimmed ? DIMMED_BORDER : failed ? RED : "var(--mantine-color-default-border)",
          borderLeftWidth: STRIPE_WIDTH,
          borderLeftColor: data.dimmed
            ? DIMMED_STRIPE
            : `light-dark(var(--mantine-color-${stripe}-8), var(--mantine-color-${stripe}-4))`,
          borderRadius: 4,
          outline: data.selected ? "3px solid var(--mantine-primary-color-filled)" : undefined,
          outlineOffset: 2,
        }}
      >
        <div style={{ position: "relative", flex: "none", height: NODE_HEIGHT - 2 * border }}>
          <button
            type="button"
            data-dimmed={data.dimmed ? "" : undefined}
            aria-pressed={data.selected}
            onFocus={() => reveal(id)}
            style={{
              width: "100%",
              height: "100%",
              boxSizing: "border-box",
              padding: "6px 10px",
              border: 0,
              background: "transparent",
              textAlign: "left",
              cursor: "pointer",
              display: "flex",
              flexDirection: "column",
              justifyContent: "space-between",
              color: "var(--mantine-color-text)",
            }}
          >
            <Text component="span" fw={600} size="sm" truncate style={{ display: "block", paddingRight: 22 }}>
              {data.name}
            </Text>
            <Text component="span" size="xs" c="dimmed">
              {data.layer}
              {data.materialization !== null && (
                <span style={{ marginLeft: 6, padding: "0 4px", border: "1px solid currentColor", borderRadius: 3 }}>
                  {data.materialization}
                </span>
              )}
            </Text>
            {status !== undefined && (
              <Text
                component="span"
                size="xs"
                fw={500}
                c={status.grey ? "dimmed" : undefined}
                style={{ display: "flex", alignItems: "center", gap: 4 }}
              >
                <span style={{ display: "flex", color: status.color }}>
                  <status.Icon size={14} aria-hidden />
                </span>
                {status.label}
                {failed && data.failingTests > 0 && ` · ${data.failingTests} failing ${data.failingTests === 1 ? "test" : "tests"}`}
              </Text>
            )}
          </button>
          {data.hasColumns && <Chevron data={data} />}
        </div>
        {data.rows.map((row) => (
          <ColumnRow key={row.column.name} row={row} nodeId={id} data={data} />
        ))}
        {data.more > 0 && (
          <button type="button" onClick={(event) => stop(event, data.onToggle)} style={ROW_BUTTON}>
            <Text component="span" size="xs" c="dimmed">
              +{data.more} more
            </Text>
          </button>
        )}
      </div>
      <Handle type="source" position={Position.Right} isConnectable={false} style={{ opacity: 0, top: NODE_HEIGHT / 2 }} />
    </>
  );
}

function Chevron({ data }: { data: GraphNodeData }) {
  const why = useId();
  const Icon = data.open ? IconChevronDown : IconChevronRight;
  return (
    <>
      <button
        type="button"
        aria-label={`Columns of ${data.name}`}
        aria-expanded={data.open}
        aria-describedby={data.unavailable === undefined ? undefined : why}
        disabled={data.unavailable !== undefined}
        title={data.unavailable}
        onClick={(event) => stop(event, data.onToggle)}
        style={{
          position: "absolute",
          top: 4,
          right: 4,
          display: "flex",
          padding: 2,
          border: 0,
          borderRadius: 3,
          background: "transparent",
          color: "var(--mantine-color-text)",
          cursor: data.unavailable === undefined ? "pointer" : "not-allowed",
          opacity: data.unavailable === undefined ? 1 : 0.5,
        }}
      >
        <Icon size={16} aria-hidden />
      </button>
      {data.unavailable !== undefined && <VisuallyHidden id={why}>{data.unavailable}</VisuallyHidden>}
    </>
  );
}

// One handle per column and side, so a column edge joins row to row. The row sits inside the stripe, so the handle
// on the left is moved out to the card's edge.
function ColumnRow({ row, nodeId, data }: { row: RowView; nodeId: string; data: GraphNodeData }) {
  const reveal = useRevealNode();
  const { column, role } = row;
  const handle = `col:${column.name}`;
  return (
    <div style={{ position: "relative", flex: "none", height: COLUMN_ROW_HEIGHT }}>
      <Handle type="target" position={Position.Left} id={handle} isConnectable={false} style={{ opacity: 0, left: -STRIPE_WIDTH }} />
      <button
        type="button"
        aria-pressed={role === undefined ? undefined : role === "focus"}
        data-dimmed={role === "other" ? "" : undefined}
        title={column.name}
        onClick={(event) => stop(event, () => data.onFocusColumn(column.name))}
        onFocus={() => reveal(nodeId)}
        style={{
          ...ROW_BUTTON,
          fontWeight: role === "focus" ? 700 : undefined,
          background: role === "focus" ? "var(--mantine-primary-color-light)" : "transparent",
          outline: role === "focus" ? "2px solid var(--mantine-primary-color-filled)" : undefined,
          outlineOffset: -2,
          color: role === "other" ? "var(--mantine-color-dimmed)" : "var(--mantine-color-text)",
        }}
      >
        <Text component="span" size="xs" truncate style={{ flex: 1, minWidth: 0, fontWeight: "inherit" }}>
          {column.name}
        </Text>
        {" "}
        <Text component="span" size="xs" c="dimmed" style={{ flex: "none", fontSize: 11 }}>
          <KindLabel kind={column.kind} />
        </Text>
        {column.resolution !== "full" && (
          <>
            {" "}
            <Text component="span" size="xs" c="dimmed" style={{ flex: "none", fontSize: 11 }}>
              <ResolutionLabel resolution={column.resolution} />
            </Text>
          </>
        )}
      </button>
      <Handle type="source" position={Position.Right} id={handle} isConnectable={false} style={{ opacity: 0, right: -1 }} />
    </div>
  );
}

const ROW_BUTTON = {
  width: "100%",
  height: COLUMN_ROW_HEIGHT,
  boxSizing: "border-box",
  padding: "0 8px",
  border: 0,
  display: "flex",
  alignItems: "center",
  gap: 6,
  textAlign: "left",
  cursor: "pointer",
  background: "transparent",
  color: "var(--mantine-color-text)",
} as const;

// A click on the chevron or a row would select the node too (React Flow's node click), and it is not that.
function stop(event: MouseEvent, action: () => void): void {
  event.stopPropagation();
  action();
}
