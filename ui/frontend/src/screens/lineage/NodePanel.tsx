import { ActionIcon, Alert, Badge, Box, Button, Code, Group, Loader, Stack, Tabs, Text, Title } from "@mantine/core";
import {
  IconAlertTriangle,
  IconCircleCheck,
  IconCircleDashed,
  IconCircleX,
  IconPlayerSkipForward,
  IconX,
} from "@tabler/icons-react";
import { type ComponentType, useEffect } from "react";
import { Link } from "react-router-dom";

import { useLineageNode } from "../../api/queries";
import type { components } from "../../api/schema";
import { ReadOnlySql } from "../../components/SqlEditor";
import { LoadError } from "../explorer/LoadError";
import { GREEN, GREY, ORANGE, RED, STATUS } from "./GraphNode";

type TestInfo = components["schemas"]["TestInfo"];
type TestOutcome = components["schemas"]["TestOutcome"];

const NODE_NOT_FOUND = "node_not_found";
const RAW = "raw";
const COMPILED = "compiled";

interface OutcomeView {
  Icon: ComponentType<{ size?: number; "aria-hidden"?: boolean }>;
  color: string;
}
// In display order, failures first. Like the graph's status, an outcome is an icon plus its name; only the icon is
// coloured.
const OUTCOMES: Record<TestOutcome, OutcomeView> = {
  failed: { Icon: IconCircleX, color: RED },
  warned: { Icon: IconAlertTriangle, color: ORANGE },
  skipped: { Icon: IconPlayerSkipForward, color: GREY },
  not_run: { Icon: IconCircleDashed, color: GREY },
  passed: { Icon: IconCircleCheck, color: GREEN },
};

interface NodePanelProps {
  uniqueId: string;
  onClose: () => void;
}

export function NodePanel({ uniqueId, onClose }: NodePanelProps) {
  const node = useLineageNode(uniqueId);

  // Esc closes the panel, unless it was meant for a text field or a menu.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (event.key === "Escape" && !target.closest("input, textarea, [role=listbox]")) onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <Stack gap="sm" component="aside" aria-label="Node details">
      <Group justify="flex-end">
        <ActionIcon variant="subtle" color="gray" size="sm" aria-label="Close the node panel" onClick={onClose}>
          <IconX size={14} />
        </ActionIcon>
      </Group>
      <Body node={node} />
    </Stack>
  );
}

function Body({ node }: { node: ReturnType<typeof useLineageNode> }) {
  if (node.isError) {
    // The graph stays: the node is simply not in the project (e.g. a stale link).
    if ("code" in node.error && node.error.code === NODE_NOT_FOUND) {
      return (
        <Alert color="yellow" role="alert" title="Node not found">
          {node.error.message}
        </Alert>
      );
    }
    return <LoadError title="Could not load the node" error={node.error} onRetry={() => void node.refetch()} />;
  }
  if (!node.data) return <Loader size="sm" />;

  const d = node.data;
  const status = STATUS[d.status];
  const failedTests = d.tests.filter((test) => test.outcome === "failed").length;
  return (
    <>
      <Title order={4}>{d.name}</Title>
      <Text size="sm">{d.description}</Text>
      <Group gap="xs">
        <Badge variant="light">{d.layer}</Badge>
        {d.materialization !== null && <Badge variant="outline">{d.materialization}</Badge>}
        {d.domain !== null && <Badge variant="light">{d.domain}</Badge>}
      </Group>
      <Text size="sm">
        <b>Contract:</b> {d.contract_enforced ? "enforced" : "not enforced"}
      </Text>
      <Text size="sm">
        <b>File:</b> <Code>{d.file_path}</Code>
      </Text>
      <Group gap="xs">
        <Button size="xs" variant="default" onClick={() => void navigator.clipboard.writeText(d.name)}>
          Copy dbt selector
        </Button>
        {d.is_mart && (
          <Button size="xs" variant="default" component={Link} to={`/explorer?table=${encodeURIComponent(d.name)}`}>
            Open in Explorer
          </Button>
        )}
      </Group>

      <Title order={5}>Last full build</Title>
      {status === undefined ? (
        <Text size="sm" c="dimmed">
          No build status.
        </Text>
      ) : (
        <Stack gap={2}>
          <Text size="sm" data-testid="node-status" style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <span style={{ display: "flex", color: status.color }}>
              <status.Icon size={14} aria-hidden />
            </span>
            {status.label}
            {failedTests > 0 && ` · ${failedTests} failing ${failedTests === 1 ? "test" : "tests"}`}
          </Text>
          {d.build?.execution_time_s != null && (
            <Text size="xs" c="dimmed">
              {d.build.execution_time_s.toFixed(1)} s
            </Text>
          )}
          {d.build?.message && <Text size="xs">{d.build.message}</Text>}
        </Stack>
      )}

      <Title order={5}>Columns</Title>
      {d.columns.length === 0 && (
        <Text size="xs" c="dimmed">
          No documented columns.
        </Text>
      )}
      {d.columns.map((column) => (
        <Stack key={column.name} gap={2}>
          <Group gap={6}>
            <Code>{column.name}</Code>
            {column.data_type !== null && (
              <Text size="xs" c="dimmed">
                {column.data_type}
              </Text>
            )}
          </Group>
          <Text size="xs">{column.description}</Text>
        </Stack>
      ))}

      <Title order={5}>Tests</Title>
      <TestList tests={d.tests} />

      {d.sql_note !== null && (
        <Text size="sm" role="note">
          {d.sql_note}
        </Text>
      )}
      {d.raw_sql !== null && (
        <Tabs defaultValue={RAW}>
          <Tabs.List>
            <Tabs.Tab value={RAW}>Raw SQL</Tabs.Tab>
            {d.compiled_sql !== null && <Tabs.Tab value={COMPILED}>Compiled SQL</Tabs.Tab>}
          </Tabs.List>
          <Tabs.Panel value={RAW} pt="xs">
            <ReadOnlySql value={d.raw_sql} label="Raw SQL" />
          </Tabs.Panel>
          {d.compiled_sql !== null && (
            <Tabs.Panel value={COMPILED} pt="xs">
              <ReadOnlySql value={d.compiled_sql} label="Compiled SQL" />
            </Tabs.Panel>
          )}
        </Tabs>
      )}
    </>
  );
}

function TestList({ tests }: { tests: TestInfo[] }) {
  if (tests.length === 0) {
    return (
      <Text size="xs" c="dimmed">
        No tests.
      </Text>
    );
  }
  return (
    <>
      {(Object.keys(OUTCOMES) as TestOutcome[]).map((outcome) => {
        const group = tests.filter((test) => test.outcome === outcome);
        if (group.length === 0) return null;
        const { Icon, color } = OUTCOMES[outcome];
        return (
          <Box key={outcome}>
            <Title order={6} style={{ display: "flex", alignItems: "center", gap: 4 }}>
              <span style={{ display: "flex", color }}>
                <Icon size={14} aria-hidden />
              </span>
              {outcome} ({group.length})
            </Title>
            {group.map((test) => (
              <Text key={test.name} size="xs">
                <span data-testid="test-name">{test.name}</span>
                <Text component="span" size="xs" c="dimmed">
                  {" "}
                  {[test.kind, test.column, test.severity === "warn" ? "warn" : null].filter(Boolean).join(" · ")}
                </Text>
              </Text>
            ))}
          </Box>
        );
      })}
    </>
  );
}
