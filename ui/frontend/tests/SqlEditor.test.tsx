import { CompletionContext, type CompletionResult, type CompletionSource } from "@codemirror/autocomplete";
import { EditorState } from "@codemirror/state";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { errorHighlight, sqlExtension } from "../src/components/SqlEditor";
import { WorksheetPanel } from "../src/screens/explorer/WorksheetPanel";
import { apiFailure, catalog, columnSummary, formatResponse, noContent, ok, table } from "./fixtures/api";
import { renderWithProviders, shownEditor } from "./fixtures/render";

const CATALOG = catalog({
  domains: [
    {
      name: "sales",
      tables: [
        table({
          name: "fct_orders",
          primary_key: ["order_id"],
          columns: [
            columnSummary({ name: "order_id", data_type: "bigint", is_primary_key: true }),
            columnSummary({ name: "order_status" }),
          ],
        }),
        table({ name: "dim_products", primary_key: ["product_id"], columns: [columnSummary({ name: "sku" })] }),
      ],
    },
  ],
});

const post = vi.fn();
vi.mock("../src/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api/client")>()),
  api: { POST: (...args: unknown[]) => post(...args) },
}));
vi.mock("../src/api/queries", () => ({
  useCatalog: () => ({ data: CATALOG }),
}));

async function completions(doc: string, activeTable?: string): Promise<string[]> {
  const state = EditorState.create({
    doc,
    extensions: [sqlExtension(CATALOG, activeTable)],
  });
  const pos = doc.length;
  const sources = state.languageDataAt<CompletionSource>("autocomplete", pos);
  const results = await Promise.all(sources.map((source) => source(new CompletionContext(state, pos, true))));
  return results.flatMap((result: CompletionResult | null) => result?.options.map((o) => o.label) ?? []);
}

describe("sqlExtension", () => {
  it("autocomplete offers marts tables and columns", async () => {
    expect(await completions("select * from marts.")).toEqual(expect.arrayContaining(["fct_orders", "dim_products"]));
    expect(await completions("select marts.fct_orders.")).toEqual(expect.arrayContaining(["order_id", "order_status"]));
    expect(await completions("select ", "fct_orders")).toEqual(expect.arrayContaining(["order_id", "order_status"]));
    expect(await completions("select ", "fct_orders")).not.toContain("sku");
    // The guard rejects unqualified tables, so they are never offered bare.
    expect(await completions("select * from ")).not.toContain("fct_orders");
  });
});

describe("errorHighlight", () => {
  it("covers the word at the position", () => {
    expect(errorHighlight("select foo_bar from t", 7, 0)).toEqual({
      from: 7,
      to: 14,
    });
  });

  it("adds the selection's offset", () => {
    expect(errorHighlight("selec 1", 0, 20)).toEqual({ from: 20, to: 25 });
  });

  it("counts code points, not UTF-16 units", () => {
    // "😀" is one character for Postgres and two UTF-16 units in the editor.
    expect(errorHighlight("select '😀' x", 11, 0)).toEqual({
      from: 12,
      to: 13,
    });
  });

  it("clamps a position past the end of the text", () => {
    expect(errorHighlight("select", 99, 0)).toEqual({ from: 5, to: 6 });
    expect(errorHighlight("", 0, 3)).toEqual({ from: 3, to: 3 });
  });
});

type HarnessProps = { initial: string; onSettled?: () => void; blockedReason?: string };

function Harness({ initial, onSettled = () => {}, blockedReason }: HarnessProps) {
  const [sql, setSql] = useState(initial);
  return (
    <WorksheetPanel
      worksheet={{ id: "w1", name: "Worksheet 1", sql }}
      blockedReason={blockedReason}
      onSqlChange={(_, next) => setSql(next)}
      onSettled={onSettled}
      onRunStarted={() => {}}
    />
  );
}

