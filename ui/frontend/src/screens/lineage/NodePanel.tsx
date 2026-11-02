import {
  ActionIcon,
  Anchor,
  Alert,
  Badge,
  Box,
  Button,
  Code,
  Flex,
  Group,
  Loader,
  Modal,
  ScrollArea,
  Stack,
  Table,
  Tabs,
  Text,
  Title,
  Tooltip,
  UnstyledButton,
  VisuallyHidden,
} from "@mantine/core";
import {
  IconAlertTriangle,
  IconCircleCheck,
  IconCircleDashed,
  IconCircleX,
  IconEye,
  IconPlayerSkipForward,
  IconX,
} from "@tabler/icons-react";
import { useClipboard, useDisclosure } from "@mantine/hooks";
import { type ComponentType, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { useLineageNode, useTestDetails } from "../../api/queries";
import type { components } from "../../api/schema";
import { CollapseToggle, usePaneCollapse } from "../../components/panes";
import { ReadOnlySql } from "../../components/SqlEditor";
import { LoadError } from "../explorer/LoadError";
import { KindLabel, ResolutionLabel } from "./ColumnLabels";
import { GREEN, GREY, ORANGE, RED, STATUS } from "./GraphNode";
import { pathSelector } from "./selectors";

type ColumnDetail = components["schemas"]["ColumnDetail"];
type NodeDetails = components["schemas"]["NodeDetails"];
type TestInfo = components["schemas"]["TestInfo"];
type TestDetails = components["schemas"]["TestDetails"];
type TestOutcome = components["schemas"]["TestOutcome"];

const NODE_NOT_FOUND = "node_not_found";
export const NODE_PANEL = "panel";
const PANEL_TITLE = "Node details";
// The folded pane is its title bar (UI spec §6.5).
export const PANEL_TITLE_BAR = 40;
const FOLD_LABEL = "Collapse the node panel";
const OPEN_LABEL = "Open the node panel";
const EMPTY_STATE = "Select a node to see its SQL, columns and tests";
const OVERVIEW = "overview";
const COLUMNS = "columns";
const TESTS = "tests";
const SQL = "sql";
const NO_COLUMNS = "This node has no columns.";
const NO_TESTS = "No test is defined on this node.";
const NO_SQL = "Sources, seeds and exposures have no SQL.";
// gray-7 on white and dark-1 on the dark surface both clear 4.5:1 (WCAG AA); a faded opacity would not.
const DISABLED_TAB_COLOR = "light-dark(var(--mantine-color-gray-7), var(--mantine-color-dark-1))";
// A test's dialog: about 80vw up to 900 px, its body scrolling on its own (UI spec §6.5).
const TEST_DIALOG_WIDTH = "min(80vw, 900px)";
const TEST_DIALOG_BODY_HEIGHT = "70vh";
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
  uniqueId: string | undefined;
  onClose: () => void;
  // The focused column of the node, and how many columns related to it are in nodes the graph does not show.
  column: string | undefined;
  outside: number;
  onFocusColumn: (column: string | undefined) => void;
}

