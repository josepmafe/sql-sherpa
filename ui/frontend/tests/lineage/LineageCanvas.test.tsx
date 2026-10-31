import { MantineProvider } from "@mantine/core";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LineageCanvas } from "../../src/screens/lineage/LineageCanvas";
import { orderNodes } from "../../src/screens/lineage/layout";
import { columnView, type LineageEdge, type LineageNode } from "../fixtures/api";
import { stubLayout } from "../fixtures/render";
import { CanvasHarness, canvasProps, COLUMN_EDGES, COLUMN_FOCUS, COLUMN_NODES, FCT, node, rankInInputOrder } from "./canvasHarness";

// A quick order in place of ELK (the layout tests run the real one), with its calls counted.
vi.mock("../../src/screens/lineage/layout", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/screens/lineage/layout")>()),
  orderNodes: vi.fn(),
}));

// a -> b -> c, and an unrelated x
const NODES = [
  node("a", { layer: "source", resource_type: "source", materialization: null, status: "none" }),
  node("b", { status: "failed", failing_tests: 2 }),
  node("c", { layer: "marts", materialization: "table", status: "warned" }),
  node("x", { status: "not_in_build" }),
];
const EDGES: LineageEdge[] = [
  { source: "model.s.a", target: "model.s.b" },
  { source: "model.s.b", target: "model.s.c" },
];

function Harness({ initial, nodes = NODES, edges = EDGES }: { initial?: string; nodes?: LineageNode[]; edges?: LineageEdge[] }) {
  return <CanvasHarness nodes={nodes} edges={edges} initial={initial} />;
}

// fireEvent, not userEvent.click: user-event's mousedown has no `view`, which d3-zoom (React Flow's pan) reads.
const item = (name: string) => screen.findByRole("button", { name: new RegExp(`^${name}\\b`) });

