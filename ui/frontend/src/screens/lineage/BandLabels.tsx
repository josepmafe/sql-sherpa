import { Text } from "@mantine/core";
import type { Node, NodeProps } from "@xyflow/react";

import { BAND, type BandNode, NODE_WIDTH, type Point } from "./layout";
import type { Layer } from "./url";

const LABEL_HEIGHT = 24;
const LABEL_GAP = 16;

export const BAND_LABEL_TYPE = "bandLabel";

type BandLabelData = Node<{ label: string }, typeof BAND_LABEL_TYPE>;

const LABEL: Record<Layer, string> = {
  source: "Raw",
  seed: "Seeds",
  staging: "Staging",
  snapshot: "Snapshots",
  intermediate: "Intermediate",
  other: "Intermediate",
  marts: "Marts",
  exposure: "Exposures",
};

// One header above each band shown, aligned with the band's left edge. A header is a plain React Flow node that cannot
// be selected, dragged or focused, and holds no button: it is not a graph node.
export function bandLabelNodes(nodes: BandNode[], positions: Map<string, Point>): Node[] {
  const bands = new Map<number, { label: string; x: number }>();
  for (const { id, layer } of nodes) {
    const at = positions.get(id);
    if (at === undefined) continue;
    const band = bands.get(BAND[layer]);
    bands.set(BAND[layer], { label: LABEL[layer], x: Math.min(band?.x ?? at.x, at.x) });
  }
  return [...bands].map(([index, { label, x }]) => ({
    id: `${BAND_LABEL_TYPE}:${index}`,
    type: BAND_LABEL_TYPE,
    position: { x, y: -LABEL_HEIGHT - LABEL_GAP },
    width: NODE_WIDTH,
    height: LABEL_HEIGHT,
    data: { label },
    draggable: false,
    selectable: false,
    focusable: false,
    connectable: false,
    style: { pointerEvents: "none" },
  }));
}

export function BandLabel({ data }: NodeProps<BandLabelData>) {
  return (
    <Text fw={700} size="sm" c="dimmed" tt="uppercase" style={{ letterSpacing: "0.04em" }}>
      {data.label}
    </Text>
  );
}
