import { Text } from "@mantine/core";
import { Handle, Position } from "@xyflow/react";
import type { MouseEvent } from "react";

import { KindLabel, ResolutionLabel } from "./ColumnLabels";
import type { RowView } from "./columnFocus";
import { COLUMN_ROW_HEIGHT } from "./layout";
import { useRevealNode } from "./reveal";

// The node card's left border (its layer stripe); the row sits inside it.
export const STRIPE_WIDTH = 6;

export const ROW_BUTTON = {
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

interface ColumnRowProps {
  row: RowView;
  // Its place among the node's rows, for keeping it in view.
  index: number;
  nodeId: string;
  onFocusColumn: (column: string) => void;
}

// One handle per column and side, so a column edge joins row to row. The row sits inside the stripe, so the handle
// on the left is moved out to the card's edge.
export function ColumnRow({ row, index, nodeId, onFocusColumn }: ColumnRowProps) {
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
        onClick={(event) => stop(event, () => onFocusColumn(column.name))}
        onFocus={() => reveal(nodeId, index)}
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

// A click on the chevron or a row would select the node too (React Flow's node click), and it is not that.
export function stop(event: MouseEvent, action: () => void): void {
  event.stopPropagation();
  action();
}
