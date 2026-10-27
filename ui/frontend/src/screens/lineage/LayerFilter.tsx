import { Chip, Group, Switch, Text } from "@mantine/core";

import type { Layer } from "./url";

type BuildState = "ok" | "no_build" | "unavailable";

const BUILD_NOTES: Record<Exclude<BuildState, "ok">, string> = {
  no_build: "No full build recorded: run make build-prod",
  unavailable: "Build status unavailable: the database is down",
};

interface LayerFilterProps {
  // The layers the graph has (UI spec §6.5); `layers` undefined means all of them are shown.
  available: Layer[];
  layers: Layer[] | undefined;
  onLayers: (layers: Layer[] | undefined) => void;
  failingOnly: boolean;
  onFailingOnly: (failingOnly: boolean) => void;
  buildState: BuildState;
}

export function LayerFilter({ available, layers, onLayers, failingOnly, onFailingOnly, buildState }: LayerFilterProps) {
  const shown = layers ?? available;
  return (
    <Group gap="md" align="center">
      <Chip.Group
        multiple
        value={shown}
        onChange={(value) => {
          const next = available.filter((layer) => value.includes(layer));
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
      {buildState !== "ok" && (
        <Text size="sm" c="dimmed">
          {BUILD_NOTES[buildState]}
        </Text>
      )}
    </Group>
  );
}
