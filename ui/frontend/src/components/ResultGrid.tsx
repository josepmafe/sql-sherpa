import { ActionIcon, Alert, Badge, Box, Button, Group, Select, Stack, Table, Text, TextInput } from "@mantine/core";
import { IconArrowDown, IconArrowUp, IconX } from "@tabler/icons-react";
import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  type SortingState,
  type Updater,
  useReactTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useMemo, useRef, useState } from "react";

import {
  applyView,
  type Cell,
  cellText,
  hasDuplicateColumns,
  isServerSide,
  type Operator,
  type QueryResponse,
  type ResultView,
  type Row,
  VALUELESS_OPERATORS,
} from "./resultView";

type GridProps = {
  response: QueryResponse;
  view: ResultView;
  onViewChange: (view: ResultView) => void;
};

const ROW_HEIGHT = 32;
const VIEWPORT_HEIGHT = 420;
const OVERSCAN = 10;
const COLUMN_WIDTH = 160;
const DISPLAY_MAX_CHARS = 200; // Long text is cut in the display only; the full value is the hover title.
const OPERATORS: { value: Operator; label: string }[] = [
  { value: "=", label: "=" },
  { value: "!=", label: "!=" },
  { value: "<", label: "<" },
  { value: "<=", label: "<=" },
  { value: ">", label: ">" },
  { value: ">=", label: ">=" },
  { value: "contains", label: "contains" },
  { value: "is_null", label: "is null" },
  { value: "is_not_null", label: "is not null" },
];

export function ResultGrid({ response, view, onViewChange }: GridProps) {
  const { columns, column_types: types } = response;
  const serverSide = isServerSide(response);
  // In server mode the rows already are the sorted and filtered ones.
  const rows = useMemo(
    () => (serverSide ? (response.rows as Row[]) : applyView(response.rows as Row[], types, view)),
    [serverSide, response.rows, types, view],
  );

  const defs = useMemo<ColumnDef<Row>[]>(
    () => columns.map((name, index) => ({ id: String(index), header: name, accessorFn: (row) => row[index] })),
    [columns],
  );
  const sorting: SortingState = view.sort ? [{ id: String(view.sort.index), desc: view.sort.direction === "desc" }] : [];
  // TanStack Table returns unmemoizable functions; nothing here depends on their identity.
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({
    data: rows,
    columns: defs,
    state: { sorting },
    manualSorting: true,
    sortDescFirst: false,
    getCoreRowModel: getCoreRowModel(),
    onSortingChange: (updater: Updater<SortingState>) => {
      const next = (typeof updater === "function" ? updater(sorting) : updater)[0];
      onViewChange({
        ...view,
        sort: next ? { index: Number(next.id), direction: next.desc ? "desc" : "asc" } : undefined,
      });
    },
  });

  const scrollRef = useRef<HTMLDivElement>(null);
  const tableRows = table.getRowModel().rows;
  const virtualizer = useVirtualizer({
    count: tableRows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: OVERSCAN,
    // The viewport has a fixed height, so it is not measured (jsdom cannot measure it either).
    observeElementRect: (_instance, report) => {
      report({ width: 0, height: VIEWPORT_HEIGHT });
      return () => undefined;
    },
  });
  const items = virtualizer.getVirtualItems();
  const before = items.length > 0 ? (items[0]?.start ?? 0) : 0;
  const after = items.length > 0 ? virtualizer.getTotalSize() - (items[items.length - 1]?.end ?? 0) : 0;
  const filtered = !serverSide && view.filters.length > 0;

  return (
    <Stack gap="xs">
      <StatusLine response={response} shown={tableRows.length} filtered={filtered} />
      {hasDuplicateColumns(columns) && response.warning && (
        <Alert color="yellow" title="Duplicate column names">
          {response.warning}
        </Alert>
      )}
      {serverSide && (
        <Alert color="blue">
          {response.truncated
            ? `Truncated at ${response.returned_rows.toLocaleString("en-US")} rows: sorting and filtering run on the server over all rows`
            : "Sorting and filtering run on the server over all rows"}
        </Alert>
      )}
      {columns.length === 0 ? (
        <Text size="sm" c="dimmed">
          The query returned no columns.
        </Text>
      ) : (
        <>
          <FilterBar columns={columns} view={view} onViewChange={onViewChange} />
          <Box ref={scrollRef} style={{ height: VIEWPORT_HEIGHT, overflow: "auto" }}>
            <Table stickyHeader withTableBorder withColumnBorders style={{ width: columns.length * COLUMN_WIDTH }}>
              <Table.Thead>
                {table.getHeaderGroups().map((group) => (
                  <Table.Tr key={group.id}>
                    {group.headers.map((header) => {
                      const direction = header.column.getIsSorted();
                      return (
                        <Table.Th
                          key={header.id}
                          aria-sort={direction ? (direction === "asc" ? "ascending" : "descending") : "none"}
                          onClick={header.column.getToggleSortingHandler()}
                          style={{ cursor: "pointer", width: COLUMN_WIDTH }}
                        >
                          <Group gap={4} wrap="nowrap">
                            {flexRender(header.column.columnDef.header, header.getContext())}
                            {direction === "asc" && <IconArrowUp size={14} />}
                            {direction === "desc" && <IconArrowDown size={14} />}
                          </Group>
                        </Table.Th>
                      );
                    })}
                  </Table.Tr>
                ))}
              </Table.Thead>
              <Table.Tbody>
                {before > 0 && <Spacer height={before} columns={columns.length} />}
                {items.map((item) => {
                  const row = tableRows[item.index];
                  if (!row) return null;
                  return (
                    <Table.Tr key={row.id} style={{ height: ROW_HEIGHT }}>
                      {row.getVisibleCells().map((cell) => (
                        <GridCell key={cell.id} value={cell.getValue()} type={types[Number(cell.column.id)]} />
                      ))}
                    </Table.Tr>
                  );
                })}
                {after > 0 && <Spacer height={after} columns={columns.length} />}
              </Table.Tbody>
            </Table>
          </Box>
          {tableRows.length === 0 && (
            <Text size="sm" c="dimmed">
              No rows.
            </Text>
          )}
        </>
      )}
    </Stack>
  );
}

