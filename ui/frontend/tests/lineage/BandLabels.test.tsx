import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { LineageCanvas } from "../../src/screens/lineage/LineageCanvas";
import { canvasProps, node } from "./canvasHarness";

// A quick order in place of ELK: the ranks follow the input.
vi.mock("../../src/screens/lineage/layout", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/screens/lineage/layout")>()),
  orderNodes: (nodes: { id: string }[]) => Promise.resolve(new Map(nodes.map((n, index) => [n.id, index]))),
}));

describe("BandLabels", () => {
  it("labels only the bands shown", async () => {
    render(
      <MantineProvider env="test">
        <LineageCanvas
          {...canvasProps({
            nodes: [node("stg_a", { layer: "staging" }), node("fct_b", { layer: "marts" })],
            edges: [{ source: "model.s.stg_a", target: "model.s.fct_b" }],
          })}
        />
      </MantineProvider>,
    );

    await waitFor(() => expect(screen.getByText("Staging")).toBeInTheDocument());

    expect(screen.getByText("Marts")).toBeInTheDocument();
    for (const absent of ["Raw", "Seeds", "Snapshots", "Intermediate", "Exposures"]) {
      expect(screen.queryByText(absent)).not.toBeInTheDocument();
    }
    // A label is no graph node: no button, not focusable.
    expect(document.querySelectorAll("button[aria-pressed]")).toHaveLength(2);
    expect(screen.getByText("Staging").closest("button")).toBeNull();
    expect(screen.getByText("Staging").closest(".react-flow__node")).not.toHaveAttribute("tabindex");
  });
});
