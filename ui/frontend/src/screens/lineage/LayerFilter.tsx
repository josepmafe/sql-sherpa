import { Chip, Group, Switch, Text } from "@mantine/core";

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
  // When the full build whose statuses the graph shows started (ISO 8601).
  startedAt: string | null;
}

export function LayerFilter({ available, layers, onLayers, failingOnly, onFailingOnly, buildState, startedAt }: LayerFilterProps) {
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
