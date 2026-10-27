import { MantineProvider } from "@mantine/core";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { NodePanel } from "../../src/screens/lineage/NodePanel";

const useLineageNode = vi.fn();
vi.mock("../../src/api/queries", () => ({
  useLineageNode: (id: string | undefined) => useLineageNode(id),
}));

const DETAILS = {
  unique_id: "model.s.fct_orders",
  name: "fct_orders",
  resource_type: "model",
  layer: "marts",
  materialization: "table",
  domain: "sales",
  contract_enforced: true,
  selector: "fct_orders",
  file_path: "models/marts/fct_orders.sql",
  description: "One row per order.",
  is_mart: true,
  status: "failed",
  failing_tests: 1,
  build: { status: "success", execution_time_s: 2.5, message: "SELECT 100" },
  columns: [{ name: "order_id", data_type: "bigint", description: "Order key." }],
  tests: [
    { name: "unique_order_id", kind: "generic", severity: "error", column: "order_id", outcome: "passed" },
    { name: "assert_totals", kind: "singular", severity: "error", column: null, outcome: "failed" },
    { name: "recent_orders", kind: "generic", severity: "warn", column: null, outcome: "warned" },
  ],
  raw_sql: "select * from {{ ref('stg_orders') }}",
  compiled_sql: "select * from stg_orders",
  sql_note: null,
};

function mount(onClose = vi.fn()) {
  render(
    <MantineProvider env="test">
      <MemoryRouter>
        <NodePanel uniqueId="model.s.fct_orders" onClose={onClose} />
        <input aria-label="elsewhere" />
      </MemoryRouter>
    </MantineProvider>,
  );
  return onClose;
}

describe("NodePanel", () => {
  beforeEach(() => {
    useLineageNode.mockReset().mockReturnValue({ data: DETAILS });
  });

  it("shows build status, tests and columns", () => {
    mount();

    expect(screen.getByRole("heading", { name: "fct_orders" })).toBeInTheDocument();
    expect(screen.getByText("One row per order.")).toBeInTheDocument();
    expect(screen.getByText("models/marts/fct_orders.sql")).toBeInTheDocument();
    expect(screen.getByText("Contract:").parentElement).toHaveTextContent("Contract: enforced");
    expect(screen.getByTestId("node-status")).toHaveTextContent("failed · 1 failing test");
    expect(screen.getByText(/2\.5 s/)).toBeInTheDocument();
    expect(screen.getByText("SELECT 100")).toBeInTheDocument();
    expect(screen.getByText("order_id")).toBeInTheDocument();
    expect(screen.getByText("bigint")).toBeInTheDocument();
    expect(screen.getByText("Order key.")).toBeInTheDocument();
    expect(screen.getByText("unique_order_id")).toBeInTheDocument();
  });

  it("failures are listed first", () => {
    mount();

    const groups = screen.getAllByRole("heading", { level: 6 }).map((heading) => heading.textContent);
    const tests = screen.getAllByTestId("test-name").map((name) => name.textContent);

    expect(groups.filter((text) => /^(failed|warned|passed)/.test(text ?? ""))).toEqual([
      "failed (1)",
      "warned (1)",
      "passed (1)",
    ]);
    expect(tests).toEqual(["assert_totals", "recent_orders", "unique_order_id"]);
  });

  it("shows the raw SQL and a note when there is no compiled SQL", () => {
    useLineageNode.mockReturnValue({
      data: { ...DETAILS, compiled_sql: null, sql_note: "The compiled file is missing; run make build-prod." },
    });
    mount();

    expect(screen.getByText("The compiled file is missing; run make build-prod.")).toBeVisible();
    expect(screen.getByRole("tab", { name: "Raw SQL" })).toHaveAttribute("aria-selected", "true");
    expect(document.querySelector(".cm-content")).toHaveTextContent("select * from {{ ref('stg_orders') }}");
  });

  it("Esc closes it, except while typing in a field", async () => {
    const user = userEvent.setup();
    const onClose = mount();

    await user.click(screen.getByRole("textbox", { name: "elsewhere" }));
    await user.keyboard("{Escape}");
    expect(onClose).not.toHaveBeenCalled();

    await user.click(document.body);
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("the close button closes it", async () => {
    const onClose = mount();

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
      data: { ...DETAILS, name: "orders", resource_type: "source", layer: "source", selector: "source:shop.orders" },
    });
    mount();

    await user.click(screen.getByRole("button", { name: "Copy dbt selector" }));

    expect(await navigator.clipboard.readText()).toBe("source:shop.orders");
  });

  it("Open in Explorer appears for marts only", () => {
    const { unmount } = render(
      <MantineProvider env="test">
        <MemoryRouter>
          <NodePanel uniqueId="model.s.fct_orders" onClose={() => {}} />
        </MemoryRouter>
      </MantineProvider>,
    );
    expect(screen.getByRole("link", { name: "Open in Explorer" })).toHaveAttribute("href", "/explorer?table=fct_orders");
    unmount();

    useLineageNode.mockReturnValue({ data: { ...DETAILS, name: "stg_orders", is_mart: false, layer: "staging" } });
    mount();

    expect(screen.queryByRole("link", { name: "Open in Explorer" })).not.toBeInTheDocument();
    expect(within(document.body).getByRole("button", { name: "Copy dbt selector" })).toBeInTheDocument();
  });

  it("a node_not_found shows a note", () => {
    const error = Object.assign(new Error("no node model.s.gone"), { code: "node_not_found", status: 404 });
    useLineageNode.mockReturnValue({ isError: true, error, refetch: vi.fn() });
    mount();

    expect(screen.getByRole("alert")).toHaveTextContent("no node model.s.gone");
  });
});
