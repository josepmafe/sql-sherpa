import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

import type { components } from "../../api/schema";

export type Layer = components["schemas"]["Layer"];

// `node` is the selected node's unique_id and `column` the focused column of that node (UI spec §6.5). `layers`
// undefined shows every layer.
export interface LineageParams {
  select: string;
  layers: Layer[] | undefined;
  failingOnly: boolean;
  node: string | undefined;
  column: string | undefined;
}

const LAYERS: readonly Layer[] = ["source", "seed", "staging", "snapshot", "intermediate", "marts", "exposure", "other"];
const FAILING_ON = "1";

// Anything the URL holds that is not a known value falls back to the default.
export function parseLineageSearch(search: URLSearchParams): LineageParams {
  const layers = (search.get("layers") ?? "").split(",").filter(isLayer);
  const node = search.get("node") || undefined;
  return {
    select: search.get("select") ?? "",
    layers: layers.length > 0 ? layers : undefined,
    failingOnly: search.get("failing") === FAILING_ON,
    node,
    column: node === undefined ? undefined : search.get("column") || undefined,
  };
}

export function toSearchParams(params: LineageParams): URLSearchParams {
  const search = new URLSearchParams();
  if (params.select !== "") search.set("select", params.select);
  if (params.layers !== undefined && params.layers.length > 0) search.set("layers", params.layers.join(","));
  if (params.failingOnly) search.set("failing", FAILING_ON);
  if (params.node !== undefined) search.set("node", params.node);
  if (params.node !== undefined && params.column !== undefined) search.set("column", params.column);
  return search;
}

// The view lives in the URL, so reload, back/forward and a shared link restore it. Picking a node or a column replaces
// the history entry: back and forward move between views, not between clicks.
export function useLineageParams(): [LineageParams, (patch: Partial<LineageParams>) => void] {
  const [search, setSearch] = useSearchParams();
  const params = parseLineageSearch(search);
  const update = useCallback(
    (patch: Partial<LineageParams>) => {
      const next = toSearchParams({ ...parseLineageSearch(search), ...patch });
      if (next.toString() === search.toString()) return; // Enter on the applied selector is not a new view.
      const onlySelection = Object.keys(patch).every((key) => key === "node" || key === "column");
      setSearch(next, { replace: onlySelection });
    },
    [search, setSearch],
  );
  return [params, update];
}

function isLayer(value: string): value is Layer {
  return (LAYERS as readonly string[]).includes(value);
}
