import { ActionIcon, Group, Paper, SimpleGrid, Text, Tooltip } from "@mantine/core";
import { IconInfoCircle } from "@tabler/icons-react";

import type { components } from "../../api/schema";
import { describeChange, formatChange, formatValue } from "./format";

type Tile = components["schemas"]["Tile"];

const ICON_SIZE = 16;
// Mantine reads only its theme's breakpoint names here: md is 992 px, lg 1200 px. Two columns below md, three from md,
// five from lg.
const COLUMNS = { base: 2, md: 3, lg: 5 };

export function Tiles({ tiles }: { tiles: readonly Tile[] }) {
  return (
    <SimpleGrid cols={COLUMNS}>
      {tiles.map((tile) => (
        <TileCard key={tile.id} tile={tile} />
      ))}
    </SimpleGrid>
  );
}

function TileCard({ tile }: { tile: Tile }) {
  return (
    <Paper component="section" aria-label={tile.title} withBorder p="md">
      <Group justify="space-between" wrap="nowrap" gap="xs">
        <Text size="sm" c="dimmed">
          {tile.title}
        </Text>
        {tile.note !== null && (
          <Tooltip label={tile.note} events={{ hover: true, focus: true, touch: true }} multiline maw={280}>
            <ActionIcon variant="subtle" color="gray" size="sm" aria-label={`About ${tile.title}`}>
              <IconInfoCircle size={ICON_SIZE} />
            </ActionIcon>
          </Tooltip>
        )}
      </Group>
      <Text fz={28} fw={600}>
        {formatValue(tile)}
      </Text>
      <Text size="sm" fw={500} c={changeColour(tile)} aria-hidden="true">
        {formatChange(tile)}
      </Text>
      <Text size="xs" c="dimmed">
        {describeChange(tile)}
      </Text>
    </Paper>
  );
}

// Teal and red carry the verdict, the arrow and the words beside them say it too; dimmed when there is none. Shade 9 on
// light and 4 on dark pass AA on each background (the pattern of GraphNode.tsx).
const GOOD = "light-dark(var(--mantine-color-teal-9), var(--mantine-color-teal-4))";
const BAD = "light-dark(var(--mantine-color-red-9), var(--mantine-color-red-4))";

function changeColour({ good }: Tile): string {
  if (good === null) return "dimmed";
  return good ? GOOD : BAD;
}
