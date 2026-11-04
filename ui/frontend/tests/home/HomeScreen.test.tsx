import { act, fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HomeScreen } from "../../src/screens/home/HomeScreen";
import { buildSummary, home, tile, trendWeek } from "../fixtures/api";
import { renderWithProviders } from "../fixtures/render";

const useHome = vi.fn();
vi.mock("../../src/api/queries", () => ({ useHome: () => useHome() }));

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

  it("draws the chart hidden from assistive technology", () => {
    const { container } = renderWithProviders(<HomeScreen />);

    expect(container.querySelector("[data-testid='trend-chart']")).toHaveAttribute("aria-hidden", "true");
  });

  it("puts the data-status strip at the top, above the tiles", () => {
    useHome.mockReturnValue({
      data: home({ figures: { tiles: TILES, trend: WEEKS }, status: { ...home().status, as_of_date: "2026-10-31" } }),
    });

    renderWithProviders(<HomeScreen />);

    const strip = screen.getByText("Data as of 2026-10-31");
    const firstTile = screen.getByRole("region", { name: "Net revenue" });
    expect(strip.compareDocumentPosition(firstTile) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  describe.each(["no_data", "catalog_unavailable"] as const)("when the figures say %s", (code) => {
    it("shows the empty state in place of the tiles and trend, and keeps the strip", () => {
      useHome.mockReturnValue({
        data: home({
          figures: null,
          figures_error: { code, message: "nothing" },
          status: { ...home().status, build: buildSummary({ failing_tests: 2 }) },
        }),
      });

      renderWithProviders(<HomeScreen />);

      expect(screen.getByText("No data yet")).toBeInTheDocument();
      expect(screen.getByText("make backfill && make build-prod")).toBeInTheDocument();
      expect(screen.queryAllByRole("region")).toHaveLength(0);
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      expect(screen.getByRole("link", { name: "2 failing tests" })).toBeInTheDocument();
    });
  });

  it.each(["database_unavailable", "timeout", "busy"] as const)(
    "shows the message and a Retry for %s, and keeps the strip",
    async (code) => {
      const refetch = vi.fn();
      useHome.mockReturnValue({
        data: home({
          figures: null,
          figures_error: { code, message: `The figures failed (${code}).` },
          status: { ...home().status, as_of_date: "2026-10-31" },
        }),
        refetch,
      });

      renderWithProviders(<HomeScreen />);
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Retry" }));
      });

      expect(screen.getByText(`The figures failed (${code}).`)).toBeInTheDocument();
      expect(screen.queryByText("No data yet")).not.toBeInTheDocument();
      expect(screen.getByText("Data as of 2026-10-31")).toBeInTheDocument();
      expect(refetch).toHaveBeenCalledTimes(1);
    },
  );

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
