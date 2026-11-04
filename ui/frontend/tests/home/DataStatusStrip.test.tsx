import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DataFooter, DataProblems } from "../../src/screens/home/DataStatusStrip";
import { buildSummary, dataStatus, sourceFreshness } from "../fixtures/api";
import { renderWithProviders } from "../fixtures/render";

const CHECKED = "2026-11-01T14:40:30Z";
const CHECKED_TEXT = "2026-11-01 14:40 UTC";
const HEALTHY = dataStatus({ build: buildSummary(), freshness: { checked_at: CHECKED, problems: [] } });

function problemsBox(): HTMLElement {
  return screen.getByRole("region", { name: "Data problems" });
}

describe("DataFooter", () => {
  it("joins the healthy facts in one quiet line", () => {
    renderWithProviders(<DataFooter status={HEALTHY} />);

    const footer = screen.getByRole("group", { name: "Data status" });
    expect(footer).toHaveTextContent(
      `Last full build 2026-11-01 14:39 UTC · 398 nodes · No failing tests · Sources fresh (checked ${CHECKED_TEXT})`,
    );
    expect(within(footer).queryByRole("link")).not.toBeInTheDocument();
  });

  it("leaves out what is a problem", () => {
    const status = dataStatus({
      build: buildSummary({ failing_tests: 3 }),
      freshness: { checked_at: CHECKED, problems: [sourceFreshness()] },
    });

    renderWithProviders(<DataFooter status={status} />);

    const footer = screen.getByRole("group", { name: "Data status" });
    expect(footer).toHaveTextContent("Last full build 2026-11-01 14:39 UTC · 398 nodes");
    expect(footer).not.toHaveTextContent(/failing|Sources fresh|warning/);
  });

  it("renders nothing when no item is healthy", () => {
    const status = dataStatus({ build_note: "build status unavailable", freshness_note: "freshness unavailable" });

    renderWithProviders(<DataFooter status={status} />);

    expect(screen.queryByRole("group", { name: "Data status" })).not.toBeInTheDocument();
  });
});

describe("DataProblems", () => {
  it("renders no box and no container when nothing is a problem", () => {
    const { container } = renderWithProviders(<DataProblems status={HEALTHY} />);

    expect(screen.queryByRole("region", { name: "Data problems" })).not.toBeInTheDocument();
    expect(container.querySelector("div")).toBeNull();
  });

  it("titles a box with a failing test 'Data errors' and links to the failing lineage", () => {
    renderWithProviders(<DataProblems status={dataStatus({ build: buildSummary({ failing_tests: 3 }) })} />);

    const box = problemsBox();
    expect(within(box).getByText("Data errors")).toBeInTheDocument();
    expect(within(box).getByRole("link", { name: "3 failing tests" })).toHaveAttribute("href", "/lineage?failing=1");
  });

  it.each([
    [1, "1 failed node"],
    [2, "2 failed nodes"],
  ])("links %i failed nodes next to the failing tests", (count, name) => {
    const status = dataStatus({ build: buildSummary({ failed_nodes: count, failing_tests: 1 }) });

    renderWithProviders(<DataProblems status={status} />);

    const box = problemsBox();
    expect(within(box).getByRole("link", { name })).toHaveAttribute("href", "/lineage?failing=1");
    expect(within(box).getByRole("link", { name: "1 failing test" })).toHaveAttribute("href", "/lineage?failing=1");
  });

  it("titles a box with only a freshness warning 'Data warnings'", () => {
    const status = dataStatus({ freshness: { checked_at: CHECKED, problems: [sourceFreshness()] } });

    renderWithProviders(<DataProblems status={status} />);

    const box = problemsBox();
    expect(within(box).getByText("Data warnings")).toBeInTheDocument();
    expect(within(box).queryByText("Data errors")).not.toBeInTheDocument();
    expect(
      within(box).getByText(`marketing.ad_spend_daily: warning, last loaded 49 h ago (checked ${CHECKED_TEXT})`),
    ).toBeInTheDocument();
  });

  it("titles a box with a freshness error 'Data errors' and lists errors before warnings", () => {
    const status = dataStatus({
      freshness: {
        checked_at: CHECKED,
        problems: [
          sourceFreshness(),
          sourceFreshness({ name: "shop.orders", status: "error", hours_ago: 120 }),
          sourceFreshness({ name: "shop.carts", status: "error", hours_ago: null, max_loaded_at: null }),
        ],
      },
    });

    renderWithProviders(<DataProblems status={status} />);

    const box = problemsBox();
    expect(within(box).getByText("Data errors")).toBeInTheDocument();
    expect(within(box).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      `shop.orders: error, last loaded 120 h ago (checked ${CHECKED_TEXT})`,
      `shop.carts: error, never loaded (checked ${CHECKED_TEXT})`,
      `marketing.ad_spend_daily: warning, last loaded 49 h ago (checked ${CHECKED_TEXT})`,
    ]);
  });

  it("is not announced as an alert", () => {
    renderWithProviders(<DataProblems status={dataStatus({ build: buildSummary({ failing_tests: 1 }) })} />);

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it.each([
    ["build_note", "no full prod build recorded (run make build-prod)"],
    ["build_note", "build status unavailable"],
    ["freshness_note", "no freshness check recorded (run make build-prod)"],
    ["freshness_note", "freshness unavailable"],
  ] as const)("shows the missing item's note in the box as a warning: %s %s", (field, note) => {
    const status = dataStatus({ [field]: note });

    renderWithProviders(
      <>
        <DataProblems status={status} />
        <DataFooter status={status} />
      </>,
    );

    const box = problemsBox();
    expect(within(box).getByText("Data warnings")).toBeInTheDocument();
    expect(within(box).getByText(note)).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Data status" })).not.toBeInTheDocument();
  });

  it("shows the first three items and folds the rest behind 'Show N more'", () => {
    const status = dataStatus({
      build: buildSummary({ failed_nodes: 2, failing_tests: 4 }),
      freshness: {
        checked_at: CHECKED,
        problems: [sourceFreshness({ name: "a.one" }), sourceFreshness({ name: "b.two", status: "error" })],
      },
    });

    renderWithProviders(<DataProblems status={status} />);

    const box = problemsBox();
    expect(within(box).getAllByRole("listitem")).toHaveLength(3);
    expect(within(box).queryByText(/^a\.one/)).not.toBeInTheDocument();
    fireEvent.click(within(box).getByRole("button", { name: "Show 1 more" }));
    expect(within(box).getAllByRole("listitem")).toHaveLength(4);
    expect(within(box).getByText(/^a\.one: warning/)).toBeInTheDocument();
  });

  it("does not fold exactly three items", () => {
    const status = dataStatus({
      build: buildSummary({ failed_nodes: 2, failing_tests: 4 }),
      freshness: { checked_at: CHECKED, problems: [sourceFreshness()] },
    });

    renderWithProviders(<DataProblems status={status} />);

    expect(within(problemsBox()).getAllByRole("listitem")).toHaveLength(3);
    expect(screen.queryByRole("button", { name: /Show/ })).not.toBeInTheDocument();
  });
});
