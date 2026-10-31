import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { orderNodes } from "../../src/screens/lineage/layout";
import { LineageScreen } from "../../src/screens/lineage/LineageScreen";
import {
  apiFailure,
  type ColumnFocusView,
  columnDetail,
  columnFocusView,
  columnView,
  focusColumn,
  type LineageGraph,
  lineageGraph,
  lineageNode,
  nodeDetails,
  ok,
} from "../fixtures/api";
import { deferred, renderWithProviders } from "../fixtures/render";

// No test here reads a position, and the real ELK layout is slow under load.
vi.mock("../../src/screens/lineage/layout", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/screens/lineage/layout")>()),
  orderNodes: vi.fn(),
}));

type Options = {
  params: { query?: { select?: string; layers?: string; columns?: boolean }; path?: { unique_id: string; column?: string } };
};
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
    selector: uniqueId.split(".").pop() ?? "",
  });
const LINEAGE = "/api/lineage";
const MESSAGE = "unsupported selector 'tag:x'; supported selectors: a model name";
const EMPTY_STATE = "Select a node to see its SQL, columns and tests";

// The graph, and the panel of the node asked for (its id is in the request's path params).
function answerNodes(graph: LineageGraph = GRAPH) {
  return (path: string, options: Options) =>
    path === LINEAGE ? Promise.resolve(ok(graph)) : Promise.resolve(ok(DETAILS(options.params.path?.unique_id ?? "")));
}

// Three nodes with columns: a (staging) -> b (marts), and c, which neither touches.
const A = "model.s.a";
const B = "model.s.b";
const C = "model.s.c";
const SCENE = [
  lineageNode({
    unique_id: A,
    name: "a",
    layer: "staging",
    domain: null,
    materialization: "view",
    columns: [columnView({ name: "amount", kind: "rename" })],
  }),
  lineageNode({
    unique_id: B,
    name: "b",
    layer: "marts",
    domain: "sales",
    columns: [columnView({ name: "net_revenue", kind: "transformed" }), columnView({ name: "units", kind: "row_count" })],
  }),
  lineageNode({
    unique_id: C,
    name: "c",
    layer: "staging",
    domain: null,
    materialization: "view",
    columns: [columnView({ name: "k" })],
  }),
];
const FOCUS = columnFocusView({
  focus: { node: B, column: "net_revenue" },
  columns: [
    focusColumn({ node: B, name: "net_revenue", direction: "focus", kind: "transformed" }),
    focusColumn({ node: A, name: "amount", direction: "upstream", kind: "rename" }),
  ],
  edges: [{ parent: { node: A, column: "amount" }, child: { node: B, column: "net_revenue" } }],
});

