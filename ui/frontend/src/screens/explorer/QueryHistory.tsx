import { Button, Group, Popover, ScrollArea, Stack, Text, UnstyledButton } from "@mantine/core";

import type { HistoryEntry } from "./worksheetStore";

type HistoryProps = {
  history: HistoryEntry[];
  // Loads a past query into the active worksheet.
  onPick: (sql: string) => void;
};

const SQL_PREVIEW_CHARS = 80;
const LIST_HEIGHT = 320;
const LIST_WIDTH = 460;

export function QueryHistory({ history, onPick }: HistoryProps) {
  return (
    <Popover position="bottom-end" withArrow shadow="md">
      <Popover.Target>
        <Button variant="subtle" size="xs">
          History ({history.length})
        </Button>
      </Popover.Target>
      <Popover.Dropdown w={LIST_WIDTH}>
        {history.length === 0 ? (
          <Text size="sm" c="dimmed">
            No queries run yet.
          </Text>
        ) : (
          <ScrollArea.Autosize mah={LIST_HEIGHT}>
            <Stack gap="xs">
              {[...history].reverse().map((entry, index) => (
                <UnstyledButton key={`${entry.ranAt}-${index}`} onClick={() => onPick(entry.sql)}>
                  <Group gap="xs" wrap="nowrap" justify="space-between">
                    <Text size="xs" ff="monospace" truncate>
                      {preview(entry.sql)}
                    </Text>
                    <Text size="xs" c={entry.status === "done" ? "dimmed" : "red"} style={{ flexShrink: 0 }}>
                      {describe(entry)}
                    </Text>
                  </Group>
                  <Text size="xs" c="dimmed">
                    {new Date(entry.ranAt).toLocaleString()}
                  </Text>
                </UnstyledButton>
              ))}
            </Stack>
          </ScrollArea.Autosize>
        )}
      </Popover.Dropdown>
    </Popover>
  );
}

function preview(sql: string): string {
  const oneLine = sql.replace(/\s+/g, " ").trim();
  return oneLine.length > SQL_PREVIEW_CHARS ? `${oneLine.slice(0, SQL_PREVIEW_CHARS)}…` : oneLine;
}

function describe(entry: HistoryEntry): string {
  if (entry.status === "done") return `${entry.returnedRows ?? 0} rows`;
  return entry.status;
}
