import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LayerFilter } from "../../src/screens/lineage/LayerFilter";

function renderFilter(props: Partial<Parameters<typeof LayerFilter>[0]> = {}) {
  const onLayers = vi.fn();
  const onFailingOnly = vi.fn();
  render(
    <MantineProvider>
      <LayerFilter
        available={["staging", "marts"]}
        layers={undefined}
        onLayers={onLayers}
        failingOnly={false}
        onFailingOnly={onFailingOnly}
        buildState="ok"
        startedAt={null}
        {...props}
      />
    </MantineProvider>,
  );
  return { onLayers, onFailingOnly };
}

describe("LayerFilter", () => {
  it("shows only layers in the graph", () => {
    renderFilter();

    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
    expect(screen.getByRole("checkbox", { name: "staging" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "marts" })).toBeChecked();
    expect(screen.queryByRole("checkbox", { name: "seed" })).not.toBeInTheDocument();
  });

  it("hiding a layer writes the layers still shown", async () => {
    const { onLayers } = renderFilter();

    await userEvent.click(screen.getByRole("checkbox", { name: "staging" }));

    expect(onLayers).toHaveBeenCalledWith(["marts"]);
  });

  it("showing every layer again clears the filter", async () => {
    const { onLayers } = renderFilter({ layers: ["marts"] });

    await userEvent.click(screen.getByRole("checkbox", { name: "staging" }));

    expect(onLayers).toHaveBeenCalledWith(undefined);
  });

  it("the failing only switch reports its state", async () => {
    const { onFailingOnly } = renderFilter();

    await userEvent.click(screen.getByRole("switch", { name: "Failing only" }));

    expect(onFailingOnly).toHaveBeenCalledWith(true);
  });

  it.each([
    ["no_build", "No full build recorded: run make build-prod"],
    ["unavailable", "Build status unavailable (could not read the build record; see the ui log)"],
  ] as const)("a %s build shows its note", (buildState, note) => {
    renderFilter({ buildState });

    expect(screen.getByText(note)).toBeInTheDocument();
  });

  it("a recorded build says which one the status comes from", () => {
    renderFilter({ startedAt: "2026-10-26T14:05:09Z" });

    expect(screen.getByText("Status from the full build of 2026-10-26 14:05 UTC")).toBeInTheDocument();
    expect(screen.queryByText(/No full build|Build status unavailable/)).not.toBeInTheDocument();
  });

  it("unticking the last layer shown is ignored", async () => {
    const { onLayers } = renderFilter({ layers: ["marts"] });

    await userEvent.click(screen.getByRole("checkbox", { name: "marts" }));

    expect(onLayers).not.toHaveBeenCalled();
  });
});
