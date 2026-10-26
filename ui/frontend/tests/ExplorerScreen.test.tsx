import { EditorView } from "@codemirror/view";
import { MantineProvider } from "@mantine/core";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { cellText } from "../src/components/resultView";
import { ExplorerScreen } from "../src/screens/explorer/ExplorerScreen";
import { useWorksheetRun } from "../src/screens/explorer/useWorksheetRun";
import { addWorksheet, freshState, saveExplorerState, updateWorksheet } from "../src/screens/explorer/worksheetStore";

const post = vi.fn();
vi.mock("../src/api/client", () => ({ api: { POST: (...args: unknown[]) => post(...args) } }));
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

const CATALOG = { database: "sherpa", schema_name: "marts", domains: [] };
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
  render(
    <MantineProvider env="test">
      <ExplorerScreen />
    </MantineProvider>,
  );
}

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
    const [first, second] = JSON.parse(localStorage.getItem("sherpa.v1.explorer")!).worksheets as { id: string }[];
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