function StatusLine({ response, shown, filtered }: { response: QueryResponse; shown: number; filtered: boolean }) {
  const total = response.returned_rows.toLocaleString("en-US");
  const count = filtered ? `${shown.toLocaleString("en-US")} of ${total} rows` : `${total} rows`;
  return (
    <Group gap="xs">
      <Text size="sm" c="dimmed">
        {count} in {response.elapsed_ms} ms
      </Text>
      <Badge variant="light" color="gray">
        read-only
      </Badge>
      {response.truncated && (
        <Badge variant="light" color="orange">
          truncated
        </Badge>
      )}
    </Group>
  );
}

function GridCell({ value, type }: { value: Cell; type: string | undefined }) {
  const text = cellText(value);
  if (text === null) {
    return (
      <Table.Td c="dimmed" fs="italic" ta={type === "number" ? "right" : "left"}>
        null
      </Table.Td>
    );
  }
  const shown = text.length > DISPLAY_MAX_CHARS ? `${text.slice(0, DISPLAY_MAX_CHARS)}…` : text;
  return (
    <Table.Td
      title={text}
      ta={type === "number" ? "right" : "left"}
      style={{ maxWidth: COLUMN_WIDTH, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
    >
      {shown}
    </Table.Td>
  );
}

function Spacer({ height, columns }: { height: number; columns: number }) {
  return (
    <Table.Tr aria-hidden style={{ height }}>
      <Table.Td colSpan={columns} p={0} style={{ border: 0 }} />
    </Table.Tr>
  );
}

function FilterBar({ columns, view, onViewChange }: Omit<GridProps, "response"> & { columns: string[] }) {
  const [column, setColumn] = useState<string | null>(null);
  const [operator, setOperator] = useState<Operator>("=");
  const [value, setValue] = useState("");
  const needsValue = !VALUELESS_OPERATORS.includes(operator);
  const add = () => {
    if (column === null) return;
    onViewChange({ ...view, filters: [...view.filters, { index: Number(column), operator, value }] });
    setValue("");
  };

  return (
    <Stack gap={4}>
      <Group gap="xs" align="flex-end" wrap="nowrap">
        <Select
          aria-label="Filter column"
          placeholder="Filter column"
          data={columns.map((name, index) => ({ value: String(index), label: name }))}
          value={column}
          onChange={setColumn}
          size="xs"
        />
        <Select
          aria-label="Filter operator"
          data={OPERATORS}
          value={operator}
          onChange={(next) => next && setOperator(next as Operator)}
          allowDeselect={false}
          size="xs"
          w={130}
        />
        {needsValue && (
          <TextInput
            aria-label="Filter value"
            placeholder="value"
            value={value}
            onChange={(event) => setValue(event.currentTarget.value)}
            onKeyDown={(event) => event.key === "Enter" && add()}
            size="xs"
          />
        )}
        <Button size="xs" variant="light" disabled={column === null} onClick={add}>
          Add filter
        </Button>
      </Group>
      {view.filters.length > 0 && (
        <Group gap="xs">
          {view.filters.map((filter, position) => (
            <Badge
              key={`${filter.index}-${filter.operator}-${filter.value}-${position}`}
              variant="outline"
              rightSection={
                <ActionIcon
                  size="xs"
                  variant="transparent"
                  aria-label={`Remove filter on ${columns[filter.index]}`}
                  onClick={() =>
                    onViewChange({ ...view, filters: view.filters.filter((_, other) => other !== position) })
                  }
                >
                  <IconX size={10} />
                </ActionIcon>
              }
            >
              {columns[filter.index]} {OPERATORS.find((o) => o.value === filter.operator)?.label}{" "}
              {VALUELESS_OPERATORS.includes(filter.operator) ? "" : filter.value}
            </Badge>
          ))}
        </Group>
      )}
    </Stack>
  );
}
