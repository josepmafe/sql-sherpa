import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LineageScreen } from "../../src/screens/lineage/LineageScreen";

const get = vi.fn();
vi.mock("../../src/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/api/client")>()),
  api: { GET: (...args: unknown[]) => get(...args) },
}));

const GRAPH = {
  data: {
    build: { state: "ok", node_count: 2, started_at: null },
    edges: [],
    layers_available: ["staging", "marts"],
    nodes: [
      { unique_id: "model.s.a", name: "a", resource_type: "model", layer: "staging", domain: null },
      { unique_id: "model.s.b", name: "b", resource_type: "model", layer: "marts", domain: "sales" },
    ],
  },
  response: { status: 200 },
};
const MESSAGE = "unsupported selector 'tag:x'; supported selectors: a model name";

describe("LineageScreen", () => {
  // A block body: a function returned from beforeEach would run as the teardown.
  beforeEach(() => {
    get.mockReset();
  });

  it("an unsupported selector keeps the previous graph and shows the supported forms", async () => {
    get.mockImplementation((...args: unknown[]) =>
      Promise.resolve(
        (args[1] as { params: { query: { select: string } } }).params.query.select === "tag:x"
          ? { error: { code: "unsupported_selector", message: MESSAGE }, response: { status: 422 } }
          : GRAPH,
      ),
    );
    render(
      <MantineProvider env="test">
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter initialEntries={["/lineage"]}>
            <LineageScreen />
          </MemoryRouter>
        </QueryClientProvider>
      </MantineProvider>,
    );
    expect(await screen.findByText("2 nodes")).toBeInTheDocument();

    await userEvent.type(screen.getByRole("textbox", { name: "dbt selector" }), "tag:x{Enter}");

    expect(await screen.findByRole("alert")).toHaveTextContent(MESSAGE);
    expect(screen.getByText("2 nodes")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "marts" })).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(document.querySelector(".mantine-Loader-root")).toBeNull();
  });
});
