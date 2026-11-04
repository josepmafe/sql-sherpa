import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

  it("titles a box with only a freshness warning 'Data warnings (1)'", async () => {
    const status = dataStatus({ freshness: { checked_at: CHECKED, problems: [sourceFreshness()] } });

    renderWithProviders(<DataProblems status={status} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Data warnings (1)" }));

    const box = problemsBox();
    expect(within(box).getByText("Data warnings (1)")).toBeInTheDocument();
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

  describe("when there are only warnings", () => {
    const warnings = (count: number) =>
      dataStatus({
        freshness: {
          checked_at: CHECKED,
          problems: Array.from({ length: count }, (_, index) => sourceFreshness({ name: `src.n${index}` })),
        },
      });

    it("puts the chevron, the icon and the title inside the one summary button", () => {
      renderWithProviders(<DataProblems status={warnings(1)} />);

      const button = within(problemsBox()).getByRole("button", { name: "Data warnings (1)" });
      expect(button.querySelectorAll("svg")).toHaveLength(2);
    });

    it("is collapsed by default: the summary button counts the items and the list is not rendered", () => {
      renderWithProviders(<DataProblems status={warnings(1)} />);

      const button = within(problemsBox()).getByRole("button", { name: "Data warnings (1)" });
      expect(button).toHaveAttribute("aria-expanded", "false");
      expect(screen.queryByRole("list")).not.toBeInTheDocument();
      expect(screen.queryByText(/src\.n0/)).not.toBeInTheDocument();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it.each(["{Enter}", " "])("opens and closes from the keyboard (%j), with aria-controls on the list", async (key) => {
      const user = userEvent.setup();
      renderWithProviders(<DataProblems status={warnings(2)} />);
      const button = screen.getByRole("button", { name: "Data warnings (2)" });
      button.focus();

      await user.keyboard(key);

      expect(button).toHaveAttribute("aria-expanded", "true");
      expect(within(problemsBox()).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
        `src.n0: warning, last loaded 49 h ago (checked ${CHECKED_TEXT})`,
        `src.n1: warning, last loaded 49 h ago (checked ${CHECKED_TEXT})`,
      ]);
      expect(document.getElementById(button.getAttribute("aria-controls")!)).toContainElement(screen.getByRole("list"));

      await user.keyboard(key);

      expect(button).toHaveAttribute("aria-expanded", "false");
      expect(screen.queryByRole("list")).not.toBeInTheDocument();
    });

    it("folds a long list after three items inside the open disclosure", async () => {
      const user = userEvent.setup();
      renderWithProviders(<DataProblems status={warnings(4)} />);
      expect(screen.queryByRole("button", { name: /^Show/ })).not.toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Data warnings (4)" }));

      expect(within(problemsBox()).getAllByRole("listitem")).toHaveLength(3);
      await user.click(screen.getByRole("button", { name: "Show 1 more" }));
      expect(within(problemsBox()).getAllByRole("listitem")).toHaveLength(4);
    });
  });

  it("keeps the box open and red when any item is an error, with no summary button", () => {
    const status = dataStatus({
      freshness: {
        checked_at: CHECKED,
        problems: [sourceFreshness(), sourceFreshness({ name: "shop.orders", status: "error" })],
      },
    });

    renderWithProviders(<DataProblems status={status} />);

    expect(within(problemsBox()).getByText("Data errors")).toBeInTheDocument();
    expect(within(problemsBox()).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
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
  ] as const)("shows the missing item's note in the box as a warning: %s %s", async (field, note) => {
    const status = dataStatus({ [field]: note });

    renderWithProviders(
      <>
        <DataProblems status={status} />
        <DataFooter status={status} />
      </>,
    );

    await userEvent.setup().click(screen.getByRole("button", { name: "Data warnings (1)" }));
    const box = problemsBox();
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

  it.each(["{Enter}", " "])("opens the fold from the keyboard (%j) and says so with aria-expanded", async (key) => {
    const folded = dataStatus({
      build: buildSummary({ failed_nodes: 2, failing_tests: 4 }),
      freshness: { checked_at: CHECKED, problems: [sourceFreshness(), sourceFreshness({ name: "b.two" })] },
    });
    const user = userEvent.setup();
    renderWithProviders(<DataProblems status={folded} />);
    const button = screen.getByRole("button", { name: "Show 1 more" });
    expect(button).toHaveAttribute("aria-expanded", "false");

    button.focus();
    await user.keyboard(key);

    const opened = screen.getByRole("button", { name: "Show less" });
    expect(opened).toHaveAttribute("aria-expanded", "true");
    expect(opened.getAttribute("aria-controls")).toBe(within(problemsBox()).getAllByRole("list")[0]!.id);
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
