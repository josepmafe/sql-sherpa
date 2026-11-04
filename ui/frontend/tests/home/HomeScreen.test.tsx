import { act, fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HomeScreen } from "../../src/screens/home/HomeScreen";
import { buildSummary, home, tile, trendWeek } from "../fixtures/api";
import { renderWithProviders } from "../fixtures/render";

const useHome = vi.fn();
vi.mock("../../src/api/queries", () => ({ useHome: () => useHome() }));
// jsdom has no layout, so recharts draws nothing; the stand-in shows the series it was given.
vi.mock("@mantine/charts", () => ({
  BarChart: ({
    series,
    data,
    xAxisProps,
    barProps,
  }: {
    series: { name: string }[];
    data: { week: string }[];
    xAxisProps: { tickFormatter: (value: string) => string };
    barProps: (series: { name: string }) => Record<string, unknown>;
  }) => (
    <div>
      <ul data-testid="chart-series">
        {series.map(({ name }) => (
          <li key={name}>{name}</li>
        ))}
      </ul>
      <p data-testid="chart-x-labels">{data.map(({ week }) => xAxisProps.tickFormatter(week)).join(",")}</p>
      <ul data-testid="chart-bar-props">
        {series.map(({ name }) => (
          <li key={name}>{`${name}=${JSON.stringify(barProps({ name }))}`}</li>
        ))}
      </ul>
    </div>
  ),
}));

const TILES = [
  tile({ id: "net_revenue", title: "Net revenue", note: "Orders not cancelled, minus refunds." }),
  tile({ id: "orders", title: "Orders", format: "count", value: "1234", change: "0.03", good: true }),
  tile({
    id: "aov",
    title: "Average order value",
    decimals: 2,
    value: "62.386",
    change: null,
    direction: null,
    good: null,
  }),
  tile({
    id: "new_customers",
    title: "New customers",
    format: "count",
    value: "410",
    change: "-0.1",
    direction: "down",
    good: false,
  }),
  tile({
    id: "refund_rate",
    title: "Refund rate",
    format: "percent",
    decimals: 1,
    up_is_good: false,
    value: "0.0918",
    change: "-0.007",
    change_kind: "points",
    direction: "down",
    good: true,
  }),
];
const WEEKS = [
  trendWeek({ week_start: "2026-11-16", net_revenue: "7000" }),
  trendWeek({ week_start: "2026-11-23", net_revenue: "19000.5", is_black_friday_week: true }),
  trendWeek({ week_start: "2026-11-30", net_revenue: "3000", is_partial: true }),
];

