import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LineageScreen } from "../../src/screens/lineage/LineageScreen";
import { apiFailure, type LineageGraph, lineageGraph, lineageNode, nodeDetails, ok } from "../fixtures/api";
import { deferred, renderWithProviders } from "../fixtures/render";

// No test here reads a position, and the real ELK layout is slow under load.
vi.mock("../../src/screens/lineage/layout", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/screens/lineage/layout")>()),
  orderNodes: (nodes: { id: string }[]) => Promise.resolve(new Map(nodes.map((node, index) => [node.id, index]))),
}));

type Options = { params: { query?: { select?: string; layers?: string }; path?: { unique_id: string } } };
const get = vi.fn<(path: string, options: Options) => Promise<unknown>>();
vi.mock("../../src/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/api/client")>()),
  api: { GET: (path: string, options: Options) => get(path, options) },
}));

const GRAPH = lineageGraph({
  build: { state: "ok", node_count: 2, started_at: null },
  layers_available: ["staging", "marts"],
  nodes: [
    lineageNode({
      unique_id: "model.s.a",
      name: "a",
      layer: "staging",
      domain: null,
      materialization: "view",
    }),
    lineageNode({ unique_id: "model.s.b", name: "b", layer: "marts", domain: "sales" }),
  ],
});
const DETAILS = (uniqueId: string) =>
  nodeDetails({
    unique_id: uniqueId,
    name: uniqueId.split(".").pop() ?? "",
    description: `Model ${uniqueId.split(".").pop()}.`,
  });
const LINEAGE = "/api/lineage";
const MESSAGE = "unsupported selector 'tag:x'; supported selectors: a model name";
const EMPTY_STATE = "Select a node to see its SQL, columns and tests";

// The graph, and the panel of the node asked for (its id is in the request's path params).
function answerNodes(graph: LineageGraph = GRAPH) {
  return (path: string, options: Options) =>
    path === LINEAGE ? Promise.resolve(ok(graph)) : Promise.resolve(ok(DETAILS(options.params.path?.unique_id ?? "")));
}

function Where() {
  return <output aria-label="location">{useLocation().search}</output>;
}

function Go({ to }: { to: string }) {
  const navigate = useNavigate();
  return <button onClick={() => void navigate(to)}>go</button>;
}

function mount(path: string, extra?: React.ReactNode) {
  renderWithProviders(
    <>
      <LineageScreen />
      <Where />
      {extra}
    </>,
    { path },
  );
}

const nodeButton = (name: string) => screen.findByRole("button", { name: new RegExp(`^${name}\\b`) });
const panel = () => screen.getByRole("complementary", { name: "Node details" });