// Always rendered under the graph, as the lower pane of the Lineage screen: selecting a node never resizes the canvas.
// Folded, it is its title bar; selecting a node opens it.
export function NodePanel({ uniqueId, onClose, column, outside, onFocusColumn }: NodePanelProps) {
  const node = useLineageNode(uniqueId);
  const { collapsed, toggle, setOpen, ready } = usePaneCollapse(NODE_PANEL);
  // Selecting a node opens a folded panel, also one folded by the stored layout (once the pane is there to open). Only
  // the selection and the pane's arrival are dependencies: folding it by hand must not reopen it.
  useEffect(() => {
    if (uniqueId !== undefined && ready && collapsed) setOpen(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uniqueId, ready]);
  // The tab stays while another node is selected, so the same tab can be read across nodes. A focused column opens
  // the Columns tab, where it is marked.
  const [tab, setTab] = useState<string | null>(column === undefined ? OVERVIEW : COLUMNS);
  const [seenColumn, setSeenColumn] = useState(column);
  if (column !== seenColumn) {
    setSeenColumn(column);
    if (column !== undefined) setTab(COLUMNS);
  }

  // Esc clears the column focus, then the selection, unless it was meant for a text field, a menu or a dialog.
  useEffect(() => {
    if (uniqueId === undefined) return;
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (event.key !== "Escape" || event.defaultPrevented || target.closest("input, textarea, [role=listbox], [role=dialog]")) return;
      if (column === undefined) onClose();
      else onFocusColumn(undefined);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [uniqueId, column, onClose, onFocusColumn]);

  return (
    <Flex component="aside" aria-label={PANEL_TITLE} direction="column" h="100%">
      <Group justify="space-between" wrap="nowrap" gap="xs" px="xs" h={PANEL_TITLE_BAR} style={{ flex: "none" }}>
        <Group gap="xs" wrap="nowrap">
          <CollapseToggle collapsed={collapsed} onToggle={toggle} foldLabel={FOLD_LABEL} openLabel={OPEN_LABEL} side="bottom" />
          <Text size="sm" fw={600}>
            {PANEL_TITLE}
          </Text>
        </Group>
        {!collapsed && uniqueId !== undefined && (
          <ActionIcon variant="subtle" color="gray" size="sm" aria-label="Close the node panel" onClick={onClose}>
            <IconX size={14} />
          </ActionIcon>
        )}
      </Group>
      {!collapsed && (
        <ScrollArea style={{ flex: 1, minHeight: 0 }} offsetScrollbars="y">
          <Stack gap="sm" p="xs">
            {uniqueId === undefined ? (
              <Text size="sm" c="dimmed">
                {EMPTY_STATE}
              </Text>
            ) : (
              <Body node={node} tab={tab} onTab={setTab} column={column} outside={outside} onFocusColumn={onFocusColumn} />
            )}
          </Stack>
        </ScrollArea>
      )}
    </Flex>
  );
}

interface BodyProps {
  node: ReturnType<typeof useLineageNode>;
  tab: string | null;
  onTab: (tab: string | null) => void;
  column: string | undefined;
  outside: number;
  onFocusColumn: (column: string | undefined) => void;
}

function Body({ node, tab, onTab, column, outside, onFocusColumn }: BodyProps) {
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
  // A tab with nothing to show says why instead of opening empty (UI spec §6.5). The chosen tab may be one this node
  // does not have: it then reads as Overview.
  const reasons: Record<string, string | undefined> = {
    [COLUMNS]: d.columns.length === 0 ? NO_COLUMNS : undefined,
    [TESTS]: d.tests.length === 0 ? NO_TESTS : undefined,
    [SQL]: d.raw_sql === null && d.sql_note === null ? NO_SQL : undefined,
  };
  const effective = tab !== null && reasons[tab] === undefined ? tab : OVERVIEW;
  const select = (next: string | null) => {
    if (next === null || reasons[next] === undefined) onTab(next);
  };
  return (
    <>
      <Title order={4}>{d.name}</Title>
      {column !== undefined && outside > 0 && <OutsideNote d={d} column={column} outside={outside} />}
      <Tabs value={effective} onChange={select}>
        <Tabs.List>
          <Tabs.Tab value={OVERVIEW}>Overview</Tabs.Tab>
          <PanelTab
            value={COLUMNS}
            label={d.columns.length === 0 ? "Columns" : `Columns (${d.columns.length})`}
            reason={reasons[COLUMNS]}
          />
          <PanelTab
            value={TESTS}
            label={d.tests.length === 0 ? "Tests" : `Tests (${d.tests.length})`}
            reason={reasons[TESTS]}
          />
          <PanelTab value={SQL} label="SQL" reason={reasons[SQL]} />
        </Tabs.List>
        <Tabs.Panel value={OVERVIEW} pt="xs">
          <Overview d={d} />
        </Tabs.Panel>
        <Tabs.Panel value={COLUMNS} pt="xs">
          {d.column_lineage_note !== null && (
            <Text size="sm" role="note" mb="xs">
              {d.column_lineage_note}
            </Text>
          )}
          <ColumnsTable columns={d.columns} focused={column} onFocusColumn={onFocusColumn} />
        </Tabs.Panel>
        <Tabs.Panel value={TESTS} pt="xs">
          <TestList key={d.unique_id} tests={d.tests} />
        </Tabs.Panel>
        <Tabs.Panel value={SQL} pt="xs">
          <SqlTabs d={d} />
        </Tabs.Panel>
      </Tabs>
    </>
  );
}

interface PanelTabProps {
  value: string;
  label: string;
  reason: string | undefined;
}

// Disabled with aria-disabled, not the attribute, so the tab stays in the focus order and its reason can be reached.
function PanelTab({ value, label, reason }: PanelTabProps) {
  const why = `tab-why-${value}`;
  if (reason === undefined) return <Tabs.Tab value={value}>{label}</Tabs.Tab>;
  return (
    <>
      <Tooltip label={reason} events={{ hover: true, focus: true, touch: false }} openDelay={200}>
        <Tabs.Tab
          value={value}
          aria-disabled="true"
          aria-describedby={why}
          style={{ color: DISABLED_TAB_COLOR, cursor: "not-allowed" }}
        >
          {label}
        </Tabs.Tab>
      </Tooltip>
      <VisuallyHidden id={why}>{reason}</VisuallyHidden>
    </>
  );
}

function Overview({ d }: { d: NodeDetails }) {
  const clipboard = useClipboard();
  const status = STATUS[d.status];
  const failedTests = d.tests.filter((test) => test.outcome === "failed").length;
  return (
    <Stack gap="sm">
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
        <Button size="xs" variant="default" onClick={() => clipboard.copy(d.selector)}>
          {clipboard.copied ? "Copied" : "Copy dbt selector"}
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
    </Stack>
  );
}

// A column edge never leaves the table edges, so +<node>+ always holds the related columns left out of the view.
function OutsideNote({ d, column, outside }: { d: NodeDetails; column: string; outside: number }) {
  const select = pathSelector(d.selector, "all", "all");
  const to = `/lineage?select=${encodeURIComponent(select)}&node=${encodeURIComponent(d.unique_id)}&column=${encodeURIComponent(column)}`;
  return (
    <Text size="sm" role="note">
      {outside === 1 ? "1 related column is" : `${outside} related columns are`} outside this view.{" "}
      {outside === 1 ? "Show it" : "Show them"} with{" "}
      <Anchor component={Link} to={to}>
        {select}
      </Anchor>
    </Text>
  );
}

interface ColumnsTableProps {
  columns: ColumnDetail[];
  focused: string | undefined;
  onFocusColumn: (column: string) => void;
}

function ColumnsTable({ columns, focused, onFocusColumn }: ColumnsTableProps) {
  return (
    <Table aria-label="Columns" withRowBorders verticalSpacing={4}>
      <Table.Thead>
        <Table.Tr>
          <Table.Th>Name</Table.Th>
          <Table.Th>Type</Table.Th>
          <Table.Th>Kind</Table.Th>
          <Table.Th>Resolution</Table.Th>
          <Table.Th>Notes</Table.Th>
        </Table.Tr>
      </Table.Thead>
      <Table.Tbody>
        {columns.map((column) => (
          <Table.Tr
            key={column.name}
            aria-current={column.name === focused ? "true" : undefined}
            bg={column.name === focused ? "var(--mantine-primary-color-light)" : undefined}
          >
            <Table.Td>
              <UnstyledButton onClick={() => onFocusColumn(column.name)} aria-label={column.name}>
                <Code>{column.name}</Code>
              </UnstyledButton>
            </Table.Td>
            <Table.Td>{column.data_type}</Table.Td>
            <Table.Td>{column.kind === null ? null : <KindLabel kind={column.kind} />}</Table.Td>
            <Table.Td>{column.resolution === null ? null : <ResolutionLabel resolution={column.resolution} />}</Table.Td>
            <Table.Td>{columnNotes(column)}</Table.Td>
          </Table.Tr>
        ))}
      </Table.Tbody>
    </Table>
  );
}

// A row count names the relation it counts: its unique_id ends with the node's name.
function columnNotes({ counts, notes }: ColumnDetail): string {
  return [counts === null ? null : `counts ${counts.split(".").at(-1)}`, ...notes].filter((note) => note !== null).join("; ");
}

function SqlTabs({ d }: { d: Pick<NodeDetails, "raw_sql" | "compiled_sql" | "sql_note"> }) {
  return (
    <Stack gap="xs">
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
    </Stack>
  );
}

function TestList({ tests }: { tests: TestInfo[] }) {
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
              <TestRow key={test.unique_id} test={test} />
            ))}
          </Box>
        );
      })}
    </>
  );
}

