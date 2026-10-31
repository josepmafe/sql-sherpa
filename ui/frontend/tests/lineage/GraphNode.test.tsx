import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { COLUMN_ROW_HEIGHT, NODE_HEIGHT } from "../../src/screens/lineage/layout";

import { CanvasHarness, COLUMN_EDGES, COLUMN_FOCUS, COLUMN_NODES, FCT, node, rankInInputOrder, STG } from "./canvasHarness";
import { columnFocusView, columnView, focusColumn } from "../fixtures/api";

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
    expect(fct.getByRole("button", { name: /^Show 2 more columns of / })).toBeVisible();
    const stg = within(card(STG));
    expect(stg.getByRole("button", { name: /^amount\b/ })).toHaveAttribute("aria-pressed", "false");
    expect(stg.queryByRole("button", { name: /^quantity\b/ })).not.toBeInTheDocument();
    expect(stg.getByRole("button", { name: /^Show 2 more columns of / })).toBeVisible();
  });

  it("+k more shows the rest of the columns, the related ones still marked", async () => {
    render(<CanvasHarness nodes={COLUMN_NODES} edges={COLUMN_EDGES} initial={FCT} initialColumn="net_revenue" focusData={COLUMN_FOCUS} />);

    await screen.findByRole("button", { name: /^amount\b/ });
    fireEvent.click(within(card(STG)).getByRole("button", { name: /^Show 2 more columns of / }));

    const stg = within(card(STG));
    expect(stg.getAllByRole("button", { name: /^(amount|quantity|order_id)\b/ })).toHaveLength(3);
    expect(stg.getByRole("button", { name: "Hide the other 2 columns of stg_orders" })).toHaveAttribute("aria-expanded", "true");
  });

  it("a focused node grows by its related rows and the +k more row", async () => {
    render(<CanvasHarness nodes={COLUMN_NODES} edges={COLUMN_EDGES} initial={FCT} initialColumn="net_revenue" focusData={COLUMN_FOCUS} />);

    await screen.findByRole("button", { name: /^amount\b/ });

    expect(card(STG).style.height).toBe(`${NODE_HEIGHT + 2 * COLUMN_ROW_HEIGHT}px`);
  });

  it("only the focused node's row is marked, not a same-named column of another node", async () => {
    const focus = columnFocusView({
      focus: { node: FCT, column: "order_id" },
      columns: [
        focusColumn({ node: FCT, name: "order_id", direction: "focus" }),
        focusColumn({ node: STG, name: "order_id", direction: "upstream" }),
      ],
      edges: [{ parent: { node: STG, column: "order_id" }, child: { node: FCT, column: "order_id" } }],
    });
    render(<CanvasHarness nodes={COLUMN_NODES} edges={COLUMN_EDGES} initial={FCT} initialColumn="order_id" focusData={focus} />);
    await waitFor(() => expect(screen.getAllByRole("button", { name: /^order_id\b/ })).toHaveLength(2));

    expect(within(card(FCT)).getByRole("button", { name: /^order_id\b/ })).toHaveAttribute("aria-pressed", "true");
    expect(within(card(STG)).getByRole("button", { name: /^order_id\b/ })).toHaveAttribute("aria-pressed", "false");
  });

  it("during a focus a reached node's chevron says whether the rest is shown", async () => {
    render(<CanvasHarness nodes={COLUMN_NODES} edges={COLUMN_EDGES} initial={FCT} initialColumn="net_revenue" focusData={COLUMN_FOCUS} />);
    await screen.findByRole("button", { name: /^amount\b/ });
    const show = await within(card(STG)).findByRole("button", { name: "Show all columns of stg_orders" });
    expect(show).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(show);

    const hide = await within(card(STG)).findByRole("button", { name: "Show related columns only of stg_orders" });
    expect(hide).toHaveAttribute("aria-expanded", "true");
    expect(within(card(FCT)).getByRole("button", { name: "Show all columns of fct_orders" })).toHaveAttribute("aria-expanded", "false");
  });

  it("+k more names its node and says it is collapsed", async () => {
    render(<CanvasHarness nodes={COLUMN_NODES} edges={COLUMN_EDGES} initial={FCT} initialColumn="net_revenue" focusData={COLUMN_FOCUS} />);
    await screen.findByRole("button", { name: /^amount\b/ });

    const more = await within(card(STG)).findByRole("button", { name: "Show 2 more columns of stg_orders" });

    expect(more).toHaveAttribute("aria-expanded", "false");
    expect(more).toHaveTextContent("+2 more");
  });

  it("Enter on +k more keeps the keyboard focus on a button of the node", async () => {
    render(<CanvasHarness nodes={COLUMN_NODES} edges={COLUMN_EDGES} initial={FCT} initialColumn="net_revenue" focusData={COLUMN_FOCUS} />);
    await screen.findByRole("button", { name: /^amount\b/ });
    const more = await within(card(STG)).findByRole("button", { name: /^Show 2 more columns of / });
    more.focus();

    await userEvent.keyboard("{Enter}");

    expect(await within(card(STG)).findByRole("button", { name: /^quantity\b/ })).toBeVisible();
    expect(document.activeElement).toBeInstanceOf(HTMLButtonElement);
    expect(card(STG)).toContainElement(document.activeElement as HTMLElement);
  });

  it("during a focus the chevron of a node whose columns are all related is disabled", async () => {
    const nodes = [node("up", { columns: [columnView({ name: "k" })] }), node("down", { columns: [columnView({ name: "k" }), columnView({ name: "v" })] })];
    const focus = columnFocusView({
      focus: { node: "model.s.down", column: "k" },
      columns: [
        focusColumn({ node: "model.s.down", name: "k", direction: "focus" }),
        focusColumn({ node: "model.s.up", name: "k", direction: "upstream" }),
      ],
      edges: [{ parent: { node: "model.s.up", column: "k" }, child: { node: "model.s.down", column: "k" } }],
    });
    render(<CanvasHarness nodes={nodes} edges={[{ source: "model.s.up", target: "model.s.down" }]} initial="model.s.down" initialColumn="k" focusData={focus} />);
    await waitFor(() => expect(screen.getAllByRole("button", { name: /^k\b/ })).toHaveLength(2));

    expect(await within(card("model.s.up")).findByRole("button", { name: "Show all columns of up" })).toBeDisabled();
    expect(within(card("model.s.down")).getByRole("button", { name: "Show all columns of down" })).toBeEnabled();
    expect(within(card("model.s.up")).queryByRole("button", { name: /more column/ })).not.toBeInTheDocument();
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
    expect(within(card(FCT)).getByRole("button", { name: /^Show 2 more columns of / })).toBeVisible();

    fireEvent.click(document.querySelector(".react-flow__pane")!);

    expect(await within(card(FCT)).findByRole("button", { name: /^units\b/ })).toBeVisible();
    expect(within(card(FCT)).getByRole("button", { name: /^order_id\b/ })).toBeVisible();
  });

  it("the rest shown during a focus ends with the focus", async () => {
    render(<CanvasHarness nodes={COLUMN_NODES} edges={COLUMN_EDGES} initial={FCT} initialColumn="net_revenue" focusData={COLUMN_FOCUS} />);
    await screen.findByRole("button", { name: /^net_revenue\b/ });
    fireEvent.click(within(card(STG)).getByRole("button", { name: /^Show 2 more columns of / }));
    expect(await within(card(STG)).findByRole("button", { name: /^quantity\b/ })).toBeVisible();

    fireEvent.click(document.querySelector(".react-flow__pane")!);

    await waitFor(() => expect(within(card(STG)).queryByRole("button", { name: /^amount\b/ })).not.toBeInTheDocument());
  });
});
