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
    ["unavailable", "Build status unavailable: the database is down"],
  ] as const)("a %s build shows its note", (buildState, note) => {
    renderFilter({ buildState });

    expect(screen.getByText(note)).toBeInTheDocument();
  });

  it("a recorded build shows no note", () => {
    renderFilter();

    expect(screen.queryByText(/No full build|Build status unavailable/)).not.toBeInTheDocument();
  });
});
