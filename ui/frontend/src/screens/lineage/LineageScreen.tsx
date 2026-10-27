import { useEffect, useState } from "react";
import { Box, Group, Loader, ScrollArea, Stack, Text } from "@mantine/core";

import { ApiRequestError, useLineage } from "../../api/queries";
import { LoadError } from "../explorer/LoadError";
import { LayerFilter } from "./LayerFilter";
import { LineageCanvas } from "./LineageCanvas";
import { NodePanel } from "./NodePanel";
import { SelectorBar } from "./SelectorBar";
import { useLineageParams } from "./url";

const UNSUPPORTED_SELECTOR = "unsupported_selector";
// The panel beside the canvas; at 1280 px the canvas keeps about 700 px, at 1024 px about 480 px.
const PANEL_WIDTH = 380;
const PANEL_HEIGHT = "70vh";

export function LineageScreen() {
  const [params, update] = useLineageParams();
  const lineage = useLineage(params);
  const error = lineage.error;
  // The last good graph stays while a selector fails (or loads), so the controls and the count do not vanish.
  const [lastGood, setLastGood] = useState(lineage.data);
  if (lineage.data !== undefined && lineage.data !== lastGood) setLastGood(lineage.data);
  const graph = lineage.data ?? lastGood;
  // An unsupported selector shows under the selector box; the graph of the last good selector stays.
  const loadError = error !== null && !(error instanceof ApiRequestError && error.code === UNSUPPORTED_SELECTOR);

  // A node= that the (freshly loaded) graph does not hold, e.g. after a selector change, clears itself.
  const fresh = lineage.data !== undefined && !lineage.isPlaceholderData;
  const staleNode =
    fresh && params.node !== undefined && !lineage.data.nodes.some((node) => node.unique_id === params.node);
  useEffect(() => {
    if (staleNode) update({ node: undefined });
  }, [staleNode, update]);

  // The node's button keeps the keyboard where it was before the panel opened.
  const closePanel = () => {
    const node = params.node;
    update({ node: undefined });
    if (node !== undefined) document.querySelector<HTMLElement>(`[data-id="${CSS.escape(node)}"] button`)?.focus();
  };

  return (
    <Stack gap="md">
      <SelectorBar select={params.select} onSelect={(select) => update({ select })} error={error} />
      <LayerFilter
        available={graph?.layers_available ?? []}
        layers={params.layers}
        onLayers={(layers) => update({ layers })}
        failingOnly={params.failingOnly}
        onFailingOnly={(failingOnly) => update({ failingOnly })}
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
          <Group align="flex-start" wrap="nowrap" gap="md">
            <Box style={{ flex: 1, minWidth: 0 }}>
              <LineageCanvas
                nodes={graph.nodes}
                edges={graph.edges}
                selected={params.node}
                onSelect={(node) => update({ node })}
                failingOnly={params.failingOnly}
                layersFiltered={params.layers !== undefined}
                buildState={graph.build.state}
              />
            </Box>
            {params.node !== undefined && (
              <ScrollArea h={PANEL_HEIGHT} w={PANEL_WIDTH} style={{ flexShrink: 0 }} offsetScrollbars="y">
                <NodePanel uniqueId={params.node} onClose={closePanel} />
              </ScrollArea>
            )}
          </Group>
        </>
      )}
    </Stack>
  );
}
