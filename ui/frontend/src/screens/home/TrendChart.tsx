import { BarChart } from "@mantine/charts";
import { Table, VisuallyHidden } from "@mantine/core";

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
  { name: PARTIAL, color: "blue.2" },
];

export function TrendChart({ weeks }: { weeks: readonly TrendWeek[] }) {
  return (
    <>
      {/* The bars say nothing the table next to them does not; a screen reader gets the table. */}
      <div data-testid="trend-chart" aria-hidden="true">
        <BarChart
          h={CHART_HEIGHT}
          data={weeks.map(chartRow)}
          dataKey="week"
          series={SERIES}
          type="stacked"
          withLegend
          valueFormatter={(value) => formatCurrency(String(value))}
          yAxisProps={{ tickFormatter: (value: number) => formatCurrency(String(value)), width: 80 }}
        />
      </div>
      <VisuallyHidden>
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

// One series per kind of bar, so each gets its colour and its legend entry; the others are absent for that week.
function chartRow(week: TrendWeek): Record<string, string | number> {
  const revenue = Number(week.net_revenue);
  const name = week.is_partial ? PARTIAL : week.is_black_friday_week ? BLACK_FRIDAY : REGULAR;
  return { week: week.week_start, [name]: revenue };
}

function marks(week: TrendWeek): string {
  return [week.is_black_friday_week && BLACK_FRIDAY, week.is_partial && "partial"].filter(Boolean).join(", ");
}
