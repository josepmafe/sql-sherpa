import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { cellText } from "../src/components/resultView";
import { ExplorerScreen } from "../src/screens/explorer/ExplorerScreen";
import { useWorksheetRun } from "../src/screens/explorer/useWorksheetRun";
import {
  addWorksheet,
  type ExplorerState,
  freshState,
  saveExplorerState,
  STORAGE_KEY,
  updateWorksheet,
} from "../src/screens/explorer/worksheetStore";
import { catalog, health, noContent, ok, queryResponse, table } from "./fixtures/api";
import { stubPaneLayout } from "./fixtures/panes";
import { editors, isShown, renderWithProviders, wideScreen } from "./fixtures/render";

const post = vi.fn();
vi.mock("../src/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api/client")>()),
  api: { POST: (...args: unknown[]) => post(...args) },
}));
vi.mock("../src/api/queries", () => ({
  useCatalog: () => ({ data: CATALOG }),
  useTableDetails: () => ({ data: undefined }),
  useHealth: () => useHealth(),
}));
const useHealth = vi.fn();
// Pass-through spies: every WorksheetPanel render calls useWorksheetRun once, every grid cell render calls cellText.
vi.mock("../src/screens/explorer/useWorksheetRun", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/screens/explorer/useWorksheetRun")>();
  return { ...actual, useWorksheetRun: vi.fn(actual.useWorksheetRun) };
});
vi.mock("../src/components/resultView", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/components/resultView")>();
  return { ...actual, cellText: vi.fn(actual.cellText) };
});

const CATALOG = catalog({ domains: [{ name: "sales", tables: [table()] }] });
const RESPONSE = queryResponse();

// jsdom has no layout: the pane groups measure the stubbed sizes once the render has settled.
async function settleLayout(): Promise<void> {
  await act(async () => {});
}

function renderScreen(path = "/explorer") {
  return renderWithProviders(<ExplorerScreen />, { path });
}

