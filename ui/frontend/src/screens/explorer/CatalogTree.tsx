import { ActionIcon, Alert, Box, Group, Menu, Stack, Text, TextInput, Tooltip } from "@mantine/core";
import { IconChevronDown, IconChevronRight, IconDots } from "@tabler/icons-react";
import { useId, useState } from "react";

import { useCatalog } from "../../api/queries";
import type { components } from "../../api/schema";
import { ColumnBadges } from "./ColumnBadges";
import { LINEAGE_UNAVAILABLE } from "./lineage";

type TableSummary = components["schemas"]["TableSummary"];
type ColumnSummary = components["schemas"]["ColumnSummary"];

type EditorActions = {
  onPreview: (qualifiedName: string) => void;
  onInsertSelect: (qualifiedName: string) => void;
};

const SUMMARY_MAX_CHARS = 200;

type TreeProps = EditorActions & {
  selected?: string;
  onSelect: (table: string) => void;
};

export function CatalogTree({ selected, onSelect, onPreview, onInsertSelect }: TreeProps) {
  const catalog = useCatalog();
  const [search, setSearch] = useState("");
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const needle = search.trim().toLowerCase();

  if (catalog.isError) return <Alert color="red">Could not load the catalog.</Alert>;
  if (!catalog.data) return <Text c="dimmed">Loading catalog…</Text>;

  const { database, schema_name: schema } = catalog.data;
  const domains = catalog.data.domains
    .map((domain) => ({ ...domain, tables: domain.tables.flatMap((t) => filterTable(t, needle)) }))
    .filter((domain) => domain.tables.length > 0);

  const toggle = (name: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (!next.delete(name)) next.add(name);
      return next;
    });

  return (
    <Stack gap="xs">
      <TextInput
        placeholder="Search tables and columns"
        aria-label="Search tables and columns"
        value={search}
        onChange={(event) => setSearch(event.currentTarget.value)}
      />
      <Text size="xs" c="dimmed">
        {database} › {schema}
      </Text>
      {domains.map((domain) => (
        <Box key={domain.name}>
          <Text fw={600} size="sm" tt="capitalize">
            {domain.name}
          </Text>
          {domain.tables.map((table) => (
            <TableRow
              key={table.name}
              table={table}
              schema={schema}
              selected={selected === table.name}
              // A search shows the matching columns, so tables open on their own while searching.
              open={needle !== "" || expanded.has(table.name)}
              onClick={() => {
                onSelect(table.name);
                toggle(table.name);
              }}
              onToggle={needle === "" ? () => toggle(table.name) : undefined}
              onPreview={onPreview}
              onInsertSelect={onInsertSelect}
            />
          ))}
        </Box>
      ))}
    </Stack>
  );
}

// A table stays when its name matches (with all its columns) or when any column matches (with those columns).
function filterTable(table: TableSummary, needle: string): TableSummary[] {
  if (needle === "" || table.name.toLowerCase().includes(needle)) return [table];
  const columns = table.columns.filter((c) => c.name.toLowerCase().includes(needle));
  return columns.length > 0 ? [{ ...table, columns }] : [];
}

type TableRowProps = EditorActions & {
  table: TableSummary;
  schema: string;
  selected: boolean;
  open: boolean;
  // Selects the table (the Inspector) and opens or closes its columns.
  onClick: () => void;
  // Only opens or closes the columns; none while a search keeps every table open.
  onToggle?: () => void;
};

const NAME_STYLE = {
  background: "none",
  border: 0,
  padding: 0,
  cursor: "pointer",
  textAlign: "left",
  color: "inherit",
  flex: 1,
  minWidth: 0,
} as const;

function TableRow({ table, schema, selected, open, onClick, onToggle, onPreview, onInsertSelect }: TableRowProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const lineageNote = useId();
  const qualified = `${schema}.${table.name}`;
  // UI spec §6.4: description, grain and PK. The full description is the Inspector's.
  const tip = (
    <>
      <div>{summary(table.description)}</div>
      {table.grain && (
        <div>
          <b>Grain:</b> {table.grain}
        </div>
      )}
      {table.primary_key.length > 0 && (
        <div>
          <b>PK:</b> {table.primary_key.join(", ")}
        </div>
      )}
    </>
  );

  return (
    <Box>
      <Group
        gap={2}
        wrap="nowrap"
        onContextMenu={(event) => {
          event.preventDefault();
          setMenuOpen(true);
        }}
      >
        {/* A mouse shortcut, out of the tab order: the name button does the same from the keyboard. */}
        <ActionIcon
          variant="subtle"
          color="gray"
          size="xs"
          tabIndex={-1}
          aria-label={`${open ? "Hide" : "Show"} the columns of ${table.name}`}
          disabled={!onToggle}
          onClick={onToggle}
        >
          {open ? <IconChevronDown size={14} /> : <IconChevronRight size={14} />}
        </ActionIcon>
        {/* Closed while the menu is open, which it would cover. */}
        <Tooltip label={tip} multiline w={320} openDelay={200} disabled={menuOpen}>
          <Text
            component="button"
            type="button"
            size="sm"
            fw={selected ? 700 : 500}
            aria-expanded={open}
            style={NAME_STYLE}
            onClick={onClick}
          >
            {table.name}
          </Text>
        </Tooltip>
        <Menu opened={menuOpen} onChange={setMenuOpen} position="bottom-end" withinPortal>
          <Menu.Target>
            <ActionIcon variant="subtle" size="sm" aria-label={`Actions for ${table.name}`}>
              <IconDots size={14} />
            </ActionIcon>
          </Menu.Target>
          <Menu.Dropdown>
            <Menu.Item onClick={() => onPreview(qualified)}>Preview 100 rows</Menu.Item>
            <Menu.Item onClick={() => onInsertSelect(qualified)}>Insert SELECT</Menu.Item>
            <Menu.Item onClick={() => void navigator.clipboard.writeText(qualified)}>Copy name</Menu.Item>
            <Menu.Item disabled aria-describedby={lineageNote}>
              Open in Lineage
            </Menu.Item>
            {/* Outside the disabled item, whose faded text would be hard to read. */}
            <Text id={lineageNote} size="xs" c="dimmed" px="sm" pb={4}>
              {LINEAGE_UNAVAILABLE}
            </Text>
          </Menu.Dropdown>
        </Menu>
      </Group>
      {open && table.columns.map((column) => <ColumnRow key={column.name} column={column} />)}
    </Box>
  );
}

function ColumnRow({ column }: { column: ColumnSummary }) {
  return (
    <Tooltip label={summary(column.description)} multiline w={320} openDelay={200}>
      <Group gap={6} ml="xl" wrap="nowrap">
        <Text size="xs">{column.name}</Text>
        <Text size="xs" c="dimmed">
          {column.data_type}
        </Text>
        <ColumnBadges isPrimaryKey={column.is_primary_key} isPii={column.is_pii} />
      </Group>
    </Tooltip>
  );
}

// The first sentence of a description, at most SUMMARY_MAX_CHARS long.
function summary(description: string): string {
  const text = description.replace(/\s+/g, " ").trim();
  const sentence = /^.*?[.!?](?=\s|$)/.exec(text)?.[0] ?? text;
  return sentence.length > SUMMARY_MAX_CHARS ? `${sentence.slice(0, SUMMARY_MAX_CHARS - 1)}…` : sentence;
}
