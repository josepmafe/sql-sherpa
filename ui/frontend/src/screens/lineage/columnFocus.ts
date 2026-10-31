import type { components } from "../../api/schema";

export type ColumnFocusView = components["schemas"]["ColumnFocusView"];
export type ColumnEdge = components["schemas"]["FocusEdge"];
export type ColumnView = components["schemas"]["ColumnView"];

export interface FocusViewResult {
  // Each shown node with related columns, and the columns of it that are related.
  expanded: Map<string, Set<string>>;
  // The column edges whose two ends are both in a shown node.
  edges: ColumnEdge[];
  // The related columns in nodes that are not shown.
  outside: number;
}

// A focused column's lineage seen through the nodes the graph shows (UI spec §6.5): a related column in a node the
// selector or a hidden layer leaves out is counted, never drawn.
export function focusView(focus: ColumnFocusView, shown: Set<string>): FocusViewResult {
  const expanded = new Map<string, Set<string>>();
  let outside = 0;
  for (const { node, name } of focus.columns) {
    if (!shown.has(node)) outside += 1;
    else if (expanded.has(node)) expanded.get(node)?.add(name);
    else expanded.set(node, new Set([name]));
  }
  const edges = focus.edges.filter(({ parent, child }) => shown.has(parent.node) && shown.has(child.node));
  return { expanded, edges, outside };
}

export type RowRole = "focus" | "related" | "other";
export interface RowView {
  column: ColumnView;
  // Only while a column is focused and this node holds related columns.
  role: RowRole | undefined;
}

interface RowOptions {
  // The user opened the node (or asked for the rest of a focused one).
  expanded: boolean;
  // The related columns of this node, when a focus reaches it.
  related: Set<string> | undefined;
  // The focused column, in the focused column's node.
  focused: string | undefined;
}

// The rows a node lists: all its columns when expanded, otherwise only the related ones. `more` counts the columns a
// focus leaves out, listed or not.
export function nodeRows(columns: ColumnView[], { expanded, related, focused }: RowOptions): { rows: RowView[]; more: number } {
  if (related === undefined) {
    return { rows: expanded ? columns.map((column) => ({ column, role: undefined })) : [], more: 0 };
  }
  const role = (column: ColumnView): RowRole =>
    column.name === focused ? "focus" : related.has(column.name) ? "related" : "other";
  const all = columns.map((column) => ({ column, role: role(column) }));
  return { rows: all.filter((row) => expanded || row.role !== "other"), more: all.filter((row) => row.role === "other").length };
}
