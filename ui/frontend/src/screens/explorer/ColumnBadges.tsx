import { Badge, Group } from "@mantine/core";

export function ColumnBadges({ isPrimaryKey, isPii }: { isPrimaryKey: boolean; isPii: boolean }) {
  return (
    <Group gap={4} component="span" wrap="nowrap">
      {isPrimaryKey && (
        <Badge size="xs" color="blue" variant="light">
          PK
        </Badge>
      )}
      {isPii && (
        <Badge size="xs" color="red" variant="light">
          PII
        </Badge>
      )}
    </Group>
  );
}
