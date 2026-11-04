import { BarChart } from "@mantine/charts";
import { ColorSwatch, Group, Stack, Table, Text, Title, VisuallyHidden } from "@mantine/core";

import type { components } from "../../api/schema";
import { formatCurrency } from "./format";

type TrendWeek = components["schemas"]["TrendWeek"];

const CHART_HEIGHT = 260;
const REGULAR = "Net revenue";
const BLACK_FRIDAY = "Black Friday week";
const PARTIAL = "Partial week";
const SERIES = [
  { name: REGULAR, color: "blue.6" },
  { name: BLACK_FRIDAY, color: "orange.6" },
  { name: PARTIAL, color: "blue.6" },
];
const SWATCH_SIZE = 12;
const Y_AXIS_WIDTH = 56;
// A partial week is the regular colour at low opacity with a dashed outline: both reach 3:1 against either theme.
const PARTIAL_OPACITY = 0.45;
const PARTIAL_DASH = "4 3";
const PARTIAL_STROKE = "var(--mantine-color-blue-6)";
const LEGEND_STYLE = { listStyle: "none", margin: 0, padding: 0, flexShrink: 0 } as const;

export function TrendChart({ weeks }: { weeks: readonly TrendWeek[] }) {
  const rows = weeks.map(chartRow);
  const series = SERIES.filter(({ name }) => rows.some((row) => name in row));
  // The regular bar needs no key: only the unusual kinds of week are named.
  const unusual = series.filter(({ name }) => name !== REGULAR);
  return (
    <>
      <Stack gap={4}>
        <Title order={3} size="h4">
          Weekly net revenue
        </Title>
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <Text size="sm" c="dimmed">
            Recent weeks read high until their refunds arrive (about 7 weeks).
          </Text>
          {unusual.length > 0 && (
            <Group component="ul" aria-label="Legend" gap="md" wrap="nowrap" style={LEGEND_STYLE}>
              {unusual.map(({ name, color }) => (
                <Group component="li" key={name} gap={6} wrap="nowrap">
                  <ColorSwatch color={`var(--mantine-color-${color.replace(".", "-")})`} size={SWATCH_SIZE} withShadow={false} aria-hidden />
                  <Text span size="sm">
                    {name}
                  </Text>
                </Group>
              ))}
            </Group>
          )}
        </Group>
      </Stack>
      {/* The bars say nothing the table next to them does not; a screen reader gets the table, and the chart is no
          keyboard stop (recharts' accessibility layer would make it one). */}
      <div data-testid="trend-chart" aria-hidden="true">
        <BarChart
          h={CHART_HEIGHT}
          data={rows}
          dataKey="week"
          series={series}
          accessibilityLayer={false}
          type="stacked"
          valueFormatter={(value) => formatCurrency(String(value))}
          barProps={partialLook}
          xAxisProps={{ tickFormatter: formatWeekLabel }}
          yAxisProps={{ tickFormatter: (value: number) => formatCurrency(String(value)), width: Y_AXIS_WIDTH }}
        />
      </div>
      <VisuallyHidden component="div">
        <Table aria-label="Net revenue per week">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Week starting</Table.Th>
              <Table.Th>Net revenue</Table.Th>
              <Table.Th>Note</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {weeks.map((week) => (
              <Table.Tr key={week.week_start}>
                <Table.Td>{week.week_start}</Table.Td>
                <Table.Td>{formatCurrency(week.net_revenue)}</Table.Td>
                <Table.Td>{marks(week)}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      </VisuallyHidden>
    </>
  );
}

/** "2026-05-11" as "11 May". */
export function formatWeekLabel(weekStart: string): string {
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", timeZone: "UTC" }).format(new Date(weekStart));
}

function partialLook({ name }: { name: string }): Record<string, unknown> {
  if (name !== PARTIAL) return {};
  return { fillOpacity: PARTIAL_OPACITY, stroke: PARTIAL_STROKE, strokeWidth: 1.5, strokeDasharray: PARTIAL_DASH };
}

// One series per kind of bar, so each gets its colour and its legend entry; the others are absent for that week.
function chartRow(week: TrendWeek): Record<string, string | number> {
  const revenue = Number(week.net_revenue);
  const name = week.is_partial ? PARTIAL : week.is_black_friday_week ? BLACK_FRIDAY : REGULAR;
  return { week: week.week_start, [name]: revenue };
}

function marks(week: TrendWeek): string {
  return [week.is_black_friday_week && BLACK_FRIDAY, week.is_partial && "partial"].filter(Boolean).join(", ");
}