// The scene's graph (its columns only once `columns=true` asks for them), a column's focus, and a node's panel.
function answerScene(overrides: { columns?: Partial<LineageGraph>; focus?: ColumnFocusView } = {}) {
  const base = lineageGraph({
    build: { state: "ok", node_count: 3, started_at: null },
    layers_available: ["staging", "marts"],
    edges: [{ source: A, target: B }],
  });
  return (path: string, options: Options) => {
    if (path === LINEAGE) {
      const withColumns = options.params.query?.columns === true;
      const nodes = SCENE.map(({ columns, ...bare }) => (withColumns ? { ...bare, columns } : bare));
      return Promise.resolve(ok({ ...base, nodes, ...(withColumns ? overrides.columns : {}) }));
    }
    if (path.startsWith("/api/lineage/columns/")) return Promise.resolve(ok(overrides.focus ?? FOCUS));
    return Promise.resolve(
      ok({ ...DETAILS(options.params.path?.unique_id ?? ""), columns: [columnDetail({ name: "net_revenue" })] }),
    );
  };
}
const lineageCalls = () => get.mock.calls.filter(([path]) => path === LINEAGE).map(([, options]) => options.params.query);

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
    vi.mocked(orderNodes).mockReset().mockImplementation((nodes) => Promise.resolve(new Map(nodes.map((n, index) => [n.id, index]))));
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

  describe("column lineage", () => {
    const row = async (name: string) =>
      within(await screen.findByLabelText("Lineage graph")).findByRole("button", { name: new RegExp(`^${name}\\b`) });
    const expandAll = () => userEvent.click(screen.getByRole("button", { name: "Expand all" }));
    const here = () => screen.getByLabelText("location");

    it("columns are requested once a node is expanded, and still after Collapse all", async () => {
      get.mockImplementation(answerScene());
      mount("/lineage");
      expect(await screen.findByText("3 nodes")).toBeInTheDocument();
      expect(lineageCalls().some((query) => query?.columns === true)).toBe(false);

      await expandAll();
      await row("net_revenue");
      await userEvent.click(screen.getByRole("button", { name: "Collapse all" }));

      await waitFor(() => expect(screen.queryByRole("button", { name: /^net_revenue\b/ })).not.toBeInTheDocument());
      await userEvent.click(screen.getByRole("checkbox", { name: "marts" }));
      await waitFor(() => expect(lineageCalls().at(-1)).toMatchObject({ layers: "staging", columns: true }));
    });

    it("Expand all expands the nodes shown", async () => {
      get.mockImplementation(answerScene());
      mount("/lineage");
      await screen.findByText("3 nodes");

      await expandAll();

      for (const name of ["amount", "net_revenue", "units", "k"]) expect(await row(name)).toBeVisible();
    });

    it("unavailable column lineage disables expanding and says why", async () => {
      const reason = "the compiled SQL is not in the dbt target";
      get.mockImplementation(answerScene({ columns: { column_lineage: "unavailable", column_lineage_reason: reason } }));
      mount("/lineage");
      await screen.findByText("3 nodes");

      await expandAll();

      const chevron = await screen.findByRole("button", { name: "Columns of b" });
      await waitFor(() => expect(chevron).toBeDisabled());
      expect(chevron).toHaveAccessibleDescription(reason);
      expect(screen.getByRole("button", { name: "Expand all" })).toBeDisabled();
      expect(screen.queryByRole("button", { name: /^net_revenue\b/ })).not.toBeInTheDocument();
    });

    it("column= in the URL restores the focus after a reload", async () => {
      get.mockImplementation(answerScene());

      mount(`/lineage?node=${B}&column=net_revenue`);

      expect(await row("net_revenue")).toHaveAttribute("aria-pressed", "true");
      expect(await row("amount")).toHaveAttribute("aria-pressed", "false");
      expect(get).toHaveBeenCalledWith("/api/lineage/columns/{unique_id}/{column}", {
        params: { path: { unique_id: B, column: "net_revenue" } },
      });
      expect(await nodeButton("c")).toHaveAttribute("data-dimmed");
      expect(screen.queryByRole("button", { name: /^units\b/ })).not.toBeInTheDocument();
    });

    it("clicking a column in a node focuses it", async () => {
      get.mockImplementation(answerScene());
      mount("/lineage");
      await screen.findByText("3 nodes");
      await expandAll();

      fireEvent.click(await row("amount"));

      await waitFor(() => expect(here()).toHaveTextContent(`node=${A}&column=amount`));
    });

    it("clicking a column in the panel's Columns tab focuses it", async () => {
      get.mockImplementation(answerScene());
      mount(`/lineage?node=${B}`);
      await screen.findByText("3 nodes");
      await userEvent.click(await screen.findByRole("tab", { name: "Columns" }));

      await userEvent.click(await screen.findByRole("button", { name: "net_revenue" }));

      await waitFor(() => expect(here()).toHaveTextContent(`node=${B}&column=net_revenue`));
    });

    it("Esc clears the column before the node", async () => {
      get.mockImplementation(answerScene());
      mount(`/lineage?node=${B}&column=net_revenue`);
      await row("net_revenue");

      await userEvent.keyboard("{Escape}");

      await waitFor(() => expect(here()).not.toHaveTextContent("column="));
      expect(here()).toHaveTextContent(`node=${B}`);
      await userEvent.keyboard("{Escape}");
      await waitFor(() => expect(here()).not.toHaveTextContent("node="));
    });

    it("selecting another node drops the column", async () => {
      get.mockImplementation(answerScene());
      mount(`/lineage?node=${B}&column=net_revenue`);
      await row("net_revenue");

      fireEvent.click(await nodeButton("c"));

      await waitFor(() => expect(here()).toHaveTextContent(`node=${C}`));
      expect(here()).not.toHaveTextContent("column=");
    });

    it("related columns in nodes that are not shown are counted in the panel", async () => {
      const outside = columnFocusView({
        ...FOCUS,
        columns: [...FOCUS.columns, focusColumn({ node: "model.s.gone", name: "amount", direction: "upstream" })],
      });
      get.mockImplementation(answerScene({ focus: outside }));

      mount(`/lineage?node=${B}&column=net_revenue`);

      expect(await screen.findByRole("note")).toHaveTextContent("1 related column is outside this view");
      expect(within(screen.getByRole("note")).getByRole("link", { name: "+b+" })).toBeVisible();
    });

    it("the first columns=true answer does not run ELK again", async () => {
      get.mockImplementation(answerScene());
      mount("/lineage");
      await screen.findByText("3 nodes");
      await nodeButton("c");
      const runs = vi.mocked(orderNodes).mock.calls.length;

      await expandAll();
      await row("net_revenue");

      expect(orderNodes).toHaveBeenCalledTimes(runs);
    });

    it("a reload with column= asks for the graph with its columns at once", async () => {
      get.mockImplementation(answerScene());

      mount(`/lineage?node=${B}&column=net_revenue`);
      await row("net_revenue");

      expect(lineageCalls().map((query) => query?.columns)).not.toContain(false);
    });

    it("a column the node does not have clears itself, and the node stays", async () => {
      get.mockImplementation((path, options) =>
        path.startsWith("/api/lineage/columns/")
          ? Promise.resolve(apiFailure("column_not_found", "no column gone", 404))
          : answerScene()(path, options),
      );

      mount(`/lineage?node=${B}&column=gone`);

      await waitFor(() => expect(here()).not.toHaveTextContent("column="));
      expect(here()).toHaveTextContent(`node=${B}`);
    });

    it("the rest shown during a focus is not the user's expansion", async () => {
      get.mockImplementation(answerScene());
      mount(`/lineage?node=${B}&column=net_revenue`);
      await row("net_revenue");
      expect(screen.queryByRole("button", { name: /^units\b/ })).not.toBeInTheDocument();

      fireEvent.click(within(await screen.findByLabelText("Lineage graph")).getAllByRole("button", { name: "+1 more" })[0]!);
      expect(await row("units")).toBeVisible();
      await userEvent.keyboard("{Escape}");

      await waitFor(() => expect(here()).not.toHaveTextContent("column="));
      await waitFor(() =>
        expect(within(screen.getByLabelText("Lineage graph")).queryByRole("button", { name: /^net_revenue\b/ })).not.toBeInTheDocument(),
      );
    });

    it("a node the user opened shows only its related columns once a column is focused from the graph", async () => {
      get.mockImplementation(answerScene());
      mount("/lineage");
      await screen.findByText("3 nodes");
      await expandAll();

      fireEvent.click(await row("net_revenue"));

      await waitFor(() => expect(screen.queryByRole("button", { name: /^units/ })).not.toBeInTheDocument());
      await userEvent.keyboard("{Escape}");
      expect(await row("units")).toBeVisible();
    });
  });
});
