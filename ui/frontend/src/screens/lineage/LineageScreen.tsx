import { useCallback, useEffect, useMemo, useState } from "react";
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
const COLUMNS_UNAVAILABLE = "Column lineage is unavailable";

export function LineageScreen() {
  const [params, update] = useLineageParams();
  // The nodes the user opened. Columns are requested from the first one on (or a focused column), and kept after it.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [wantColumns, setWantColumns] = useState(false);
  if (!wantColumns && (expanded.size > 0 || params.column !== undefined)) setWantColumns(true);
  const lineage = useLineage(params, { columns: wantColumns });
  const error = lineage.error;
  // The last good graph stays while a selector fails (or loads), so the controls and the count do not vanish.
  const [lastGood, setLastGood] = useState(lineage.data);
  if (lineage.data !== undefined && lineage.data !== lastGood) setLastGood(lineage.data);
  const graph = lineage.data ?? lastGood;
  // An unsupported selector shows under the selector box; the graph of the last good selector stays.
  const loadError = error !== null && !(error instanceof ApiRequestError && error.code === UNSUPPORTED_SELECTOR);

  const columnsUnavailable =
    graph?.column_lineage === "unavailable" ? (graph.column_lineage_reason ?? COLUMNS_UNAVAILABLE) : undefined;
  const focusData = useColumnFocus(params.node, columnsUnavailable === undefined ? params.column : undefined).data;
  const shown = useMemo(() => new Set(graph?.nodes.map((node) => node.unique_id)), [graph]);
  const focus = useMemo(
    () => (params.column === undefined || focusData === undefined ? undefined : focusView(focusData, shown)),
    [params.column, focusData, shown],
  );

  // A node= that the (freshly loaded) graph does not hold, e.g. after a selector change, clears itself.
  const fresh = lineage.data !== undefined && !lineage.isPlaceholderData;
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
  const toggle = useCallback(
    (uniqueId: string) =>
      setExpanded((now) => {
        const next = new Set(now);
        if (!next.delete(uniqueId)) next.add(uniqueId);
        return next;
      }),
    [],
  );

  return (
    <Stack gap="md">
      <SelectorBar select={params.select} onSelect={(select) => update({ select })} error={error} />
      <LayerFilter
        available={graph?.layers_available ?? []}
        layers={params.layers}
        onLayers={(layers) => update({ layers })}
        failingOnly={params.failingOnly}
        onFailingOnly={(failingOnly) => update({ failingOnly })}
        onExpandAll={() => setExpanded(new Set(shown))}
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
            onToggle={toggle}
            onFocusColumn={(node, column) => update({ node, column })}
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
