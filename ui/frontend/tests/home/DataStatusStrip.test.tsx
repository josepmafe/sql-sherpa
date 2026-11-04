import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DataStatusStrip } from "../../src/screens/home/DataStatusStrip";
import { buildSummary, dataStatus, sourceFreshness } from "../fixtures/api";
import { renderWithProviders } from "../fixtures/render";

const CHECKED = "2026-11-01T14:40:30Z";

describe("DataStatusStrip", () => {
  it("shows the data date, the last full build and fresh sources", () => {
    const status = dataStatus({
      as_of_date: "2026-10-31",
      build: buildSummary(),
      freshness: { checked_at: CHECKED, problems: [] },
    });

    renderWithProviders(<DataStatusStrip status={status} />);

    expect(screen.getByText("Data as of 2026-10-31")).toBeInTheDocument();
    expect(
      screen.getByText("Last full build 2026-11-01 14:39 UTC · 398 nodes"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Sources fresh (checked 2026-11-01 14:40 UTC)"),
    ).toBeInTheDocument();
    expect(screen.getByText("No failing tests")).toBeInTheDocument();
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("links the failing tests to the lineage filtered to failing nodes", () => {
    const status = dataStatus({ build: buildSummary({ failing_tests: 3 }) });

    renderWithProviders(<DataStatusStrip status={status} />);

    expect(
      screen.getByRole("link", { name: "3 failing tests" }),
    ).toHaveAttribute("href", "/lineage?failing=1");
  });

  it("lists each source with a problem, worst wording first as given", () => {
    const status = dataStatus({
      freshness: {
        checked_at: CHECKED,
        problems: [
          sourceFreshness(),
          sourceFreshness({
            name: "shop.orders",
            status: "error",
            hours_ago: 120,
          }),
          sourceFreshness({
            name: "shop.carts",
            status: "error",
            hours_ago: null,
            max_loaded_at: null,
          }),
        ],
      },
    });

    renderWithProviders(<DataStatusStrip status={status} />);

    expect(
      screen.getByText(
        "marketing.ad_spend_daily: warning, last loaded 49 h ago",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("shop.orders: error, last loaded 120 h ago"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("shop.carts: error, never loaded"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Sources fresh/)).not.toBeInTheDocument();
  });

  it.each([
    ["build_note", "no full prod build recorded (run make build-prod)"],
    ["build_note", "build status unavailable"],
    ["freshness_note", "no freshness check recorded (run make build-prod)"],
    ["freshness_note", "freshness unavailable"],
  ] as const)("replaces a missing item with its note: %s %s", (field, note) => {
    renderWithProviders(
      <DataStatusStrip status={dataStatus({ [field]: note })} />,
    );

    expect(screen.getByText(note)).toBeInTheDocument();
    expect(screen.queryByText(/failing test/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Sources fresh/)).not.toBeInTheDocument();
  });
});
