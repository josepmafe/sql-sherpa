import { BaseEdge, type Edge, type EdgeProps } from "@xyflow/react";

import { edgePath } from "./edgeRoute";
import type { Point } from "./layout";

export const ROUTED_EDGE = "routed";

export type RoutedEdgeData = { waypoints: Point[] };
type RoutedEdgeType = Edge<RoutedEdgeData, typeof ROUTED_EDGE>;

// An edge that runs through the gaps its layout reserved in the columns between its nodes. The ends stay on the real
// handles (a column edge ends on a row's), the waypoints in between come from the layout.
export function RoutedEdge({ id, sourceX, sourceY, targetX, targetY, markerEnd, style, data }: EdgeProps<RoutedEdgeType>) {
  const path = edgePath({ x: sourceX, y: sourceY }, data?.waypoints ?? [], { x: targetX, y: targetY });
  return <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />;
}
