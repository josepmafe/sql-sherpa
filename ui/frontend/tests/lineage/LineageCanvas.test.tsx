import { MantineProvider } from "@mantine/core";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeAll, describe, expect, it, vi } from "vitest";

import type { components } from "../../src/api/schema";
import { LineageCanvas } from "../../src/screens/lineage/LineageCanvas";
import { layoutGraph } from "../../src/screens/lineage/layout";

// The real layout, with its calls counted.
vi.mock("../../src/screens/lineage/layout", async (importOriginal) => {
  const original = await importOriginal<typeof import("../../src/screens/lineage/layout")>();
  return { ...original, layoutGraph: vi.fn(original.layoutGraph) };
});

type Node = components["schemas"]["LineageNode"];

function node(name: string, patch: Partial<Node> = {}): Node {
  return {
    unique_id: `model.s.${name}`,
    name,
    resource_type: "model",
    layer: "staging",
    domain: null,
    materialization: "view",
    status: "ok",
    failing_tests: 0,
    ...patch,
  };
}

// a -> b -> c, and an unrelated x
const NODES = [
  node("a", { layer: "source", resource_type: "source", materialization: null, status: "none" }),
  node("b", { status: "failed", failing_tests: 2 }),
  node("c", { layer: "marts", materialization: "table", status: "warned" }),
  node("x", { status: "not_in_build" }),
];
const EDGES = [
  { source: "model.s.a", target: "model.s.b" },
  { source: "model.s.b", target: "model.s.c" },
];

function Harness({ initial, nodes = NODES, edges = EDGES }: { initial?: string; nodes?: Node[]; edges?: typeof EDGES }) {
  const [selected, setSelected] = useState(initial);
  return (
    <MantineProvider env="test">
      <LineageCanvas nodes={nodes} edges={edges} selected={selected} onSelect={setSelected} failingOnly={false} layersFiltered={false} />
    </MantineProvider>
  );
}

// fireEvent, not userEvent.click: user-event's mousedown has no `view`, which d3-zoom (React Flow's pan) reads.
const item = (name: string) => screen.findByRole("button", { name: new RegExp(`^${name}\\b`) });

describe("LineageCanvas", () => {
  // The first ELK load takes longer than findBy's default timeout under load; pay it once here.
  beforeAll(async () => {
    await layoutGraph([{ id: "warm", layer: "marts" }], []);
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
        <LineageCanvas nodes={[]} edges={[]} selected={undefined} onSelect={() => {}} failingOnly={false} layersFiltered={false} />
      </MantineProvider>,
    );

    expect(screen.getByText("No node matches this selector")).toBeInTheDocument();
  });

  it.each([
    [true, false, "Nothing failed in the last full build"],
    [true, true, "Nothing failed in the last full build"],
    [false, true, "No node in the layers shown"],
  ])("the empty state names its reason (failing only %s, layers filtered %s)", (failingOnly, layersFiltered, text) => {
    render(
      <MantineProvider env="test">
        <LineageCanvas
          nodes={[]}
          edges={[]}
          selected={undefined}
          onSelect={() => {}}
          failingOnly={failingOnly}
          layersFiltered={layersFiltered}
        />
      </MantineProvider>,
    );

    expect(screen.getByText(text)).toBeInTheDocument();
  });

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

  it("a status-only refetch keeps the layout, so the zoom stays", async () => {
    const view = render(<Harness />);
    await item("x");
    const runs = vi.mocked(layoutGraph).mock.calls.length;

    view.rerender(<Harness nodes={NODES.map((node) => (node.name === "x" ? { ...node, status: "ok" as const } : node))} />);

    expect(await screen.findByText("ok")).toBeInTheDocument();
    expect(layoutGraph).toHaveBeenCalledTimes(runs);
  });

  it("the minimap of a large graph is hidden while the panel is open", async () => {
    const many = Array.from({ length: 61 }, (_, index) => node(`n${index}`));
    render(<Harness nodes={many} edges={[]} />);
    const graphNodes = () => document.querySelectorAll("button[aria-pressed]");
    await waitFor(() => expect(graphNodes()).toHaveLength(61), { timeout: 15_000 });
    const first = graphNodes()[0]!;
    expect(document.querySelector(".react-flow__minimap")).not.toBeNull();

    fireEvent.click(first);

    expect(document.querySelector(".react-flow__minimap")).toBeNull();
  }, 20_000);

  it("node= in the URL restores the highlight after a reload", async () => {
    render(<Harness initial="model.s.b" />);

    expect(await item("x")).toHaveAttribute("data-dimmed");
    expect(await item("b")).toHaveAttribute("aria-pressed", "true");
  });
});
