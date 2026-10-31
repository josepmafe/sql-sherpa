import { Button, Chip, Group, Switch, Text, VisuallyHidden } from "@mantine/core";
import { useId } from "react";

import type { Layer } from "./url";

type BuildState = "ok" | "no_build" | "unavailable";

const BUILD_NOTES: Record<Exclude<BuildState, "ok">, string> = {
  no_build: "No full build recorded: run make build-prod",
  unavailable: "Build status unavailable (could not read the build record; see the ui log)",
};

interface LayerFilterProps {
  // The layers the graph has (UI spec §6.5); `layers` undefined means all of them are shown.
  available: Layer[];
  layers: Layer[] | undefined;
  onLayers: (layers: Layer[] | undefined) => void;
  failingOnly: boolean;
  onFailingOnly: (failingOnly: boolean) => void;
  buildState: BuildState;
  // Act on the nodes shown. Disabled, with this reason as their description, while column lineage is unavailable.
  onExpandAll: () => void;
  onCollapseAll: () => void;
  columnsUnavailable: string | undefined;
  // When the full build whose statuses the graph shows started (ISO 8601).
  startedAt: string | null;
}

export function LayerFilter({
  available,
  layers,
  onLayers,
  failingOnly,
  onFailingOnly,
  onExpandAll,
  onCollapseAll,
  columnsUnavailable,
  buildState,
  startedAt,
}: LayerFilterProps) {
  const why = useId();
  const unavailable = columnsUnavailable !== undefined;
  const describedBy = unavailable ? why : undefined;
  const shown = layers ?? available;
  return (
    <Group gap="md" align="center">
      <Chip.Group
        multiple
        value={shown}
        onChange={(value) => {
          const next = available.filter((layer) => value.includes(layer));
          if (next.length === 0) return; // no layer shown is not a view
          onLayers(next.length === available.length ? undefined : next);
        }}
      >
        <Group gap="xs">
          {available.map((layer) => (
            <Chip key={layer} value={layer} size="xs">
              {layer}
            </Chip>
          ))}
        </Group>
      </Chip.Group>
      <Switch
        label="Failing only"
        checked={failingOnly}
        onChange={(event) => onFailingOnly(event.currentTarget.checked)}
      />
      <Group gap="xs">
        <Button size="xs" variant="default" onClick={onExpandAll} disabled={unavailable} aria-describedby={describedBy}>
          Expand all
        </Button>
        <Button size="xs" variant="default" onClick={onCollapseAll} disabled={unavailable} aria-describedby={describedBy}>
          Collapse all
        </Button>
        {columnsUnavailable !== undefined && <VisuallyHidden id={why}>{columnsUnavailable}</VisuallyHidden>}
      </Group>
      {note(buildState, startedAt) !== undefined && (
        <Text size="sm" c="dimmed">
          {note(buildState, startedAt)}
        </Text>
      )}
    </Group>
  );
}

function note(buildState: BuildState, startedAt: string | null): string | undefined {
  if (buildState !== "ok") return BUILD_NOTES[buildState];
  return startedAt === null ? undefined : `Status from the full build of ${formatBuildStart(startedAt)}`;
}

function formatBuildStart(startedAt: string): string {
  return `${new Date(startedAt).toISOString().slice(0, 16).replace("T", " ")} UTC`;
}