describe("ExplorerScreen", () => {
  beforeEach(() => {
    localStorage.clear();
    useHealth.mockReset().mockReturnValue({ data: health() });
    stubPaneLayout();
    post.mockReset();
    post.mockImplementation((path: string) =>
      path === "/api/queries" ? new Promise(() => {}) : Promise.resolve(noContent()),
    );
  });

  it("switching worksheet tabs does not cancel a running query", async () => {
    const user = userEvent.setup();
    const seeded = freshState();
    saveExplorerState(updateWorksheet(seeded, seeded.activeId, { sql: "select 1" }));
    renderScreen();
    await user.click(screen.getByRole("button", { name: "Run" }));

    await user.click(screen.getByRole("button", { name: "New worksheet" }));
    await user.click(screen.getByRole("tab", { name: "Worksheet 1" }));

    expect(post.mock.calls.filter(([path]) => path === "/api/queries")).toHaveLength(1);
    expect(post.mock.calls.filter(([path]) => path === "/api/queries/{query_id}/cancel")).toHaveLength(0);
    expect(screen.getAllByRole("button", { name: "Cancel" })[0]).toBeInTheDocument();
  });

  it("saves the worksheets as they change, and the next page load shows them", () => {
    const { unmount } = renderScreen();

    act(() => editors()[0]!.dispatch({ changes: { from: 0, insert: "select 42" } }));

    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY)!) as ExplorerState;
    expect(saved.worksheets.map((w) => w.sql)).toEqual(["select 42"]);
    unmount();
    renderScreen();
    expect(editors()[0]!.state.doc.toString()).toBe("select 42");
  });

  it("renames a tab on double-click: Enter keeps the name, Escape and an empty name do not", async () => {
    const user = userEvent.setup();
    renderScreen();
    const rename = async (from: string, keys: string) => {
      await user.dblClick(screen.getByRole("tab", { name: from }));
      await user.keyboard(keys); // the field opens focused, with its text selected
    };

    await rename("Worksheet 1", "Orders{Enter}");
    await rename("Orders", "Nope{Escape}");
    await rename("Orders", "{Backspace}{Enter}");

    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Orders"]);
    expect(screen.queryByRole("textbox", { name: "Worksheet name" })).not.toBeInTheDocument();
  });

  it("closes a tab with its x, and the last tab has none", async () => {
    const user = userEvent.setup();
    renderScreen();
    await user.click(screen.getByRole("button", { name: "New worksheet" }));
    expect(screen.getByRole("tab", { name: "Worksheet 2" })).toHaveAttribute("aria-selected", "true");

    await user.click(screen.getByTitle("Close Worksheet 2"));

    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Worksheet 1"]);
    expect(screen.getByRole("tab", { name: "Worksheet 1" })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByTitle(/^Close /)).not.toBeInTheDocument();
  });

  it("the worksheet tabs are a tablist: each controls its panel, and the arrow keys move between them", async () => {
    const user = userEvent.setup();
    saveExplorerState(addWorksheet(freshState()));
    renderScreen();
    const [one, two] = screen.getAllByRole("tab") as [HTMLElement, HTMLElement];
    const panel = document.getElementById(two.getAttribute("aria-controls")!);
    expect(panel).toHaveAttribute("role", "tabpanel");
    expect(panel).toHaveAttribute("aria-labelledby", two.id);
    expect([one.tabIndex, two.tabIndex]).toEqual([-1, 0]);
    // Only tabs inside the tablist: the close marks and the new-worksheet button are not in it.
    expect(within(screen.getByRole("tablist")).queryAllByRole("button")).toEqual([]);

    act(() => two.focus());
    await user.keyboard("{ArrowLeft}");

    expect(one).toHaveFocus();
    expect(one).toHaveAttribute("aria-selected", "true");
    expect(isShown(document.getElementById(one.getAttribute("aria-controls")!)!)).toBe(true);
  });

  it("F2 renames the focused tab, and Enter gives the focus back to it", async () => {
    const user = userEvent.setup();
    renderScreen();
    act(() => screen.getByRole("tab", { name: "Worksheet 1" }).focus());

    await user.keyboard("{F2}");
    await user.keyboard("Orders{Enter}");

    expect(screen.getByRole("tab", { name: "Orders" })).toHaveFocus();
  });

  it("Delete closes the focused tab, and the focus moves to the tab that becomes active", async () => {
    const user = userEvent.setup();
    saveExplorerState(addWorksheet(freshState()));
    renderScreen();
    act(() => screen.getByRole("tab", { name: "Worksheet 2" }).focus());

    await user.keyboard("{Delete}");

    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual(["Worksheet 1"]);
    expect(screen.getByRole("tab", { name: "Worksheet 1" })).toHaveFocus();
    await user.keyboard("{Delete}");
    expect(screen.getAllByRole("tab")).toHaveLength(1);
  });

  it("the first Tab stop skips the tree to the active worksheet's editor", async () => {
    const user = userEvent.setup();
    saveExplorerState(addWorksheet(freshState()));
    renderScreen();

    await user.tab();
    expect(screen.getByRole("button", { name: "Skip to the SQL editor" })).toHaveFocus();
    await user.keyboard("{Enter}");

    expect(document.activeElement).toHaveClass("cm-content");
    expect(isShown(document.activeElement as HTMLElement)).toBe(true);
  });

  it("takes in the worksheets and history another tab saves, and ignores other keys and bad values", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    renderScreen();
    const mine = JSON.parse(localStorage.getItem(STORAGE_KEY)!) as ExplorerState;
    const theirs: ExplorerState = {
      worksheets: [...mine.worksheets, { id: "theirs", name: "From the other tab", sql: "select 2" }],
      activeId: "theirs",
      history: [{ sql: "select 2", ranAt: "2026-10-26T09:00:00.000Z", returnedRows: 1, status: "done" }],
    };
    const storageEvent = (key: string, newValue: string) =>
      act(() => void window.dispatchEvent(new StorageEvent("storage", { key, newValue })));

    storageEvent("another.key", JSON.stringify(theirs));
    storageEvent(STORAGE_KEY, "{not json");
    expect(screen.queryByRole("tab", { name: "From the other tab" })).not.toBeInTheDocument();

    storageEvent(STORAGE_KEY, JSON.stringify(theirs));

    expect(screen.getByRole("tab", { name: "From the other tab" })).toHaveAttribute("aria-selected", "false");
    expect(screen.getByRole("tab", { name: "Worksheet 1" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "History (1)" })).toBeInTheDocument();
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY)!) as ExplorerState;
    expect(saved.worksheets.map((w) => w.id)).toEqual([mine.worksheets[0]!.id, "theirs"]);
    warn.mockRestore();
  });

  it("while the database is down Run and Preview are disabled with the reason, and they come back with it", async () => {
    const user = userEvent.setup();
    const seeded = freshState();
    saveExplorerState(updateWorksheet(seeded, seeded.activeId, { sql: "select 1" }));
    useHealth.mockReturnValue({ data: health({ database: "down" }) });
    const view = renderScreen();

    const run = screen.getByRole("button", { name: "Run" });
    expect(run).toBeDisabled();
    expect(run).toHaveAccessibleDescription("Database unavailable: queries run again once it is back");
    await user.click(screen.getByRole("button", { name: "Actions for fct_orders" }));
    expect(await screen.findByRole("menuitem", { name: "Preview 100 rows" })).toBeDisabled();
    await user.keyboard("{Escape}");

    useHealth.mockReturnValue({ data: health() });
    view.rerender(<ExplorerScreen />);

    expect(screen.getByRole("button", { name: "Run" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Run" })).toHaveAccessibleDescription("Ctrl/Cmd+Enter runs");
  });

  it("below 1280 px a click on a table opens the Inspector as an overlay and moves the focus to its heading", async () => {
    const user = userEvent.setup();
    renderScreen();
    await settleLayout();
    expect(screen.queryByRole("heading", { name: "Table details" })).not.toBeInTheDocument();
    const widthBefore = screen.getByRole("separator", { name: "Resize the tree" }).getAttribute("aria-valuenow");

    await user.click(screen.getByRole("button", { name: "fct_orders" }));

    const overlay = screen.getByRole("complementary", { name: "Table details" });
    expect(overlay).toHaveTextContent("Loading fct_orders…");
    expect(within(overlay).getByRole("heading", { name: "Table details" })).toHaveFocus();
    expect(screen.queryByRole("separator", { name: "Resize the Inspector" })).not.toBeInTheDocument();
    expect(screen.getByRole("separator", { name: "Resize the tree" })).toHaveAttribute("aria-valuenow", widthBefore);
  });

  it("below 1280 px Escape closes the Inspector overlay and the focus goes back to the table's row", async () => {
    const user = userEvent.setup();
    renderScreen();
    await settleLayout();
    await user.click(screen.getByRole("button", { name: "fct_orders" }));
    expect(screen.getByRole("complementary", { name: "Table details" })).toBeInTheDocument();

    await user.keyboard("{Escape}");

    expect(screen.queryByRole("complementary", { name: "Table details" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "fct_orders" })).toHaveFocus();
  });

  it("below 1280 px the overlay's toggle closes it and the focus goes back to the table's row", async () => {
    const user = userEvent.setup();
    renderScreen();
    await settleLayout();
    await user.click(screen.getByRole("button", { name: "fct_orders" }));

    await user.click(screen.getByRole("button", { name: "Close the table details" }));

    expect(screen.queryByRole("complementary", { name: "Table details" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "fct_orders" })).toHaveFocus();
  });


  it("?table= selects and inspects a known mart", async () => {
    vi.spyOn(window, "matchMedia").mockImplementation(wideScreen);
    renderScreen("/explorer?table=fct_orders");

    expect(await screen.findByText("Loading fct_orders…")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("?table= with an unknown name notifies", async () => {
    vi.spyOn(window, "matchMedia").mockImplementation(wideScreen);
    renderScreen("/explorer?table=nope");

    expect(await screen.findByRole("alert")).toHaveTextContent("No mart named nope");
    expect(screen.getByText("Select a table to inspect it.")).toBeInTheDocument();
  });

  it("from 1280 px the Inspector has its own column", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "matchMedia").mockImplementation(wideScreen);
    renderScreen();
    expect(screen.getByText("Select a table to inspect it.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "fct_orders" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Table details" })).toHaveTextContent("Loading fct_orders…");
  });

  it("typing in one worksheet re-renders neither the other worksheets nor any result grid", async () => {
    const user = userEvent.setup();
    post.mockImplementation(() => Promise.resolve(ok(RESPONSE)));
    const one = freshState();
    const two = addWorksheet(updateWorksheet(one, one.activeId, { sql: "select 1" }));
    saveExplorerState(updateWorksheet(two, two.activeId, { sql: "select 2" }));
    renderScreen();
    for (const tab of ["Worksheet 2", "Worksheet 1"]) {
      await user.click(screen.getByRole("tab", { name: tab }));
      await user.click(screen.getAllByRole("button", { name: "Run" }).find(isShown)!);
    }
    expect(await screen.findAllByText("2 rows in 5 ms")).toHaveLength(2);
    const [first, second] = JSON.parse(localStorage.getItem(STORAGE_KEY)!).worksheets as { id: string }[];
    const panelRenders = (id: string) => vi.mocked(useWorksheetRun).mock.calls.filter(([w]) => w === id).length;
    const before = { first: panelRenders(first!.id), second: panelRenders(second!.id) };
    vi.mocked(cellText).mockClear();

    act(() => editors()[0]!.dispatch({ changes: { from: 0, insert: "-- note\n" } }));
    act(() => editors()[0]!.dispatch({ changes: { from: 0, insert: "x" } }));

    expect(editors()[0]!.state.doc.toString()).toBe("x-- note\nselect 1");
    expect(panelRenders(first!.id)).toBeGreaterThan(before.first);
    // Two edits, each re-rendering the edited panel a small number of times (2 allowed): a bound, not React's exact count.
    expect(panelRenders(first!.id)).toBeLessThanOrEqual(before.first + 4);
    expect(panelRenders(second!.id)).toBe(before.second);
    expect(cellText).not.toHaveBeenCalled();
  });
});

describe("ExplorerScreen panes", () => {
  const LAYOUT_KEY = "sherpa.v1.layout.explorer";
  const handle = (name: string) => screen.getByRole("separator", { name });
  const valueNow = (name: string) => Number(handle(name).getAttribute("aria-valuenow"));
  const storedLayout = () => JSON.parse(localStorage.getItem(LAYOUT_KEY) ?? "null") as unknown;

  beforeEach(() => {
    localStorage.clear();
    useHealth.mockReset().mockReturnValue({ data: health() });
    post.mockReset();
    post.mockImplementation(() => new Promise(() => {}));
    stubPaneLayout();
    vi.spyOn(window, "matchMedia").mockImplementation(wideScreen);
  });

  it("from 1280 px the tree, the worksheets and the Inspector are three panes with named handles", async () => {
    renderScreen();
    await settleLayout();

    expect(valueNow("Resize the tree")).toBeCloseTo((280 / 1200) * 100, 0);
    expect(valueNow("Resize the Inspector")).toBeCloseTo((620 / 1200) * 100, 0);
  });

  it("the arrow keys resize the tree and the Inspector, and the sizes are remembered", async () => {
    const user = userEvent.setup();
    renderScreen();
    await settleLayout();
    const treeBefore = valueNow("Resize the tree");
    const inspectorBefore = valueNow("Resize the Inspector");

    handle("Resize the tree").focus();
    await user.keyboard("{ArrowRight}");
    handle("Resize the Inspector").focus();
    await user.keyboard("{ArrowLeft}");

    expect(valueNow("Resize the tree")).toBeGreaterThan(treeBefore);
    expect(valueNow("Resize the Inspector")).toBeLessThan(inspectorBefore);
    const layout = storedLayout() as { sizes: Record<string, number> };
    expect(layout.sizes.tree).toBeGreaterThan(280);
    expect(layout.sizes.inspector).toBeGreaterThan(300);
  });

  it("restores a remembered tree width, clamps a stored 5000 to the maximum and ignores corrupt JSON", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    localStorage.setItem(LAYOUT_KEY, JSON.stringify({ sizes: { tree: 400 }, collapsed: {} }));
    const first = renderScreen();
    await settleLayout();
    expect(valueNow("Resize the tree")).toBeCloseTo((400 / 1200) * 100, 0);
    first.unmount();

    localStorage.setItem(LAYOUT_KEY, JSON.stringify({ sizes: { tree: 5000 }, collapsed: {} }));
    const second = renderScreen();
    await settleLayout();
    expect(valueNow("Resize the tree")).toBeCloseTo((480 / 1200) * 100, 0);
    second.unmount();

    localStorage.setItem(LAYOUT_KEY, '{"sizes":');
    renderScreen();
    await settleLayout();
    expect(valueNow("Resize the tree")).toBeCloseTo((280 / 1200) * 100, 0);
    warn.mockRestore();
  });

  it("the tree's button folds it to a strip and opens it again", async () => {
    const user = userEvent.setup();
    renderScreen();
    await settleLayout();
    expect(screen.getByRole("button", { name: "Collapse the table tree" })).toHaveAttribute("aria-expanded", "true");

    await user.click(screen.getByRole("button", { name: "Collapse the table tree" }));

    expect(screen.getByRole("button", { name: "Open the table tree" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: "fct_orders" })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Search tables and columns" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Open the table tree" }));
    expect(screen.getByRole("button", { name: "fct_orders" })).toBeInTheDocument();
  });

  it("the Inspector's button folds it, and a click on a table opens it again", async () => {
    const user = userEvent.setup();
    renderScreen();
    await settleLayout();

    await user.click(screen.getByRole("button", { name: "Collapse the Inspector" }));

    expect(screen.getByRole("button", { name: "Open the Inspector" })).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("Select a table to inspect it.")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "fct_orders" }));
    expect(screen.getByRole("button", { name: "Collapse the Inspector" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("complementary", { name: "Table details" })).toHaveTextContent("Loading fct_orders…");
  });

  it("a folded tree opens over the editor on focus, and a click on a table selects it and closes the strip", async () => {
    const user = userEvent.setup();
    localStorage.setItem(LAYOUT_KEY, JSON.stringify({ sizes: { tree: 280 }, collapsed: { tree: true } }));
    renderScreen();
    await settleLayout();
    expect(screen.queryByRole("button", { name: "fct_orders" })).not.toBeInTheDocument();

    act(() => screen.getByRole("button", { name: "Open the table tree" }).focus());
    await user.click(await screen.findByRole("button", { name: "fct_orders" }));

    expect(screen.getByRole("complementary", { name: "Table details" })).toHaveTextContent("Loading fct_orders…");
    expect(screen.queryByRole("button", { name: "fct_orders" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Open the table tree" })).toHaveAttribute("aria-expanded", "false");
  });

  it("the editor and the results are two panes: moving the handle neither clears the result nor runs the query again", async () => {
    const user = userEvent.setup();
    post.mockImplementation(() => Promise.resolve(ok(RESPONSE)));
    const seeded = freshState();
    saveExplorerState(updateWorksheet(seeded, seeded.activeId, { sql: "select 1" }));
    renderScreen();
    await settleLayout();
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(await screen.findByText("2 rows in 5 ms")).toBeInTheDocument();
    const before = valueNow("Resize the editor and the results");

    handle("Resize the editor and the results").focus();
    await user.keyboard("{ArrowDown}");

    expect(valueNow("Resize the editor and the results")).toBeGreaterThan(before);
    expect(screen.getByText("2 rows in 5 ms")).toBeInTheDocument();
    expect(post.mock.calls.filter(([path]) => path === "/api/queries")).toHaveLength(1);
  });
});