describe("LineageScreen", () => {
  // restoreMocks restores spies only: a module-level vi.fn keeps its calls and implementation.
  beforeEach(() => {
    get.mockReset();
  });

  it("an unsupported selector keeps the previous graph and shows the supported forms", async () => {
    get.mockImplementation((_, options) =>
      Promise.resolve(
        options.params.query?.select === "tag:x" ? apiFailure("unsupported_selector", MESSAGE, 422) : ok(GRAPH),
      ),
    );
    mount("/lineage");
    expect(await screen.findByText("2 nodes")).toBeInTheDocument();

    await userEvent.type(screen.getByRole("textbox", { name: "dbt selector" }), "tag:x{Enter}");

    expect(await screen.findByRole("alert")).toHaveTextContent(MESSAGE);
    expect(screen.getByText("2 nodes")).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "marts" })).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    expect(document.querySelector(".mantine-Loader-root")).toBeNull();
  });

  it("a node= that the graph does not hold clears itself", async () => {
    get.mockImplementation(answerNodes());

    mount("/lineage?node=model.s.gone&failing=1");

    await waitFor(() => expect(screen.getByLabelText("location")).toHaveTextContent("?failing=1"));
    expect(screen.getByLabelText("location")).not.toHaveTextContent("node=");
  });

  it("the panel shows its empty state with no node selected", async () => {
    get.mockImplementation(answerNodes());

    mount("/lineage");

    expect(await screen.findByText("2 nodes")).toBeInTheDocument();
    expect(within(panel()).getByText(EMPTY_STATE)).toBeVisible();
  });

  it("a node= opens its panel", async () => {
    get.mockImplementation(answerNodes());

    mount("/lineage?node=model.s.a");

    expect(await screen.findByText("Model a.")).toBeVisible();
    expect(within(panel()).queryByText(EMPTY_STATE)).not.toBeInTheDocument();
  });

  it("selecting a node does not change the canvas size", async () => {
    get.mockImplementation(answerNodes());
    mount("/lineage");
    const canvas = (await screen.findByLabelText("Lineage graph")).parentElement!;
    const before = { height: canvas.style.height, width: canvas.style.width, minHeight: canvas.style.minHeight };

    fireEvent.click(await nodeButton("a"));

    expect(await screen.findByText("Model a.")).toBeVisible();
    expect({ height: canvas.style.height, width: canvas.style.width, minHeight: canvas.style.minHeight }).toEqual(before);
    expect(before).toEqual({ height: "60vh", width: "", minHeight: "400px" });
  });

  it("Esc clears the selection and keeps the panel", async () => {
    get.mockImplementation(answerNodes());
    mount("/lineage?node=model.s.a");
    expect(await screen.findByText("Model a.")).toBeVisible();

    await userEvent.keyboard("{Escape}");

    await waitFor(() => expect(within(panel()).getByText(EMPTY_STATE)).toBeVisible());
    expect(screen.getByLabelText("location")).not.toHaveTextContent("node=");
    expect(screen.queryByText("Model a.")).not.toBeInTheDocument();
  });

  it("closing the panel with its close button returns the focus to the node", async () => {
    get.mockImplementation(answerNodes());
    mount("/lineage?node=model.s.a");
    const button = await nodeButton("a");
    expect(await screen.findByText("Model a.")).toBeVisible();

    await userEvent.click(screen.getByRole("button", { name: "Close the node panel" }));

    await waitFor(() => expect(within(panel()).getByText(EMPTY_STATE)).toBeVisible());
    expect(button).toHaveFocus();
  });

  it("Esc returns the focus to the node", async () => {
    get.mockImplementation(answerNodes());
    mount("/lineage?node=model.s.a");
    const button = await nodeButton("a");
    expect(await screen.findByText("Model a.")).toBeVisible();

    await userEvent.keyboard("{Escape}");

    await waitFor(() => expect(within(panel()).getByText(EMPTY_STATE)).toBeVisible());
    expect(button).toHaveFocus();
  });

  it("focus returns to the node after a background click clears the selection", async () => {
    get.mockImplementation(answerNodes());
    mount("/lineage?node=model.s.a");
    const button = await nodeButton("a");
    expect(await screen.findByText("Model a.")).toBeVisible();

    fireEvent.click(document.querySelector(".react-flow__pane")!);

    await waitFor(() => expect(within(panel()).getByText(EMPTY_STATE)).toBeVisible());
    expect(screen.getByLabelText("location")).not.toHaveTextContent("node=");
    expect(button).toHaveFocus();
  });

  it("a node= that only the next graph holds survives the previous graph standing in", async () => {
    // The graph for s2 is slow; until it arrives the graph for s1 stays, and it does not hold model.s.b.
    const slow = deferred<unknown>();
    const only = (id: string) =>
      lineageGraph({ ...GRAPH, nodes: GRAPH.nodes.filter((node) => node.unique_id === id) });
    const nodes = answerNodes();
    get.mockImplementation((path, options) => {
      if (path !== LINEAGE) return nodes(path, options);
      return options.params.query?.select === "s1" ? Promise.resolve(ok(only("model.s.a"))) : slow.promise;
    });
    mount("/lineage?select=s1", <Go to="/lineage?select=s2&node=model.s.b" />);
    expect(await screen.findByText("1 nodes")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "go" }));
    await waitFor(() => expect(get.mock.calls.some(([, o]) => o.params.query?.select === "s2")).toBe(true));
    expect(screen.getByLabelText("location")).toHaveTextContent("node=model.s.b");

    slow.resolve(ok(only("model.s.b")));
    // The new graph is on screen once its node is, and the node= is still there.
    await nodeButton("b");
    expect(screen.getByLabelText("location")).toHaveTextContent("node=model.s.b");
    expect(await screen.findByText("Model b.")).toBeVisible();
  });

  it("a node_not_found keeps the graph and shows a note", async () => {
    get.mockImplementation((path) =>
      Promise.resolve(path === LINEAGE ? ok(GRAPH) : apiFailure("node_not_found", "no node model.s.a", 404)),
    );
    mount("/lineage?node=model.s.a");

    expect(await screen.findByRole("alert")).toHaveTextContent("no node model.s.a");
    expect(screen.getByText("2 nodes")).toBeInTheDocument();
    expect(screen.getByLabelText("Lineage graph")).toBeInTheDocument();
  });

  it("a node= that the graph holds stays", async () => {
    get.mockImplementation(answerNodes());

    mount("/lineage?node=model.s.a");

    expect(await screen.findByText("2 nodes")).toBeInTheDocument();
    expect(screen.getByLabelText("location")).toHaveTextContent("node=model.s.a");
  });

  it("a load error hides the graph instead of showing a stale one", async () => {
    get.mockImplementation((_, options) =>
      Promise.resolve(
        options.params.query?.layers === undefined ? ok(GRAPH) : apiFailure("internal", "boom", 500),
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