describe("WorksheetPanel", () => {
  beforeEach(() => {
    post.mockReset();
  });

  it("Run becomes Cancel while running", async () => {
    const user = userEvent.setup();
    post.mockImplementation((path: string) =>
      path === "/api/queries" ? new Promise(() => {}) : Promise.resolve(noContent()),
    );
    renderWithProviders(<Harness initial="select 1" />);

    await user.click(screen.getByRole("button", { name: "Run" }));

    expect(screen.queryByRole("button", { name: "Run" })).not.toBeInTheDocument();
    expect(screen.getByText(/Running… 0 s/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("Cancelled after 0 s")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run" })).toBeInTheDocument();
  });

  it("error position is highlighted, offset by the selection", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue(apiFailure("sql_error", 'syntax error at or near "selec"', 422, 0));
    renderWithProviders(<Harness initial={"selec 2;\nselec 2"} />);
    act(() => shownEditor().dispatch({ selection: { anchor: 9, head: 16 } }));

    await user.click(await screen.findByRole("button", { name: "Run selection" }));

    await waitFor(() => expect(document.querySelector(".cm-error-position")).not.toBeNull());
    expect(post).toHaveBeenCalledWith(
      "/api/queries",
      expect.objectContaining({
        body: expect.objectContaining({ sql: "selec 2" }),
      }),
    );
    const mark = document.querySelector(".cm-error-position") as HTMLElement;
    expect(mark.textContent).toBe("selec");
    // The second statement, not the first: the selection's start offset (9) is added to the position (0).
    expect(shownEditor().posAtDOM(mark)).toBe(9);
    expect(screen.getByText('syntax error at or near "selec"')).toBeInTheDocument();
    expect(screen.getByText("Postgres stopped at the highlighted spot in the editor.")).toBeInTheDocument();
  });

  it("an error without a position has no highlight hint", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue(apiFailure("sql_error", "boom", 422));
    renderWithProviders(<Harness initial="select 1" />);

    await user.click(screen.getByRole("button", { name: "Run" }));

    expect(await screen.findByText("boom")).toBeInTheDocument();
    expect(screen.queryByText(/highlighted spot/)).not.toBeInTheDocument();
  });

  it("clears the highlight when the text changes", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue(apiFailure("sql_error", "boom", 422, 0));
    renderWithProviders(<Harness initial="selec 1" />);
    await user.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(document.querySelector(".cm-error-position")).not.toBeNull());

    act(() => shownEditor().dispatch({ changes: { from: 0, insert: "x" } }));

    expect(document.querySelector(".cm-error-position")).toBeNull();
  });

  it("Format replaces the text with the formatted SQL", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue(ok(formatResponse({ sql: "SELECT 1" })));
    renderWithProviders(<Harness initial="select    1" />);

    await user.click(screen.getByRole("button", { name: "Format" }));

    await waitFor(() => expect(shownEditor().state.doc.toString()).toBe("SELECT 1"));
    expect(post).toHaveBeenCalledWith("/api/queries/format", {
      body: { sql: "select    1" },
    });
  });

  it("Ctrl+Enter and Cmd+Enter run the worksheet without adding a line; Enter alone does not run", async () => {
    const user = userEvent.setup();
    post.mockImplementation((path: string) =>
      path === "/api/queries" ? new Promise(() => {}) : Promise.resolve(noContent()),
    );
    renderWithProviders(<Harness initial="select 1" />);
    const runs = () => post.mock.calls.filter(([path]) => path === "/api/queries");
    const content = document.querySelector(".cm-content") as HTMLElement;

    fireEvent.keyDown(content, { key: "Enter", ctrlKey: true });
    expect(runs()).toHaveLength(1);
    expect(runs()[0]?.[1].body.sql).toBe("select 1");
    expect(shownEditor().state.doc.toString()).toBe("select 1");
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.keyDown(content, { key: "Enter", metaKey: true });
    expect(runs()).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.keyDown(content, { key: "Enter" });

    expect(runs()).toHaveLength(2);
  });

  it("Run is disabled for an empty worksheet, and says why", () => {
    renderWithProviders(<Harness initial="  " />);

    const run = screen.getByRole("button", { name: "Run" });
    expect(run).toBeDisabled();
    expect(run).toHaveAccessibleDescription("Write a query to run it");
    expect(screen.getByText("Write a query to run it")).toBeVisible();
    expect(screen.getByRole("button", { name: "Run selection" })).toBeDisabled();
  });

  it("a blocked worksheet disables Run, Run selection and Ctrl+Enter, says why, and still formats", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Harness initial={"select 1"} blockedReason="Database unavailable" />);
    act(() => shownEditor().dispatch({ selection: { anchor: 0, head: 6 } }));

    fireEvent.keyDown(document.querySelector(".cm-content") as HTMLElement, { key: "Enter", ctrlKey: true });

    const run = screen.getByRole("button", { name: "Run" });
    expect(run).toBeDisabled();
    expect(run).toHaveAccessibleDescription("Database unavailable");
    expect(screen.getByRole("button", { name: "Run selection" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Format" })).toBeEnabled();
    expect(post).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Format" }));
    expect(post).toHaveBeenCalledWith("/api/queries/format", { body: { sql: "select 1" } });
  });
});
