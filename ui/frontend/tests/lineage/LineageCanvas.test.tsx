import { MantineProvider } from "@mantine/core";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import type { components } from "../../src/api/schema";
import { LineageCanvas } from "../../src/screens/lineage/LineageCanvas";

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

function Harness({ initial }: { initial?: string }) {
  const [selected, setSelected] = useState(initial);
  return (
    <MantineProvider env="test">
      <LineageCanvas nodes={NODES} edges={EDGES} selected={selected} onSelect={setSelected} />
    </MantineProvider>
  );
}

// fireEvent, not userEvent.click: user-event's mousedown has no `view`, which d3-zoom (React Flow's pan) reads.
const item = (name: string) => screen.findByRole("button", { name: new RegExp(`^${name}\\b`) });

describe("LineageCanvas", () => {
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
        <LineageCanvas nodes={[]} edges={[]} selected={undefined} onSelect={() => {}} />
      </MantineProvider>,
    );

    expect(screen.getByText("No node matches this selector")).toBeInTheDocument();
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
    // a dimmed node stays focusable
    expect(await item("x")).not.toHaveAttribute("tabindex", "-1");
  });

  it("Esc and a background click clear the highlight", async () => {
    render(<Harness />);
    fireEvent.click(await item("b"));
    expect(await item("x")).toHaveAttribute("data-dimmed");

    await userEvent.keyboard("{Escape}");
    expect(await item("x")).not.toHaveAttribute("data-dimmed");

    fireEvent.click(await item("b"));
    expect(await item("x")).toHaveAttribute("data-dimmed");
    fireEvent.click(document.querySelector(".react-flow__pane")!);
    expect(await item("x")).not.toHaveAttribute("data-dimmed");
  });

  it("node= in the URL restores the highlight after a reload", async () => {
    render(<Harness initial="model.s.b" />);

    expect(await item("x")).toHaveAttribute("data-dimmed");
    expect(await item("b")).toHaveAttribute("aria-pressed", "true");
  });
});
