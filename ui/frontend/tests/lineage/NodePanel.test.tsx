import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NodePanel } from "../../src/screens/lineage/NodePanel";
import { columnDetail, nodeDetails } from "../fixtures/api";
import { renderWithProviders } from "../fixtures/render";

const useLineageNode = vi.fn();
vi.mock("../../src/api/queries", () => ({
  useLineageNode: (id: string | undefined) => useLineageNode(id),
}));

const DETAILS = nodeDetails({
  description: "One row per order.",
  status: "failed",
  failing_tests: 1,
  build: { status: "success", execution_time_s: 2.5, message: "SELECT 100" },
  columns: [
    columnDetail({ name: "order_id", data_type: "bigint", description: "Order key.", kind: "source" }),
    columnDetail({
      name: "total",
      data_type: "numeric(12,2)",
      description: "Order total.",
      kind: "transformed",
      resolution: "partial",
      notes: ["cast to numeric(12,2)"],
    }),
    columnDetail({ name: "orders", data_type: null, kind: "row_count", counts: "stg_orders" }),
  ],
  tests: [
    { name: "unique_order_id", kind: "generic", severity: "error", column: "order_id", outcome: "passed" },
    { name: "assert_totals", kind: "singular", severity: "error", column: null, outcome: "failed" },
    { name: "recent_orders", kind: "generic", severity: "warn", column: null, outcome: "warned" },
  ],
  raw_sql: "select * from {{ ref('stg_orders') }}",
  compiled_sql: "select * from stg_orders",
});

type FocusProps = { column?: string | undefined; outside?: number; onFocusColumn?: (column: string | undefined) => void };

function panel(uniqueId: string | undefined, onClose: () => void, focus: FocusProps = {}) {
  return (
    <>
      <NodePanel
        uniqueId={uniqueId}
        onClose={onClose}
        column={focus.column}
        outside={focus.outside ?? 0}
        onFocusColumn={focus.onFocusColumn ?? (() => {})}
      />
      <input aria-label="elsewhere" />
    </>
  );
}

function mount(uniqueId: string | null = "model.sherpa.fct_orders", onClose = vi.fn(), focus: FocusProps = {}) {
  const view = renderWithProviders(panel(uniqueId ?? undefined, onClose, focus), {});
  return { onClose, view, select: (id: string) => view.rerender(panel(id, onClose, focus)) };
}

const openTab = (name: string) => userEvent.click(screen.getByRole("tab", { name }));

