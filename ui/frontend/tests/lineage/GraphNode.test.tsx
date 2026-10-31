import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { COLUMN_ROW_HEIGHT, NODE_HEIGHT } from "../../src/screens/lineage/layout";

import { CanvasHarness, COLUMN_EDGES, COLUMN_FOCUS, COLUMN_NODES, FCT, node, rankInInputOrder, STG } from "./canvasHarness";
import { columnView } from "../fixtures/api";

vi.mock("../../src/screens/lineage/layout", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/screens/lineage/layout")>()),
  orderNodes: vi.fn(),
}));

const chevron = (name: string) => screen.findByRole("button", { name: `Columns of ${name}` });
const row = (name: string) => screen.getByRole("button", { name: new RegExp(`^${name}\\b`) });
const card = (id: string) => document.querySelector<HTMLElement>(`[data-id="${id}"]`)!;

describe("GraphNode", () => {
  beforeEach(rankInInputOrder);

  it("expanding lists the columns with kind and resolution", async () => {
    render(<CanvasHarness nodes={COLUMN_NODES} edges={COLUMN_EDGES} />);
    const toggle = await chevron("fct_orders");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(within(card(FCT)).queryByRole("button", { name: /^net_revenue/ })).not.toBeInTheDocument();

    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute("aria-expanded", "true");
    const rows = within(card(FCT)).getAllByRole("button", { name: /^(net_revenue|units|order_id)\b/ });
    expect(rows.map((r) => r.textContent)).toEqual(["net_revenue transformed partial", "units row count", "order_id passthrough"]);
  });

  it("expanding a node does not select it", async () => {
    render(<CanvasHarness nodes={COLUMN_NODES} edges={COLUMN_EDGES} />);

    fireEvent.click(await chevron("fct_orders"));

    expect(screen.getByRole("button", { name: /^fct_orders\b/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("the node grows by one row height per column and pushes the nodes below it down", async () => {
    const sameBand = [node("a", { columns: [columnView({ name: "k" }), columnView({ name: "v" })] }), node("b")];
    render(<CanvasHarness nodes={sameBand} edges={[]} />);
    const toggle = await chevron("a");
    const y = (id: string) => Number(/translate\([-\d.]+px,\s*([-\d.]+)px\)/.exec(card(id).style.transform)?.[1]);
    const before = y("model.s.b");
    expect(card("model.s.a").style.height).toBe(`${NODE_HEIGHT}px`);

    fireEvent.click(toggle);

    expect(card("model.s.a").style.height).toBe(`${NODE_HEIGHT + 2 * COLUMN_ROW_HEIGHT}px`);
    expect(y("model.s.b")).toBe(before + 2 * COLUMN_ROW_HEIGHT);
  });

  it("a column row is a keyboard-reachable button", async () => {
    render(<CanvasHarness nodes={COLUMN_NODES} edges={COLUMN_EDGES} initialExpanded={[FCT]} focusData={COLUMN_FOCUS} />);
    const toggle = await chevron("fct_orders");

    toggle.focus();
    await userEvent.tab();

    expect(row("net_revenue")).toHaveFocus();
    await userEvent.keyboard("{Enter}");
    expect(row("net_revenue")).toHaveAttribute("aria-pressed", "true");
  });

  it("a focused column's node shows only related columns and +k more", async () => {
    render(<CanvasHarness nodes={COLUMN_NODES} edges={COLUMN_EDGES} initial={FCT} initialColumn="net_revenue" focusData={COLUMN_FOCUS} />);

    expect(await screen.findByRole("button", { name: /^net_revenue\b/ })).toHaveAttribute("aria-pressed", "true");

    const fct = within(card(FCT));
    expect(fct.queryByRole("button", { name: /^units\b/ })).not.toBeInTheDocument();
    expect(fct.getByRole("button", { name: "+2 more" })).toBeVisible();
    const stg = within(card(STG));
    expect(stg.getByRole("button", { name: /^amount\b/ })).toHaveAttribute("aria-pressed", "false");
    expect(stg.queryByRole("button", { name: /^quantity\b/ })).not.toBeInTheDocument();
    expect(stg.getByRole("button", { name: "+2 more" })).toBeVisible();
  });

  it("+k more shows the rest of the columns, the related ones still marked", async () => {
    render(<CanvasHarness nodes={COLUMN_NODES} edges={COLUMN_EDGES} initial={FCT} initialColumn="net_revenue" focusData={COLUMN_FOCUS} />);

    await screen.findByRole("button", { name: /^amount\b/ });
    fireEvent.click(within(card(STG)).getByRole("button", { name: "+2 more" }));

    const stg = within(card(STG));
    expect(stg.getAllByRole("button", { name: /^(amount|quantity|order_id)\b/ })).toHaveLength(3);
    expect(stg.queryByRole("button", { name: /more$/ })).not.toBeInTheDocument();
  });

  it("unavailable column lineage disables expanding and says why", async () => {
    render(<CanvasHarness nodes={COLUMN_NODES} edges={COLUMN_EDGES} columnsUnavailable="the compiled SQL is not in the dbt target" />);

    const toggle = await chevron("fct_orders");

    expect(toggle).toBeDisabled();
    expect(toggle).toHaveAccessibleDescription("the compiled SQL is not in the dbt target");
  });

  it("a node without columns has no chevron", async () => {
    render(<CanvasHarness nodes={[node("exposed", { columns: [] })]} edges={[]} />);
    await screen.findByRole("button", { name: /^exposed\b/ });

    expect(screen.queryByRole("button", { name: "Columns of exposed" })).not.toBeInTheDocument();
  });
  it("a focus shows only the related columns of a node the user had opened, and gives the user's expansion back", async () => {
    render(<CanvasHarness nodes={COLUMN_NODES} edges={COLUMN_EDGES} initialExpanded={[FCT]} focusData={COLUMN_FOCUS} />);
    fireEvent.click(await screen.findByRole("button", { name: /^net_revenue\b/ }));

    await waitFor(() => expect(within(card(FCT)).queryByRole("button", { name: /^units\b/ })).not.toBeInTheDocument());
    expect(within(card(FCT)).getByRole("button", { name: "+2 more" })).toBeVisible();

    fireEvent.click(document.querySelector(".react-flow__pane")!);

    expect(await within(card(FCT)).findByRole("button", { name: /^units\b/ })).toBeVisible();
    expect(within(card(FCT)).getByRole("button", { name: /^order_id\b/ })).toBeVisible();
  });

  it("the rest shown during a focus ends with the focus", async () => {
    render(<CanvasHarness nodes={COLUMN_NODES} edges={COLUMN_EDGES} initial={FCT} initialColumn="net_revenue" focusData={COLUMN_FOCUS} />);
    await screen.findByRole("button", { name: /^net_revenue\b/ });
    fireEvent.click(within(card(STG)).getByRole("button", { name: "+2 more" }));
    expect(await within(card(STG)).findByRole("button", { name: /^quantity\b/ })).toBeVisible();

    fireEvent.click(document.querySelector(".react-flow__pane")!);

    await waitFor(() => expect(within(card(STG)).queryByRole("button", { name: /^amount\b/ })).not.toBeInTheDocument());
  });

});
