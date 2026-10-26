import { EditorView } from "@codemirror/view";
import { MantineProvider } from "@mantine/core";
import { act, render, screen, within } from "@testing-library/react";
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

const post = vi.fn();
vi.mock("../src/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api/client")>()),
  api: { POST: (...args: unknown[]) => post(...args) },
}));
vi.mock("../src/api/queries", () => ({
  useCatalog: () => ({ data: CATALOG }),
  useTableDetails: () => ({ data: undefined }),
}));
// Pass-through spies: every WorksheetPanel render calls useWorksheetRun once, every grid cell render calls cellText.
vi.mock("../src/screens/explorer/useWorksheetRun", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/screens/explorer/useWorksheetRun")>();
  return { ...actual, useWorksheetRun: vi.fn(actual.useWorksheetRun) };
});
vi.mock("../src/components/resultView", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../src/components/resultView")>();
  return { ...actual, cellText: vi.fn(actual.cellText) };
});

const ORDERS = { name: "fct_orders", domain: "sales", description: "", grain: "", primary_key: [], columns: [] };
const CATALOG = { database: "sherpa", schema_name: "marts", domains: [{ name: "sales", tables: [ORDERS] }] };
const RESPONSE = {
  query_id: "q",
  columns: ["n"],
  column_types: ["number"],
  rows: [["1"], ["2"]],
  returned_rows: 2,
  truncated: false,
  elapsed_ms: 5,
  mode: "client",
};

function renderScreen() {
  return render(
    <MantineProvider env="test">
      <ExplorerScreen />
    </MantineProvider>,
  );
}

// A screen at least 1280 px wide; the setup's stub matches no query.
const wideScreen = (query: string) =>
  ({
    matches: query === "(min-width: 80em)",
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }) as MediaQueryList;

// Hidden worksheets stay mounted under a `display: none` box.
const isShown = (element: HTMLElement) => element.closest('[style*="display: none"]') === null;
const editors = () =>
  Array.from(document.querySelectorAll<HTMLElement>(".cm-editor")).map((element) => EditorView.findFromDOM(element)!);

describe("ExplorerScreen", () => {
  beforeEach(() => {
    localStorage.clear();
    post.mockReset();
    post.mockImplementation((path: string) =>
      path === "/api/queries" ? new Promise(() => {}) : Promise.resolve({ response: { status: 204 } }),
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

  it("below 1280 px the Inspector opens as a drawer when a table is clicked", async () => {
    const user = userEvent.setup();
    renderScreen();
    expect(screen.queryByText("Select a table to inspect it.")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "fct_orders" }));

    const drawer = screen.getByRole("dialog", { name: "Table details" });
    expect(drawer).toHaveTextContent("Loading fct_orders…");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("from 1280 px the Inspector has its own column", async () => {
    const user = userEvent.setup();
    const matchMedia = vi.spyOn(window, "matchMedia").mockImplementation(wideScreen);
    renderScreen();
    expect(screen.getByText("Select a table to inspect it.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "fct_orders" }));

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("complementary", { name: "Table details" })).toHaveTextContent("Loading fct_orders…");
    matchMedia.mockRestore();
  });

  it("typing in one worksheet re-renders neither the other worksheets nor any result grid", async () => {
    const user = userEvent.setup();
    post.mockImplementation(() => Promise.resolve({ data: RESPONSE, response: { status: 200 } }));
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
    expect(panelRenders(first!.id)).toBe(before.first + 2);
    expect(panelRenders(second!.id)).toBe(before.second);
    expect(cellText).not.toHaveBeenCalled();
  });
});