describe("LineageCanvas", () => {

  // restoreMocks restores spies only: the module-level vi.fn needs its implementation again.
  beforeEach(() => {
    rankInInputOrder();
  });

  it("renders a node per graph node with its status label", async () => {
    render(<Harness />);

    expect(await item("b")).toHaveTextContent("failed");
    expect(await item("c")).toHaveTextContent("warned");
    expect(await item("x")).toHaveTextContent("not in the last build");
    expect(await item("a")).not.toHaveTextContent(/failed|warned|skipped|ok/);
  });

  it("a failed node shows its failing-test count", async () => {
    render(<Harness />);

    expect(await item("b")).toHaveTextContent("2 failing tests");
  });

  it("shows the empty state", () => {
    render(
      <MantineProvider env="test">
        <LineageCanvas {...canvasProps()} />
      </MantineProvider>,
    );

    expect(screen.getByText("No node matches this selector")).toBeInTheDocument();
  });

  it.each([
    [true, false, "ok", "Nothing failed in the last full build"],
    [true, true, "ok", "No failed node in the layers shown"],
    [true, false, "no_build", "No build status to filter on"],
    [true, true, "unavailable", "No build status to filter on"],
    [false, true, "ok", "No node in the layers shown"],
    [false, false, "ok", "No node matches this selector"],
  ] as const)(
    "the empty state names its reason (failing only %s, layers filtered %s, build %s)",
    (failingOnly, layersFiltered, buildState, text) => {
      render(
        <MantineProvider env="test">
          <LineageCanvas {...canvasProps({ failingOnly, layersFiltered, buildState })} />
        </MantineProvider>,
      );

      expect(screen.getByText(text)).toBeInTheDocument();
    },
  );

  it("Enter on a focused node selects it", async () => {
    render(<Harness />);
    const b = await item("b");

    b.focus();
    await userEvent.keyboard("{Enter}");

    expect(b).toHaveAttribute("aria-pressed", "true");
  });

  it("selecting a node dims the unrelated ones and highlights its chain", async () => {
    render(<Harness />);

    fireEvent.click(await item("b"));

    expect(await item("x")).toHaveAttribute("data-dimmed");
    expect(await item("a")).not.toHaveAttribute("data-dimmed");
    expect(await item("c")).not.toHaveAttribute("data-dimmed");
  });

  it("a dimmed node stays focusable and selectable", async () => {
    render(<Harness initial="model.s.b" />);
    const x = await item("x");
    expect(x).toHaveAttribute("data-dimmed");

    x.focus();
    await userEvent.keyboard("{Enter}");

    expect(x).toHaveFocus();
    expect(x).toHaveAttribute("aria-pressed", "true");
    expect(x).not.toHaveAttribute("data-dimmed");
    expect(await item("a")).toHaveAttribute("data-dimmed");
  });

  it("a background click clears the highlight", async () => {
    render(<Harness />);
    fireEvent.click(await item("b"));
    expect(await item("x")).toHaveAttribute("data-dimmed");

    fireEvent.click(document.querySelector(".react-flow__pane")!);
    expect(await item("x")).not.toHaveAttribute("data-dimmed");
  });

  it("a dimmed node dims its chrome, not its text", async () => {
    render(<Harness initial="model.s.b" />);

    const x = await item("x");

    expect(x).toHaveAttribute("data-dimmed");
    expect(x.style.opacity).toBe("");
  });

  it("Tab follows the visual order: column by column, not the order of the API", async () => {
    render(<Harness nodes={[NODES[2]!, NODES[3]!, NODES[1]!, NODES[0]!]} />);
    await item("a");

    const names = [...document.querySelectorAll("button[aria-pressed]")].map((button) => button.querySelector("span")?.textContent);

    expect(names[0]).toBe("a");
    expect(names[3]).toBe("c");
  });

  it("Tab follows the screen order when a layer is split into sub-columns: left to right, then top to bottom", async () => {
    const chain = ["p", "q", "r"].map((name) => node(name));
    const lone = ["s", "t", "u"].map((name) => node(name));
    const edges = [
      { source: "model.s.p", target: "model.s.q" },
      { source: "model.s.q", target: "model.s.r" },
    ];
    render(<Harness nodes={[...chain, ...lone]} edges={edges} />);
    await item("p");

    const corners = [...document.querySelectorAll<HTMLElement>(".react-flow__node")].map((wrapper) => {
      const match = /translate\((-?[\d.]+)px,\s*(-?[\d.]+)px\)/.exec(wrapper.style.transform);
      return [Number(match?.[1]), Number(match?.[2])] as const;
    });
    const sorted = [...corners].sort((a, b) => a[0] - b[0] || a[1] - b[1]);

    expect(new Set(corners.map(([x]) => x)).size).toBeGreaterThan(1);
    expect(corners).toEqual(sorted);
  });

  it("a status-only refetch keeps the layout, so the zoom stays", async () => {
    const view = render(<Harness />);
    await item("x");
    const runs = vi.mocked(orderNodes).mock.calls.length;

    view.rerender(<Harness nodes={NODES.map((node) => (node.name === "x" ? { ...node, status: "ok" as const } : node))} />);

    expect(await screen.findByText("ok")).toBeInTheDocument();
    expect(orderNodes).toHaveBeenCalledTimes(runs);
  });

  it("the minimap appears above the node threshold, whether or not a node is selected, and not below it", async () => {
    const graphNodes = () => document.querySelectorAll("button[aria-pressed]");
    const minimap = () => document.querySelector(".react-flow__minimap");
    const nodesOf = (count: number) => Array.from({ length: count }, (_, index) => node(`n${index}`));

    const large = render(<Harness nodes={nodesOf(61)} edges={[]} />);
    await waitFor(() => expect(graphNodes()).toHaveLength(61), { timeout: 15_000 });
    expect(minimap()).not.toBeNull();
    fireEvent.click(graphNodes()[0]!);
    expect(graphNodes()[0]).toHaveAttribute("aria-pressed", "true");
    expect(minimap()).not.toBeNull();
    large.unmount();

    render(<Harness nodes={nodesOf(60)} edges={[]} />);
    await waitFor(() => expect(graphNodes()).toHaveLength(60), { timeout: 15_000 });
    expect(minimap()).toBeNull();
    fireEvent.click(graphNodes()[0]!);
    expect(graphNodes()[0]).toHaveAttribute("aria-pressed", "true");
    expect(minimap()).toBeNull();
  }, 40_000);

  it("the initial fit never zooms past 1x", async () => {
    // Without the cap, one small node in a 1200x800 canvas is fitted to 2x.
    stubLayout();
    render(<Harness nodes={[NODES[1]!]} edges={[]} />);
    await item("b");
    const viewport = () => (document.querySelector(".react-flow__viewport") as HTMLElement).style.transform;

    // The fit moves the viewport off its origin.
    await waitFor(() => expect(viewport()).not.toMatch(/translate\(0px,0px\)/));

    expect(zoomOf(viewport())).toBeLessThanOrEqual(1);
  });

  it("node= in the URL restores the highlight after a reload", async () => {
    render(<Harness initial="model.s.b" />);

    expect(await item("x")).toHaveAttribute("data-dimmed");
    expect(await item("b")).toHaveAttribute("aria-pressed", "true");
  });

  describe("column focus", () => {
    const other = node("other", { columns: [columnView({ name: "k" })] });
    const nodes = [...COLUMN_NODES, other];
    const mount = () =>
      render(<CanvasHarness nodes={nodes} edges={COLUMN_EDGES} initialExpanded={[FCT]} focusData={COLUMN_FOCUS} />);

    it("clicking a column focuses it and dims the unrelated nodes", async () => {
      mount();
      expect(await item("other")).not.toHaveAttribute("data-dimmed");

      fireEvent.click(await screen.findByRole("button", { name: /^net_revenue\b/ }));

      expect(await item("other")).toHaveAttribute("data-dimmed");
      expect(await item("stg_orders")).not.toHaveAttribute("data-dimmed");
      expect(await item("fct_orders")).not.toHaveAttribute("data-dimmed");
      expect(await screen.findByRole("button", { name: /^amount\b/ })).toBeVisible();
      expect(screen.getByRole("button", { name: /^net_revenue\b/ })).toHaveAttribute("aria-pressed", "true");
    });

    it("a dimmed node's column rows stay focusable", async () => {
      render(<CanvasHarness nodes={nodes} edges={COLUMN_EDGES} initialExpanded={["model.s.other"]} initial={FCT} initialColumn="net_revenue" focusData={COLUMN_FOCUS} />);
      const row = await screen.findByRole("button", { name: /^k\b/ });

      row.focus();

      expect(row).toHaveFocus();
      expect(await item("other")).toHaveAttribute("data-dimmed");
    });

    it("draws the column edge in the primary colour and fades the table edges", async () => {
      stubLayout();
      mount();
      await waitFor(() => expect(document.querySelectorAll(".react-flow__edge")).toHaveLength(1));
      const tableEdge = () => document.querySelector<SVGPathElement>(".react-flow__edge-path")!;
      expect(tableEdge().style.opacity).toBe("1");

      fireEvent.click(await screen.findByRole("button", { name: /^net_revenue\b/ }));

      await waitFor(() => expect(document.querySelectorAll(".react-flow__edge")).toHaveLength(2));
      const strokes = [...document.querySelectorAll<SVGPathElement>(".react-flow__edge-path")].map((path) => [
        path.style.stroke,
        path.style.opacity,
      ]);
      expect(strokes).toContainEqual(["var(--mantine-primary-color-filled)", "1"]);
      expect(strokes.filter(([, opacity]) => opacity === "0.15")).toHaveLength(1);
    });

    it("the legend says what column lineage follows, while columns are shown", async () => {
      mount();
      await item("fct_orders");
      const legend = /Column lineage follows the select list: columns used only in joins or filters are not linked/;
      expect(screen.getByText(legend)).toBeVisible();
    });

    it("no legend while every node is collapsed", async () => {
      render(<CanvasHarness nodes={nodes} edges={COLUMN_EDGES} />);
      await item("fct_orders");

      expect(screen.queryByText(/Column lineage follows the select list/)).not.toBeInTheDocument();
    });
  });
  describe("column focus dimming", () => {
    it("a node in the selection's table chain that holds no related column dims during a column focus", async () => {
      const upstream = node("raw_orders", { columns: [columnView({ name: "id" })] });
      const nodes = [upstream, ...COLUMN_NODES];
      const edges = [{ source: upstream.unique_id, target: "model.s.stg_orders" }, ...COLUMN_EDGES];
      render(<CanvasHarness nodes={nodes} edges={edges} initial={FCT} />);
      expect(await item("raw_orders")).not.toHaveAttribute("data-dimmed");
      cleanup();

      render(<CanvasHarness nodes={nodes} edges={edges} initial={FCT} initialColumn="net_revenue" focusData={COLUMN_FOCUS} />);

      expect(await item("raw_orders")).toHaveAttribute("data-dimmed");
      expect(await item("stg_orders")).not.toHaveAttribute("data-dimmed");
    });
  });

});

function zoomOf(transform: string): number {
  const scale = /scale\(([\d.]+)\)/.exec(transform)?.[1];
  if (scale === undefined) throw new Error(`no zoom in the transform "${transform}"`);
  return Number(scale);
}