// An eye button opens the test in a dialog, which has the room to read where it is written and what it checks; the
// request waits for the first opening.
function TestRow({ test }: { test: TestInfo }) {
  const [opened, { open, close }] = useDisclosure(false);
  return (
    <Group gap={4} wrap="nowrap" align="baseline">
      <ActionIcon
        variant="subtle"
        color="gray"
        size="xs"
        aria-label={`Show ${test.name}`}
        aria-haspopup="dialog"
        onClick={open}
      >
        <IconEye size={14} />
      </ActionIcon>
      <Text size="xs">
        <span data-testid="test-name">{test.name}</span>
        <Text component="span" size="xs" c="dimmed">
          {" "}
          {[test.kind, test.column, test.severity === "warn" ? "warn" : null].filter(Boolean).join(" · ")}
        </Text>
      </Text>
      <Modal
        opened={opened}
        onClose={close}
        title={test.name}
        size={TEST_DIALOG_WIDTH}
        closeButtonProps={{ "aria-label": "Close" }}
        styles={{ body: { maxHeight: TEST_DIALOG_BODY_HEIGHT, overflowY: "auto" } }}
      >
        <TestDefinition uniqueId={test.unique_id} />
      </Modal>
    </Group>
  );
}

function TestDefinition({ uniqueId }: { uniqueId: string }) {
  const test = useTestDetails(uniqueId);
  if (test.isError) {
    return <LoadError title="Could not load the test" error={test.error} onRetry={() => void test.refetch()} />;
  }
  if (!test.data) return <Loader size="sm" />;

  return <TestBody t={test.data} />;
}

function TestBody({ t }: { t: TestDetails }) {
  const clipboard = useClipboard();
  return (
    <Stack gap="xs" my="xs">
      <Group gap="xs">
        <Badge variant="light">{t.kind}</Badge>
        <Text size="xs">
          <b>File:</b> <Code>{t.file_path}</Code>
        </Text>
      </Group>
      <Group gap="xs">
        <Button size="xs" variant="default" onClick={() => clipboard.copy(t.selector)}>
          {clipboard.copied ? "Copied" : "Copy dbt selector"}
        </Button>
      </Group>
      {t.raw_sql === null && t.compiled_sql !== null ? (
        <ReadOnlySql value={t.compiled_sql} label="Compiled SQL" />
      ) : (
        <SqlTabs d={t} />
      )}
      {t.definition !== null && (
        <Code block aria-label="Definition">
          {t.definition}
        </Code>
      )}
    </Stack>
  );
}
