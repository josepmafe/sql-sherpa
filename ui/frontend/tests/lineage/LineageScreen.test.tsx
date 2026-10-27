import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LineageScreen } from "../../src/screens/lineage/LineageScreen";

// No test here reads a position, and the real ELK layout is slow under load.
vi.mock("../../src/screens/lineage/layout", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/screens/lineage/layout")>()),
  layoutGraph: (nodes: { id: string }[]) =>
    Promise.resolve(new Map(nodes.map((node, index) => [node.id, { x: index * 300, y: 0 }]))),
}));

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
const NODE = {
  unique_id: "model.s.a",
  name: "a",
  resource_type: "model",
  layer: "staging",
  materialization: "view",
  domain: null,
  contract_enforced: false,
  file_path: "models/a.sql",
  description: "",
  is_mart: false,
  status: "ok",
  failing_tests: 0,
  build: null,
  columns: [],
  tests: [],
  raw_sql: null,
  compiled_sql: null,
  sql_note: null,
};
// The graph, and an empty node panel for the node asked for.
const answer = (path: string) =>
  Promise.resolve(path === "/api/lineage" ? GRAPH : { data: NODE, response: { status: 200 } });
const MESSAGE = "unsupported selector 'tag:x'; supported selectors: a model name";

function Where() {
  return <output aria-label="location">{useLocation().search}</output>;
}

function Go({ to }: { to: string }) {
  const navigate = useNavigate();
  return <button onClick={() => void navigate(to)}>go</button>;
}

function mount(path: string, extra?: React.ReactNode) {
  render(
    <MantineProvider env="test">
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={[path]}>
          <LineageScreen />
          <Where />
          {extra}
        </MemoryRouter>
      </QueryClientProvider>
    </MantineProvider>,
  );
}

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

  it("a node= that the graph does not hold clears itself", async () => {
    get.mockImplementation(answer);

    mount("/lineage?node=model.s.gone&failing=1");

    await waitFor(() => expect(screen.getByLabelText("location")).toHaveTextContent("?failing=1"));
    expect(screen.getByLabelText("location")).not.toHaveTextContent("node=");
  });

  it("a node= opens its panel, and Esc closes the panel and clears node=", async () => {
    get.mockImplementation((path: string) =>
      Promise.resolve(
        path === "/api/lineage"
          ? GRAPH
          : { data: { ...NODE, unique_id: "model.s.a", name: "a", description: "Model a." }, response: { status: 200 } },
      ),
    );
    mount("/lineage?node=model.s.a");

    expect(await screen.findByText("Model a.")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");

    await waitFor(() => expect(screen.queryByText("Model a.")).not.toBeInTheDocument());
    expect(screen.getByLabelText("location")).not.toHaveTextContent("node=");
  });

  it.each([
    ["Esc", () => userEvent.keyboard("{Escape}")],
    ["the close button", () => userEvent.click(screen.getByRole("button", { name: "Close the node panel" }))],
  ])("closing the panel with %s returns the focus to the node", async (_, close) => {
    get.mockImplementation(answer);
    mount("/lineage?node=model.s.a");
    expect(await screen.findByRole("complementary", { name: "Node details" })).toBeInTheDocument();

    await close();

    await waitFor(() => expect(screen.queryByRole("complementary", { name: "Node details" })).not.toBeInTheDocument());
    expect(document.querySelector('[data-id="model.s.a"] button')).toHaveFocus();
  });

  it("a node= that only the next graph holds survives the previous graph standing in", async () => {
    // The graph for s2 is slow; until it arrives the graph for s1 stays, and it does not hold model.s.b.
    const slow: { resolve?: (value: unknown) => void } = {};
    const only = (id: string) => ({
      ...GRAPH,
      data: { ...GRAPH.data, nodes: GRAPH.data.nodes.filter((node) => node.unique_id === id) },
    });
    get.mockImplementation((path: string, options: { params: { query: { select: string } } }) => {
      if (path !== "/api/lineage") return Promise.resolve({ data: { ...NODE, unique_id: "model.s.b", name: "b" }, response: { status: 200 } });
      if (options.params.query.select === "s1") return Promise.resolve(only("model.s.a"));
      return new Promise((resolve) => (slow.resolve = resolve));
    });
    mount("/lineage?select=s1", <Go to="/lineage?select=s2&node=model.s.b" />);
    expect(await screen.findByText("1 nodes")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "go" }));
    await waitFor(() => expect(slow.resolve).toBeDefined());
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(screen.getByLabelText("location")).toHaveTextContent("node=model.s.b");
    slow.resolve?.(only("model.s.b"));
    expect(await screen.findByText("1 nodes")).toBeInTheDocument();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.getByLabelText("location")).toHaveTextContent("node=model.s.b");
  });

  it("a node_not_found keeps the graph and shows a note", async () => {
    get.mockImplementation((path: string) =>
      Promise.resolve(
        path === "/api/lineage"
          ? GRAPH
          : { error: { code: "node_not_found", message: "no node model.s.a" }, response: { status: 404 } },
      ),
    );
    mount("/lineage?node=model.s.a");

    expect(await screen.findByRole("alert")).toHaveTextContent("no node model.s.a");
    expect(screen.getByText("2 nodes")).toBeInTheDocument();
    expect(screen.getByLabelText("Lineage graph")).toBeInTheDocument();
  });

  it("a node= that the graph holds stays", async () => {
    get.mockImplementation(answer);

    mount("/lineage?node=model.s.a");

    expect(await screen.findByText("2 nodes")).toBeInTheDocument();
    expect(screen.getByLabelText("location")).toHaveTextContent("node=model.s.a");
  });

  it("a load error hides the graph instead of showing a stale one", async () => {
    get.mockImplementation((...args: unknown[]) =>
      Promise.resolve(
        (args[1] as { params: { query: { layers?: string } } }).params.query.layers === undefined
          ? GRAPH
          : { error: { code: "internal", message: "boom" }, response: { status: 500 } },
      ),
    );
    mount("/lineage");
    expect(await screen.findByText("2 nodes")).toBeInTheDocument();
    expect(screen.getByLabelText("Lineage graph")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("checkbox", { name: "marts" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    expect(screen.queryByText(/\d+ nodes/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Lineage graph")).not.toBeInTheDocument();
  });
});
