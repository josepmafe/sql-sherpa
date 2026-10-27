import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

import type { components } from "../../api/schema";

export type Layer = components["schemas"]["Layer"];

// `node` is the selected node's unique_id (UI spec §6.5). `layers` undefined shows every layer.
export interface LineageParams {
  select: string;
  layers: Layer[] | undefined;
  failingOnly: boolean;
  node: string | undefined;
}

const LAYERS: readonly Layer[] = ["source", "seed", "staging", "snapshot", "intermediate", "marts", "exposure", "other"];
const FAILING_ON = "1";

// Anything the URL holds that is not a known value falls back to the default.
export function parseLineageSearch(search: URLSearchParams): LineageParams {
  const layers = (search.get("layers") ?? "").split(",").filter(isLayer);
  return {
    select: search.get("select") ?? "",
    layers: layers.length > 0 ? layers : undefined,
    failingOnly: search.get("failing") === FAILING_ON,
    node: search.get("node") || undefined,
  };
}

export function toSearchParams(params: LineageParams): URLSearchParams {
  const search = new URLSearchParams();
  if (params.select !== "") search.set("select", params.select);
  if (params.layers !== undefined && params.layers.length > 0) search.set("layers", params.layers.join(","));
  if (params.failingOnly) search.set("failing", FAILING_ON);
  if (params.node !== undefined) search.set("node", params.node);
  return search;
}

// The view lives in the URL, so reload, back/forward and a shared link restore it. Picking a node replaces the
// history entry: back and forward move between views, not between clicks.
export function useLineageParams(): [LineageParams, (patch: Partial<LineageParams>) => void] {
  const [search, setSearch] = useSearchParams();
  const params = parseLineageSearch(search);
  const update = useCallback(
    (patch: Partial<LineageParams>) => {
      const next = { ...parseLineageSearch(search), ...patch };
      const onlyNode = Object.keys(patch).every((key) => key === "node");
      setSearch(toSearchParams(next), { replace: onlyNode });
    },
    [search, setSearch],
  );
  return [params, update];
}

function isLayer(value: string): value is Layer {
  return (LAYERS as readonly string[]).includes(value);
}
