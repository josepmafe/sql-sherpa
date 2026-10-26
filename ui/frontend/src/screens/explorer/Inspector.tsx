import { Alert, Badge, Button, Code, Group, Stack, Text, Title } from "@mantine/core";
import { useId } from "react";

import { useTableDetails } from "../../api/queries";
import { ColumnBadges } from "./ColumnBadges";
import { LINEAGE_UNAVAILABLE } from "./lineage";

export function Inspector({ table }: { table: string | undefined }) {
  const details = useTableDetails(table);
  const lineageNote = useId();

  if (table === undefined) return <Text c="dimmed">Select a table to inspect it.</Text>;
  if (details.isError) return <Alert color="red">Could not load {table}.</Alert>;
  if (!details.data) return <Text c="dimmed">Loading {table}…</Text>;

  const t = details.data;
  return (
    <Stack gap="sm">
      <Title order={4}>{t.name}</Title>
      <Badge variant="light" w="fit-content">
        {t.domain}
      </Badge>
      <Text size="sm">{t.description}</Text>
      <Text size="sm">
        <b>Grain:</b> {t.grain}
      </Text>
      <Text size="sm">
        <b>Primary key:</b> {t.primary_key.join(", ")}
      </Text>
      {t.synonyms.length > 0 && (
        <Text size="sm">
          <b>Synonyms:</b> {t.synonyms.join(", ")}
        </Text>
      )}
      <Group gap="xs">
        <Button disabled variant="default" size="xs" aria-describedby={lineageNote}>
          Open in Lineage
        </Button>
        <Text id={lineageNote} size="xs" c="dimmed">
          {LINEAGE_UNAVAILABLE}
        </Text>
      </Group>

      <Title order={5}>Columns</Title>
      {t.columns.map((column) => (
        <Stack key={column.name} gap={2}>
          <Group gap={6}>
            <Code>{column.name}</Code>
            <Text size="xs" c="dimmed">
              {column.data_type}
            </Text>
            <ColumnBadges isPrimaryKey={column.is_primary_key} isPii={column.is_pii} />
          </Group>
          <Text size="xs">{column.description}</Text>
          {column.accepted_values && <Text size="xs">Accepted values: {column.accepted_values.join(", ")}</Text>}
          {column.synonyms.length > 0 && <Text size="xs">Synonyms: {column.synonyms.join(", ")}</Text>}
          {column.metric_refs.map((metric) => (
            <Text key={metric.name} size="xs">
              Metric <b>{metric.name}</b>: {metric.definition}
            </Text>
          ))}
        </Stack>
      ))}

      <Title order={5}>Tests</Title>
      {t.tests.length === 0 ? (
        <Text size="xs" c="dimmed">
          No tests.
        </Text>
      ) : (
        t.tests.map((test) => (
          <Text key={test} size="xs">
            {test}
          </Text>
        ))
      )}
    </Stack>
  );
}
