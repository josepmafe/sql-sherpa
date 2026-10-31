import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NodePanel } from "../../src/screens/lineage/NodePanel";
import { nodeDetails } from "../fixtures/api";
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
    {
      name: "order_id",
      data_type: "bigint",
      description: "Order key.",
      kind: "source",
      resolution: "full",
      notes: [],
      counts: null,
    },
    {
      name: "total",
      data_type: "numeric(12,2)",
      description: "Order total.",
      kind: "transformed",
      resolution: "partial",
      notes: ["cast to numeric(12,2)"],
      counts: null,
    },
    {
      name: "orders",
      data_type: null,
      description: "",
      kind: "row_count",
      resolution: "full",
      notes: [],
      counts: "stg_orders",
    },
  ],
  tests: [
    { name: "unique_order_id", kind: "generic", severity: "error", column: "order_id", outcome: "passed" },
    { name: "assert_totals", kind: "singular", severity: "error", column: null, outcome: "failed" },
    { name: "recent_orders", kind: "generic", severity: "warn", column: null, outcome: "warned" },
  ],
  raw_sql: "select * from {{ ref('stg_orders') }}",
  compiled_sql: "select * from stg_orders",
});

function panel(uniqueId: string | undefined, onClose: () => void) {
  return (
    <>
      <NodePanel uniqueId={uniqueId} onClose={onClose} />
      <input aria-label="elsewhere" />
    </>
  );
}

function mount(uniqueId: string | null = "model.sherpa.fct_orders", onClose = vi.fn()) {
  const view = renderWithProviders(panel(uniqueId ?? undefined, onClose));
  return { onClose, view, select: (id: string) => view.rerender(panel(id, onClose)) };
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
});
