import { useEffect, useMemo, useState } from "react";
import { Loader, Stack, Text } from "@mantine/core";

import { ApiRequestError, useColumnFocus, useLineage } from "../../api/queries";
import { LoadError } from "../explorer/LoadError";
import { focusView } from "./columnFocus";
import { LayerFilter } from "./LayerFilter";
import { LineageCanvas } from "./LineageCanvas";
import { NodePanel } from "./NodePanel";
import { SelectorBar } from "./SelectorBar";
import { useLineageParams } from "./url";

const UNSUPPORTED_SELECTOR = "unsupported_selector";
const COLUMN_NOT_FOUND = "column_not_found";
const COLUMNS_UNAVAILABLE = "Column lineage is unavailable";

export function LineageScreen() {
  const [params, update] = useLineageParams();
  // The nodes the user opened. Columns are requested from the first one on (or a focused column), and kept after it.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [wantColumns, setWantColumns] = useState(false);
  if (!wantColumns && params.column !== undefined) setWantColumns(true);
  const columns = wantColumns || params.column !== undefined;
  const lineage = useLineage(params, { columns });
  const error = lineage.error;
  // The last good graph stays while a selector fails (or loads), so the controls and the count do not vanish.
  const [lastGood, setLastGood] = useState(lineage.data);
  if (lineage.data !== undefined && lineage.data !== lastGood) setLastGood(lineage.data);
  const graph = lineage.data ?? lastGood;
  // An unsupported selector shows under the selector box; the graph of the last good selector stays.
  const loadError = error !== null && !(error instanceof ApiRequestError && error.code === UNSUPPORTED_SELECTOR);

  const columnsUnavailable =
    graph?.column_lineage === "unavailable" ? (graph.column_lineage_reason ?? COLUMNS_UNAVAILABLE) : undefined;
  const focusQuery = useColumnFocus(params.node, columnsUnavailable === undefined ? params.column : undefined);
  const focusData = focusQuery.data;
  // "Show the rest" of the nodes a focus reaches belongs to that focus: it ends with it, and the user's own expansion
  // is not touched.
  const focusKey = params.node !== undefined && params.column !== undefined ? `${params.node}/${params.column}` : undefined;
  const [restOf, setRestOf] = useState<{ key: string | undefined; nodes: Set<string> }>({ key: undefined, nodes: new Set() });
  if (restOf.key !== focusKey) setRestOf({ key: focusKey, nodes: new Set() });
  const rest = restOf.nodes;
  const shown = useMemo(() => new Set(graph?.nodes.map((node) => node.unique_id)), [graph]);
  const focus = useMemo(
    () => (params.column === undefined || focusData === undefined ? undefined : focusView(focusData, shown)),
    [params.column, focusData, shown],
  );

  // A node= that the (freshly loaded) graph does not hold, e.g. after a selector change, clears itself.
  const fresh = lineage.data !== undefined && !lineage.isPlaceholderData;
  const staleColumn =
    focusQuery.error instanceof ApiRequestError && focusQuery.error.code === COLUMN_NOT_FOUND;
  useEffect(() => {
    if (staleColumn) update({ column: undefined });
  }, [staleColumn, update]);
  const staleNode =
    fresh && params.node !== undefined && !lineage.data.nodes.some((node) => node.unique_id === params.node);
  useEffect(() => {
    if (staleNode) update({ node: undefined });
  }, [staleNode, update]);

  // The node's button keeps the keyboard where it was before the selection or the focus ends (Esc, the close button,
  // the background).
  const focusNodeButton = (uniqueId: string | undefined) => {
    if (uniqueId !== undefined) document.querySelector<HTMLElement>(`[data-id="${CSS.escape(uniqueId)}"] button`)?.focus();
  };
  const clearSelection = () => {
    const node = params.node;
    update({ node: undefined, column: undefined });
    focusNodeButton(node);
  };
  const focusColumn = (column: string | undefined) => {
    update({ column });
    if (column === undefined) focusNodeButton(params.node);
  };
  const toggle = (uniqueId: string) => {
    setWantColumns(true);
    const toggled = (now: Set<string>) => {
      const next = new Set(now);
      if (!next.delete(uniqueId)) next.add(uniqueId);
      return next;
    };
    if (focus?.expanded.has(uniqueId)) setRestOf({ key: focusKey, nodes: toggled(rest) });
    else setExpanded(toggled);
  };

  return (
    <Stack gap="md">
      <SelectorBar select={params.select} onSelect={(select) => update({ select })} error={error} columns={columns} />
      <LayerFilter
        available={graph?.layers_available ?? []}
        layers={params.layers}
        onLayers={(layers) => update({ layers })}
        failingOnly={params.failingOnly}
        onFailingOnly={(failingOnly) => update({ failingOnly })}
        onExpandAll={() => {
          setWantColumns(true);
          setExpanded(new Set(shown));
        }}
        onCollapseAll={() => setExpanded(new Set())}
        columnsUnavailable={columnsUnavailable}
        buildState={graph?.build.state ?? "ok"}
        startedAt={graph?.build.started_at ?? null}
      />
      {loadError && (
        <LoadError title="Could not load the lineage" error={error} onRetry={() => void lineage.refetch()} />
      )}
      {lineage.isPending && <Loader size="sm" />}
      {/* Under a load error the last graph would be stale; an unsupported selector keeps it on purpose. */}
      {graph !== undefined && !loadError && (
        <>
          <Text size="sm">{graph.nodes.length} nodes</Text>
          <LineageCanvas
            nodes={graph.nodes}
            edges={graph.edges}
            selected={params.node}
            column={params.column}
            onSelect={(node) => (node === undefined ? clearSelection() : update({ node, column: undefined }))}
            focus={focus}
            expanded={expanded}
            rest={rest}
            onToggle={toggle}
            onFocusColumn={(node, column) => {
              setWantColumns(true);
              update({ node, column });
            }}
            columnsUnavailable={columnsUnavailable}
            failingOnly={params.failingOnly}
            layersFiltered={params.layers !== undefined}
            buildState={graph.build.state}
          />
          <NodePanel
            uniqueId={params.node}
            onClose={clearSelection}
            column={params.column}
            outside={focus?.outside ?? 0}
            onFocusColumn={focusColumn}
          />
        </>
      )}
    </Stack>
  );
}
