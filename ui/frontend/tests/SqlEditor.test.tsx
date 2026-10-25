import { CompletionContext, type CompletionResult, type CompletionSource } from "@codemirror/autocomplete";
import { EditorState } from "@codemirror/state";
import { EditorView } from "@codemirror/view";
import { MantineProvider } from "@mantine/core";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { errorHighlight, sqlExtension } from "../src/components/SqlEditor";
import { WorksheetPanel } from "../src/screens/explorer/WorksheetPanel";

const CATALOG = {
  database: "sherpa",
  schema_name: "marts",
  domains: [
    {
      name: "sales",
      tables: [
        {
          name: "fct_orders",
          domain: "sales",
          description: "",
          grain: "",
          primary_key: ["order_id"],
          columns: [
            {
              name: "order_id",
              data_type: "bigint",
              description: "",
              is_primary_key: true,
              is_pii: false,
            },
            {
              name: "order_status",
              data_type: "text",
              description: "",
              is_primary_key: false,
              is_pii: false,
            },
          ],
        },
        {
          name: "dim_products",
          domain: "sales",
          description: "",
          grain: "",
          primary_key: ["product_id"],
          columns: [
            {
              name: "sku",
              data_type: "text",
              description: "",
              is_primary_key: false,
              is_pii: false,
            },
          ],
        },
      ],
    },
  ],
};

const post = vi.fn();
vi.mock("../src/api/client", () => ({
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

function Harness({ initial, onSettled = () => {} }: { initial: string; onSettled?: () => void }) {
  const [sql, setSql] = useState(initial);
  return (
    <MantineProvider env="test">
      <WorksheetPanel worksheet={{ id: "w1", name: "Worksheet 1", sql }} onSqlChange={setSql} onSettled={onSettled} />
    </MantineProvider>
  );
}

function editorView(): EditorView {
  const view = EditorView.findFromDOM(document.querySelector(".cm-editor") as HTMLElement);
  if (!view) throw new Error("no editor");
  return view;
}

describe("WorksheetPanel", () => {
  beforeEach(() => {
    post.mockReset();
  });

  it("Run becomes Cancel while running", async () => {
    const user = userEvent.setup();
    post.mockImplementation((path: string) =>
      path === "/api/queries" ? new Promise(() => {}) : Promise.resolve({ response: { status: 204 } }),
    );
    render(<Harness initial="select 1" />);

    await user.click(screen.getByRole("button", { name: "Run" }));

    expect(screen.queryByRole("button", { name: "Run" })).not.toBeInTheDocument();
    expect(screen.getByText(/Running… 0 s/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByText("Cancelled after 0 s")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run" })).toBeInTheDocument();
  });

  it("error position is highlighted, offset by the selection", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue({
      error: {
        code: "sql_error",
        message: 'syntax error at or near "selec"',
        detail: { position: 0 },
      },
      response: { status: 422 },
    });
    render(<Harness initial={"selec 2;\nselec 2"} />);
    act(() => editorView().dispatch({ selection: { anchor: 9, head: 16 } }));

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
    expect(editorView().posAtDOM(mark)).toBe(9);
    expect(screen.getByText('syntax error at or near "selec"')).toBeInTheDocument();
  });

  it("clears the highlight when the text changes", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue({
      error: { code: "sql_error", message: "boom", detail: { position: 0 } },
      response: { status: 422 },
    });
    render(<Harness initial="selec 1" />);
    await user.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(document.querySelector(".cm-error-position")).not.toBeNull());

    act(() => editorView().dispatch({ changes: { from: 0, insert: "x" } }));

    expect(document.querySelector(".cm-error-position")).toBeNull();
  });

  it("Format replaces the text with the formatted SQL", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue({
      data: { sql: "SELECT 1" },
      response: { status: 200 },
    });
    render(<Harness initial="select    1" />);

    await user.click(screen.getByRole("button", { name: "Format" }));

    await waitFor(() => expect(editorView().state.doc.toString()).toBe("SELECT 1"));
    expect(post).toHaveBeenCalledWith("/api/queries/format", {
      body: { sql: "select    1" },
    });
  });

  it("Run is disabled for an empty worksheet", () => {
    render(<Harness initial="  " />);

    expect(screen.getByRole("button", { name: "Run" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Run selection" })).toBeDisabled();
  });
});
