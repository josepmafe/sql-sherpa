import { Text } from "@mantine/core";
import {
  IconAlertTriangle,
  IconCircleCheck,
  IconCircleDashed,
  IconCircleX,
  IconPlayerSkipForward,
  IconSquareDashed,
} from "@tabler/icons-react";
import { Handle, type Node, type NodeProps, Position } from "@xyflow/react";
import type { ComponentType } from "react";

import type { components } from "../../api/schema";
import { NODE_HEIGHT, NODE_WIDTH } from "./layout";
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
}

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

// Low enough to see the dimming, high enough that a dimmed node's text stays legible.
const DIMMED_OPACITY = 0.5;

export function GraphNode({ data }: NodeProps<Node<GraphNodeData>>) {
  const status = STATUS[data.status];
  const failed = data.status === "failed";
  const stripe = LAYER_COLOR[data.layer];
  return (
    <>
      <Handle type="target" position={Position.Left} isConnectable={false} style={{ opacity: 0 }} />
      <button
        type="button"
        data-dimmed={data.dimmed ? "" : undefined}
        aria-pressed={data.selected}
        style={{
          width: NODE_WIDTH,
          height: NODE_HEIGHT,
          boxSizing: "border-box",
          padding: "6px 10px",
          textAlign: "left",
          cursor: "pointer",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background: "light-dark(var(--mantine-color-white), var(--mantine-color-dark-6))",
          color: "var(--mantine-color-text)",
          // Longhands: a shorthand set after the stripe would reset it when the status changes.
          borderStyle: "solid",
          borderWidth: failed ? 2 : 1,
          borderColor: failed ? RED : "var(--mantine-color-default-border)",
          borderLeftWidth: 6,
          borderLeftColor: `light-dark(var(--mantine-color-${stripe}-8), var(--mantine-color-${stripe}-4))`,
          borderRadius: 4,
          opacity: data.dimmed ? DIMMED_OPACITY : 1,
          outline: data.selected ? "3px solid var(--mantine-primary-color-filled)" : undefined,
          outlineOffset: 2,
        }}
      >
        <Text component="span" fw={600} size="sm" truncate style={{ display: "block" }}>
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
      <Handle type="source" position={Position.Right} isConnectable={false} style={{ opacity: 0 }} />
    </>
  );
}