describe("HomeScreen", () => {
  beforeEach(() => {
    useHome.mockReset().mockReturnValue({ data: home({ figures: { tiles: TILES, trend: WEEKS } }) });
  });

  it("shows five tiles, each a region named by its title with its value and change", () => {
    renderWithProviders(<HomeScreen />);

    const revenue = screen.getByRole("region", { name: "Net revenue" });
    expect(within(revenue).getByText("€34,812")).toBeInTheDocument();
    expect(within(revenue).getByText("▲ 12.0%")).toBeInTheDocument();
    expect(within(revenue).getByText("up 12.0% on the previous 30 days, an improvement")).toBeInTheDocument();
    const aov = screen.getByRole("region", { name: "Average order value" });
    expect(within(aov).getByText("€62.39")).toBeInTheDocument();
    expect(within(aov).getByText("no comparison with the previous 30 days")).toBeInTheDocument();
    expect(screen.getAllByRole("region")).toHaveLength(5);
  });

  it.each([
    ["Net revenue", "▲ 12.0%", "better"],
    ["Orders", "▲ 3.0%", "better"],
    ["New customers", "▼ 10.0%", "worse"],
    ["Refund rate", "▼ 0.7 pp", "better"],
    ["Average order value", "—", "no comparison"],
  ])("tells the %s change in one word beside the arrow", (name, change, word) => {
    renderWithProviders(<HomeScreen />);

    const card = screen.getByRole("region", { name });
    expect(within(card).getByText(change)).toBeInTheDocument();
    expect(within(card).getByText(word, { exact: true })).toBeInTheDocument();
  });

  it("shows a flat change without a verdict word", () => {
    const flat = tile({ id: "orders", title: "Orders", change: "0.0001", direction: "flat", good: null });
    useHome.mockReturnValue({ data: home({ figures: { tiles: [flat], trend: WEEKS } }) });

    renderWithProviders(<HomeScreen />);

    const card = screen.getByRole("region", { name: "Orders" });
    expect(within(card).getByText("0.0%")).toBeInTheDocument();
    expect(within(card).queryByText(/^(better|worse|no comparison)$/)).not.toBeInTheDocument();
    expect(within(card).getByText("unchanged on the previous 30 days")).toBeInTheDocument();
  });

  it("headlines the window and its dates, and drops the page title", () => {
    useHome.mockReturnValue({
      data: home({ figures: { tiles: TILES, trend: WEEKS }, status: { ...home().status, as_of_date: "2026-10-31" } }),
    });

    renderWithProviders(<HomeScreen />);

    expect(screen.getByRole("heading", { level: 2, name: "Last 30 days" })).toBeInTheDocument();
    expect(screen.getByText("to 2026-10-31, compared with the 30 days before")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Home" })).not.toBeInTheDocument();
  });

  it("leaves the dates out of the heading when there is no as-of date", () => {
    useHome.mockReturnValue({
      data: home({ figures: { tiles: TILES, trend: WEEKS }, status: { ...home().status, as_of_date: null } }),
    });

    renderWithProviders(<HomeScreen />);

    expect(screen.getByRole("heading", { level: 2, name: "Last 30 days" })).toBeInTheDocument();
    expect(screen.queryByText(/compared with the 30 days before/)).not.toBeInTheDocument();
  });

  it("reads a falling refund rate as an improvement", () => {
    renderWithProviders(<HomeScreen />);

    const refunds = screen.getByRole("region", { name: "Refund rate" });
    expect(within(refunds).getByText("9.2%")).toBeInTheDocument();
    expect(within(refunds).getByText("down 0.7 pp on the previous 30 days, an improvement")).toBeInTheDocument();
  });

  it("shows a tile's note when its info button is focused", async () => {
    renderWithProviders(<HomeScreen />);

    const revenue = screen.getByRole("region", { name: "Net revenue" });
    await act(async () => {
      fireEvent.focus(within(revenue).getByRole("button", { name: "About Net revenue" }));
    });

    expect(await screen.findByText("Orders not cancelled, minus refunds.")).toBeInTheDocument();
  });

  it("lists every week in the hidden table with its marks", () => {
    renderWithProviders(<HomeScreen />);

    const table = screen.getByRole("table", { name: "Net revenue per week" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(rows[0]).not.toHaveTextContent(/Black Friday|partial/);
    expect(rows[1]).toHaveTextContent("2026-11-23");
    expect(rows[1]).toHaveTextContent("€19,001");
    expect(rows[1]).toHaveTextContent("Black Friday week");
    expect(rows[2]).toHaveTextContent("partial");
  });

  it("titles the chart and says recent weeks read high until their refunds arrive", () => {
    renderWithProviders(<HomeScreen />);

    expect(screen.getByRole("heading", { name: "Weekly net revenue" })).toBeInTheDocument();
    expect(
      screen.getByText("Recent weeks read high until their refunds arrive (about 7 weeks)."),
    ).toBeInTheDocument();
  });

  it("keys only the unusual kinds of week present, in the caption row", () => {
    renderWithProviders(<HomeScreen />);

    const legend = screen.getByTestId("chart-legend");
    expect(within(legend).getAllByRole("listitem", { hidden: true }).map((item) => item.textContent)).toEqual([
      "Black Friday week",
      "Partial week",
    ]);
  });

  it("draws no legend when every week is regular", () => {
    useHome.mockReturnValue({ data: home({ figures: { tiles: TILES, trend: [trendWeek()] } }) });

    renderWithProviders(<HomeScreen />);

    expect(screen.queryByTestId("chart-legend")).not.toBeInTheDocument();
  });

  it("labels the x axis with short dates", () => {
    renderWithProviders(<HomeScreen />);

    expect(screen.getByTestId("chart-x-labels")).toHaveTextContent("16 Nov,23 Nov,30 Nov");
  });

  it("draws the partial week's bar translucent with a dashed outline, and the others solid", () => {
    renderWithProviders(<HomeScreen />);

    const looks = new Map(
      within(screen.getByTestId("chart-bar-props"))
        .getAllByRole("listitem", { hidden: true })
        .map((item) => {
          const [name, props] = item.textContent!.split("=");
          return [name, JSON.parse(props!) as { fillOpacity?: number; strokeDasharray?: string }];
        }),
    );
    expect(looks.get("Partial week")!.fillOpacity).toBeLessThan(1);
    expect(looks.get("Partial week")!.strokeDasharray).toBeTruthy();
    for (const name of ["Net revenue", "Black Friday week"]) {
      expect(looks.get(name)!.fillOpacity).toBeUndefined();
      expect(looks.get(name)!.strokeDasharray).toBeUndefined();
    }
  });

  it("gives the legend's partial key the same look as the bar: a translucent fill and a dashed border", () => {
    renderWithProviders(<HomeScreen />);

    const swatch = (name: string): HTMLElement =>
      within(screen.getByTestId("chart-legend"))
        .getByText(name, { exact: true })
        .parentElement!.querySelector("span")!;
    expect(swatch("Partial week").style.backgroundColor).toContain("transparent");
    expect(swatch("Partial week").style.border).toContain("dashed");
    expect(swatch("Black Friday week").style.backgroundColor).not.toContain("transparent");
    expect(swatch("Black Friday week").style.border).not.toContain("dashed");
  });

  it("draws a partial Black Friday week as partial and still marks both in the table", () => {
    const weeks = [trendWeek({ week_start: "2026-11-23", is_black_friday_week: true, is_partial: true })];
    useHome.mockReturnValue({ data: home({ figures: { tiles: TILES, trend: weeks } }) });

    renderWithProviders(<HomeScreen />);

    const series = within(screen.getByTestId("chart-series")).getAllByRole("listitem", { hidden: true });
    expect(series.map((item) => item.textContent)).toEqual(["Partial week"]);
    const row = within(screen.getByRole("table", { name: "Net revenue per week" })).getAllByRole("row")[1]!;
    expect(row).toHaveTextContent("Black Friday week, partial");
  });

  it.each([
    [[trendWeek({ week_start: "2026-10-05" })], ["Net revenue"]],
    [[trendWeek({ is_black_friday_week: true })], ["Black Friday week"]],
    [[trendWeek({ is_partial: true })], ["Partial week"]],
  ])("leaves out the series no week uses: %j", (weeks, expected) => {
    useHome.mockReturnValue({ data: home({ figures: { tiles: TILES, trend: weeks } }) });

    renderWithProviders(<HomeScreen />);

    const series = within(screen.getByTestId("chart-series")).getAllByRole("listitem", { hidden: true });
    expect(series.map((item) => item.textContent)).toEqual(expected);
  });


  it("orders the heading, the problems box, the tiles, the chart and the footer", () => {
    useHome.mockReturnValue({
      data: home({
        figures: { tiles: TILES, trend: WEEKS },
        status: { ...home().status, build: buildSummary({ failing_tests: 2 }) },
      }),
    });

    renderWithProviders(<HomeScreen />);

    const order = [
      screen.getByRole("heading", { name: "Last 30 days" }),
      screen.getByRole("region", { name: "Data problems" }),
      screen.getByRole("region", { name: "Net revenue" }),
      screen.getByRole("heading", { name: "Weekly net revenue" }),
      screen.getByRole("group", { name: "Data status" }),
    ];
    order.slice(1).forEach((element, index) => {
      expect(order[index]!.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    });
  });

  it("shows neither the problems box nor an empty container when the data is healthy", () => {
    useHome.mockReturnValue({
      data: home({ figures: { tiles: TILES, trend: WEEKS }, status: { ...home().status, build: buildSummary() } }),
    });

    renderWithProviders(<HomeScreen />);

    expect(screen.queryByRole("region", { name: "Data problems" })).not.toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Data status" })).toHaveTextContent("No failing tests");
  });

  describe.each(["no_data", "catalog_unavailable"] as const)("when the figures say %s", (code) => {
    it("shows the empty state in place of the tiles and trend, and keeps the problems box", () => {
      useHome.mockReturnValue({
        data: home({
          figures: null,
          figures_error: { code, message: "nothing" },
          status: { ...home().status, build: buildSummary({ failing_tests: 2 }) },
        }),
      });

      renderWithProviders(<HomeScreen />);

      expect(screen.getByText("No data yet")).toBeInTheDocument();
      expect(
        screen.getByText(
          "no orders in the marts yet: run make build-prod (first make backfill if uv run sherpa-generate status shows no simulation)",
        ),
      ).toBeInTheDocument();
      expect(screen.queryAllByRole("region", { name: /^(Net revenue|Orders|Average order value|Refund rate|New customers)$/ })).toHaveLength(0);
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      const box = within(screen.getByRole("region", { name: "Data problems" }));
      expect(box.getByRole("link", { name: "2 failing tests" })).toBeInTheDocument();
      expect(
        screen.getByRole("region", { name: "Data problems" }).compareDocumentPosition(screen.getByText("No data yet")) &
          Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    });
  });

  it.each(["database_unavailable", "timeout", "busy"] as const)(
    "shows the message and a Retry for %s, and keeps the box and the footer",
    async (code) => {
      const refetch = vi.fn();
      useHome.mockReturnValue({
        data: home({
          figures: null,
          figures_error: { code, message: `The figures failed (${code}).` },
          status: { ...home().status, build: buildSummary({ failing_tests: 2 }) },
        }),
        refetch,
      });

      renderWithProviders(<HomeScreen />);
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      });

      expect(screen.getByText(`The figures failed (${code}).`)).toBeInTheDocument();
      expect(screen.queryByText("No data yet")).not.toBeInTheDocument();
      expect(screen.getByRole("link", { name: "2 failing tests" })).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Last 30 days" })).not.toBeInTheDocument();
      expect(refetch).toHaveBeenCalledTimes(1);
    },
  );

  it("shows a loader that assistive technology announces while the first load runs", () => {
    useHome.mockReturnValue({ data: undefined, isError: false });

    renderWithProviders(<HomeScreen />);

    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
  });

  it("disables Retry while the refetch runs", () => {
    useHome.mockReturnValue({
      data: home({ figures: null, figures_error: { code: "timeout", message: "slow" } }),
      isFetching: true,
    });

    renderWithProviders(<HomeScreen />);

    expect(screen.getByRole("button", { name: "Retry" })).toBeDisabled();
  });

  it("enables Retry when no refetch runs", () => {
    useHome.mockReturnValue({
      data: home({ figures: null, figures_error: { code: "timeout", message: "slow" } }),
      isFetching: false,
    });

    renderWithProviders(<HomeScreen />);

    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  });

  it("says '1 failing test' in the box and no 'No failing tests' in the footer", () => {
    useHome.mockReturnValue({
      data: home({ figures: { tiles: TILES, trend: WEEKS }, status: { ...home().status, build: buildSummary({ failing_tests: 1 }) } }),
    });

    renderWithProviders(<HomeScreen />);

    expect(within(screen.getByRole("region", { name: "Data problems" })).getByRole("link", { name: "1 failing test" })).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Data status" })).not.toHaveTextContent("No failing tests");
  });

  it("shows the shared load error with a retry when the request itself fails", async () => {
    const refetch = vi.fn();
    useHome.mockReturnValue({ data: undefined, isError: true, error: new Error("boom"), refetch });

    renderWithProviders(<HomeScreen />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    });

    expect(screen.getByText("Could not load Home")).toBeInTheDocument();
    expect(screen.getByText("Could not reach the server.")).toBeInTheDocument();
    expect(refetch).toHaveBeenCalledTimes(1);
  });
});