describe("NodePanel", () => {
  beforeEach(() => {
    useLineageNode.mockReset().mockReturnValue({ data: DETAILS });
  });

  it("the panel shows its empty state with no node selected", () => {
    mount(null);

    expect(screen.getByText("Select a node to see its SQL, columns and tests")).toBeVisible();
    expect(screen.getByRole("complementary", { name: "Node details" })).toBeInTheDocument();
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Close the node panel" })).not.toBeInTheDocument();
  });

  it("the Overview tab opens first with the metadata, the last build and the actions", () => {
    mount();

    expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("heading", { name: "fct_orders" })).toBeInTheDocument();
    expect(screen.getByText("One row per order.")).toBeVisible();
    expect(screen.getByText("marts")).toBeVisible();
    expect(screen.getByText("table")).toBeVisible();
    expect(screen.getByText("sales")).toBeVisible();
    expect(screen.getByText("models/marts/sales/fct_orders.sql")).toBeVisible();
    expect(screen.getByText("Contract:").parentElement).toHaveTextContent("Contract: enforced");
    expect(screen.getByTestId("node-status")).toHaveTextContent("failed · 1 failing test");
    expect(screen.getByText(/2\.5 s/)).toBeVisible();
    expect(screen.getByText("SELECT 100")).toBeVisible();
    expect(screen.getByRole("button", { name: "Copy dbt selector" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Open in Explorer" })).toBeVisible();
  });

  it("the Columns tab lists kind and resolution", async () => {
    mount();

    await openTab("Columns");

    const rows = within(screen.getByRole("table", { name: "Columns" })).getAllByRole("row");
    expect(rows[0]).toHaveTextContent("NameTypeKindResolutionNotes");
    expect(rows[1]).toHaveTextContent("order_idbigintsourcefull");
    expect(rows[2]).toHaveTextContent("totalnumeric(12,2)transformedpartialcast to numeric(12,2)");
    expect(rows[3]).toHaveTextContent("orders");
    expect(rows[3]).toHaveTextContent("row count");
    expect(rows[3]).toHaveTextContent("stg_orders");
  });

  it("the Columns tab says so when the node has no columns", async () => {
    useLineageNode.mockReturnValue({ data: nodeDetails({ columns: [] }) });
    mount();

    await openTab("Columns");

    expect(screen.getByText("No documented columns.")).toBeVisible();
  });

  it("failures are listed first in the Tests tab", async () => {
    mount();

    await openTab("Tests");

    const groups = screen.getAllByRole("heading", { level: 6 }).map((heading) => heading.textContent);
    const tests = screen.getAllByTestId("test-name").map((name) => name.textContent);
    expect(groups).toEqual(["failed (1)", "warned (1)", "passed (1)"]);
    expect(tests).toEqual(["assert_totals", "recent_orders", "unique_order_id"]);
  });

  it("the Tests tab says so when the node has no tests", async () => {
    useLineageNode.mockReturnValue({ data: nodeDetails({ tests: [] }) });
    mount();

    await openTab("Tests");

    expect(screen.getByText("No tests.")).toBeVisible();
  });

  it("the SQL tab shows the raw SQL, with the compiled SQL beside it", async () => {
    mount();

    await openTab("SQL");

    expect(screen.getByRole("tab", { name: "Raw SQL" })).toHaveAttribute("aria-selected", "true");
    expect(document.querySelector(".cm-content")).toHaveTextContent("select * from {{ ref('stg_orders') }}");
    await userEvent.click(screen.getByRole("tab", { name: "Compiled SQL" }));
    expect(screen.getByRole("tab", { name: "Compiled SQL" })).toHaveAttribute("aria-selected", "true");
  });

  it("the SQL tab shows the raw SQL and a note when there is no compiled SQL", async () => {
    useLineageNode.mockReturnValue({
      data: { ...DETAILS, compiled_sql: null, sql_note: "The compiled file is missing; run make build-prod." },
    });
    mount();

    await openTab("SQL");

    expect(screen.getByText("The compiled file is missing; run make build-prod.")).toBeVisible();
    expect(screen.queryByRole("tab", { name: "Compiled SQL" })).not.toBeInTheDocument();
    expect(document.querySelector(".cm-content")).toHaveTextContent("select * from {{ ref('stg_orders') }}");
  });

  it("the tab persists across node selections", async () => {
    const { select } = mount("model.sherpa.fct_orders");
    await openTab("Columns");
    useLineageNode.mockReturnValue({
      data: nodeDetails({
        unique_id: "model.sherpa.dim_customers",
        name: "dim_customers",
        columns: [DETAILS.columns[0]!],
      }),
    });

    select("model.sherpa.dim_customers");

    expect(screen.getByRole("tab", { name: "Columns" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("table", { name: "Columns" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "dim_customers" })).toBeInTheDocument();
  });

  it("Esc closes it, except while typing in a field", async () => {
    const user = userEvent.setup();
    const { onClose } = mount();

    await user.click(screen.getByRole("textbox", { name: "elsewhere" }));
    await user.keyboard("{Escape}");
    expect(onClose).not.toHaveBeenCalled();

    await user.click(document.body);
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Esc does nothing with no node selected", async () => {
    const user = userEvent.setup();
    const { onClose } = mount(null);

    await user.keyboard("{Escape}");

    expect(onClose).not.toHaveBeenCalled();
  });

  it("the close button closes it", async () => {
    const { onClose } = mount();

    await userEvent.click(screen.getByRole("button", { name: "Close the node panel" }));

    expect(onClose).toHaveBeenCalledOnce();
  });

  it("Copy dbt selector copies the selector of a model and says so", async () => {
    const user = userEvent.setup();
    mount();

    await user.click(screen.getByRole("button", { name: "Copy dbt selector" }));

    expect(await navigator.clipboard.readText()).toBe("fct_orders");
    expect(screen.getByRole("button", { name: "Copied" })).toBeInTheDocument();
  });

  it("Copy dbt selector copies what selects a source, not its table name", async () => {
    const user = userEvent.setup();
    useLineageNode.mockReturnValue({
      data: nodeDetails({ name: "orders", resource_type: "source", layer: "source", selector: "source:shop.orders" }),
    });
    mount();

    await user.click(screen.getByRole("button", { name: "Copy dbt selector" }));

    expect(await navigator.clipboard.readText()).toBe("source:shop.orders");
  });

  it("Open in Explorer appears for marts only", () => {
    const { view } = mount();
    expect(screen.getByRole("link", { name: "Open in Explorer" })).toHaveAttribute("href", "/explorer?table=fct_orders");
    view.unmount();

    useLineageNode.mockReturnValue({ data: nodeDetails({ name: "stg_orders", is_mart: false, layer: "staging" }) });
    mount();

    expect(screen.queryByRole("link", { name: "Open in Explorer" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy dbt selector" })).toBeInTheDocument();
  });

  it("a node_not_found shows a note", () => {
    const error = Object.assign(new Error("no node model.s.gone"), { code: "node_not_found", status: 404 });
    useLineageNode.mockReturnValue({ isError: true, error, refetch: vi.fn() });
    mount();

    expect(screen.getByRole("alert")).toHaveTextContent("no node model.s.gone");
  });

  describe("column focus", () => {
    it("clicking a column in the Columns tab focuses it", async () => {
      const onFocusColumn = vi.fn();
      mount("model.sherpa.fct_orders", vi.fn(), { onFocusColumn });
      await openTab("Columns");

      await userEvent.click(screen.getByRole("button", { name: "total" }));

      expect(onFocusColumn).toHaveBeenCalledExactlyOnceWith("total");
    });

    it("the focused column is marked in the Columns tab, which opens by itself", () => {
      mount("model.sherpa.fct_orders", vi.fn(), { column: "total" });

      expect(screen.getByRole("tab", { name: "Columns" })).toHaveAttribute("aria-selected", "true");
      const rows = within(screen.getByRole("table", { name: "Columns" })).getAllByRole("row");
      expect(rows[2]).toHaveAttribute("aria-current", "true");
      expect(rows[1]).not.toHaveAttribute("aria-current");
    });

    it("focusing a column from the graph opens the Columns tab", () => {
      const { view } = mount();
      expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute("aria-selected", "true");

      view.rerender(panel("model.sherpa.fct_orders", vi.fn(), { column: "total" }));

      expect(screen.getByRole("tab", { name: "Columns" })).toHaveAttribute("aria-selected", "true");
    });

    it("Esc clears the column focus first and closes only when there is none", async () => {
      const user = userEvent.setup();
      const onFocusColumn = vi.fn();
      const { onClose } = mount("model.sherpa.fct_orders", vi.fn(), { column: "total", onFocusColumn });

      await user.keyboard("{Escape}");

      expect(onFocusColumn).toHaveBeenCalledExactlyOnceWith(undefined);
      expect(onClose).not.toHaveBeenCalled();
    });

    it.each([
      [1, "1 related column is outside this view. Show it with"],
      [3, "3 related columns are outside this view. Show them with"],
    ])("%i related column(s) outside the view link to the selector that holds them", (outside, text) => {
      mount("model.sherpa.fct_orders", vi.fn(), { column: "total", outside });

      expect(screen.getByRole("note")).toHaveTextContent(text);
      const link = within(screen.getByRole("note")).getByRole("link", { name: "+fct_orders+" });
      expect(link).toHaveAttribute(
        "href",
        "/lineage?select=%2Bfct_orders%2B&node=model.sherpa.fct_orders&column=total",
      );
    });

    it("nothing outside the view, no note", () => {
      mount("model.sherpa.fct_orders", vi.fn(), { column: "total", outside: 0 });

      expect(screen.queryByRole("note")).not.toBeInTheDocument();
    });

    it("a column's kind and resolution are an icon plus text", async () => {
      mount();
      await openTab("Columns");

      const total = within(screen.getByRole("table", { name: "Columns" })).getAllByRole("row")[2]!;

      expect(total.querySelectorAll("svg[aria-hidden]")).toHaveLength(2);
      expect(total).toHaveTextContent("transformed");
      expect(total).toHaveTextContent("partial");
    });
  });
  it("the outside-the-view link of a source node selects it by its source selector", () => {
    useLineageNode.mockReturnValue({
      data: nodeDetails({ unique_id: "source.s.shop.orders", name: "orders", selector: "source:shop.orders" }),
    });
    mount("source.s.shop.orders", vi.fn(), { column: "id", outside: 2 });

    const link = within(screen.getByRole("note")).getByRole("link", { name: "+source:shop.orders+" });

    expect(link).toHaveAttribute(
      "href",
      "/lineage?select=%2Bsource%3Ashop.orders%2B&node=source.s.shop.orders&column=id",
    );
  });

});
