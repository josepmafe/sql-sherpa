import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiRequestError } from "../../src/api/queries";
import { SelectorBar } from "../../src/screens/lineage/SelectorBar";

const WHOLE_PROJECT = {
  build: { state: "ok", node_count: 3, started_at: null },
  edges: [],
  layers_available: ["staging", "marts"],
  nodes: [
    { unique_id: "model.sherpa.stg_orders", name: "stg_orders", resource_type: "model", layer: "staging", domain: null },
    { unique_id: "model.sherpa.fct_orders", name: "fct_orders", resource_type: "model", layer: "marts", domain: "sales" },
    {
      unique_id: "model.sherpa.dim_products",
      name: "dim_products",
      resource_type: "model",
      layer: "marts",
      domain: "product",
    },
  ],
};
vi.mock("../../src/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/api/queries")>()),
  useLineage: () => ({ data: WHOLE_PROJECT }),
}));

function renderBar(select: string, error: Error | null = null) {
  const onSelect = vi.fn();
  render(
    <MantineProvider env="test">
      <SelectorBar select={select} onSelect={onSelect} error={error} />
    </MantineProvider>,
  );
  return onSelect;
}

describe("SelectorBar", () => {
  beforeEach(() => vi.clearAllMocks());

  it("switching the mode writes the selector to the URL", async () => {
    const onSelect = renderBar("+fct_orders+");

    await userEvent.click(screen.getByRole("radio", { name: "Domain" }));
    expect(onSelect).toHaveBeenLastCalledWith("+config.meta.domain:product");

    await userEvent.click(screen.getByRole("radio", { name: "Whole project" }));
    expect(onSelect).toHaveBeenLastCalledWith("");
  });

  it("choosing a depth in the path mode writes the depth operators", async () => {
    const onSelect = renderBar("+fct_orders+");

    await userEvent.click(screen.getByRole("combobox", { name: "Downstream" }));
    await userEvent.click(await screen.findByRole("option", { name: "2" }));

    expect(onSelect).toHaveBeenLastCalledWith("+fct_orders+2");
  });

  it("the selector box shows the real dbt selector", () => {
    renderBar("+config.meta.domain:sales");

    expect(screen.getByRole("textbox", { name: "dbt selector" })).toHaveValue("+config.meta.domain:sales");
  });

  it("typing a selector and pressing Enter applies it", async () => {
    const onSelect = renderBar("");

    await userEvent.type(screen.getByRole("textbox", { name: "dbt selector" }), "+fct_orders+ +dim_products{Enter}");

    expect(onSelect).toHaveBeenCalledWith("+fct_orders+ +dim_products");
  });

  it("an unsupported selector shows the supported forms", () => {
    const message = "unsupported selector 'tag:x'; supported selectors: a model name, source:<source>";
    renderBar("tag:x", new ApiRequestError({ code: "unsupported_selector", message }, 422));

    expect(screen.getByRole("alert")).toHaveTextContent(message);
  });
});
