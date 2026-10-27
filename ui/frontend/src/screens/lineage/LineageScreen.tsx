import { useState } from "react";
import { Loader, Stack, Text } from "@mantine/core";

import { ApiRequestError, useLineage } from "../../api/queries";
import { LoadError } from "../explorer/LoadError";
import { LayerFilter } from "./LayerFilter";
import { SelectorBar } from "./SelectorBar";
import { useLineageParams } from "./url";

const UNSUPPORTED_SELECTOR = "unsupported_selector";

// The canvas arrives with the next task; for now the screen shows the controls and the node count.
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
      />
      {loadError && (
        <LoadError title="Could not load the lineage" error={error} onRetry={() => void lineage.refetch()} />
      )}
      {lineage.isPending && <Loader size="sm" />}
      {graph !== undefined && <Text>{graph.nodes.length} nodes</Text>}
    </Stack>
  );
}
