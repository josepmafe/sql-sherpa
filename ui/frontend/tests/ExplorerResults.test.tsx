import { MantineProvider } from "@mantine/core";
import { EditorView } from "@codemirror/view";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ExplorerScreen } from "../src/screens/explorer/ExplorerScreen";
import { freshState, saveExplorerState, updateWorksheet } from "../src/screens/explorer/worksheetStore";

const post = vi.fn();
vi.mock("../src/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api/client")>()),
  api: { POST: (...args: unknown[]) => post(...args) },
}));
const EMAIL = { name: "email", data_type: "text", description: "", is_primary_key: false, is_pii: true };
const CATALOG = {
  database: "sherpa",
  schema_name: "marts",
  domains: [
    {
      name: "sales",
      tables: [
        { name: "fct_orders", domain: "sales", description: "", grain: "", primary_key: [], columns: [] },
        { name: "dim_customers", domain: "sales", description: "", grain: "", primary_key: [], columns: [EMAIL] },
      ],
    },
  ],
};
vi.mock("../src/api/queries", () => ({
  useCatalog: () => ({ data: CATALOG }),
  useTableDetails: () => ({ data: undefined }),
  useHealth: () => ({ data: undefined }),
}));

const RESPONSE = {
  query_id: "q",
  columns: ["name", "amount"],
  column_types: ["text", "number"],
  rows: [
    ["a", "10"],
    ["b", "9"],
  ],
  returned_rows: 2,
  truncated: false,
  elapsed_ms: 5,
  mode: "client",
};

const queryCalls = () => post.mock.calls.filter(([path]) => path === "/api/queries");
const firstColumn = () =>
  screen
    .getAllByRole("row")
    .slice(1)
    .map((row) => row.querySelector("td")?.textContent);
const shownEditor = () =>
  Array.from(document.querySelectorAll<HTMLElement>(".cm-editor"))
    .filter((element) => element.closest('[style*="display: none"]') === null)
    .map((element) => EditorView.findFromDOM(element)!)[0]!;

function renderScreen(sql: string, strict = false) {
  const seeded = freshState();
  saveExplorerState(updateWorksheet(seeded, seeded.activeId, { sql }));
  const screenUnderTest = (
    <MantineProvider env="test">
      <MemoryRouter>
        <ExplorerScreen />
      </MemoryRouter>
    </MantineProvider>
  );
  render(strict ? <StrictMode>{screenUnderTest}</StrictMode> : screenUnderTest);
}

describe("Explorer results", () => {
  beforeEach(() => {
    localStorage.clear();
    post.mockReset();
  });

  it("guard rejection shows the reason without the rule's code, and what is allowed", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue({
      error: {
        code: "guard_rejected",
        message: "[guard:not_select] the statement must be a SELECT query, not DELETE; write one SELECT",
      },
      response: { status: 422 },
    });
    renderScreen("delete from marts.fct_orders");

    await user.click(screen.getByRole("button", { name: "Run" }));

    const alert = (await screen.findByText("Query not allowed")).closest<HTMLElement>('[role="alert"]')!;
    expect(alert).toHaveTextContent("the statement must be a SELECT query, not DELETE; write one SELECT");
    expect(alert).not.toHaveTextContent("[guard:");
    expect(alert).toHaveTextContent("Only one read-only SELECT (or WITH … SELECT) over the marts.<table> tables runs here.");
  });

  it("a timeout shows its hint", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue({
      error: { code: "timeout", message: "Query exceeded 30 s." },
      response: { status: 504 },
    });
    renderScreen("select 1");

    await user.click(screen.getByRole("button", { name: "Run" }));

    expect(await screen.findByText("Query timed out")).toBeInTheDocument();
    expect(screen.getByText(/Narrow it with a WHERE clause or a LIMIT/)).toBeInTheDocument();
  });

  it.each([
    ["busy", 429, "Too many queries running"],
    ["too_many_connections", 503, "Warehouse connections full"],
    ["pii_column", 422, "Masked column"],
  ])("a %s error has its own title", async (code, status, title) => {
    const user = userEvent.setup();
    post.mockResolvedValue({ error: { code, message: "the server's reason" }, response: { status } });
    renderScreen("select 1");

    await user.click(screen.getByRole("button", { name: "Run" }));

    expect(await screen.findByText(title)).toBeInTheDocument();
    expect(screen.getByText("the server's reason")).toBeInTheDocument();
  });

  it("truncated results sort on the server", async () => {
    const user = userEvent.setup();
    post.mockResolvedValueOnce({
      data: { ...RESPONSE, truncated: true, returned_rows: 1000 },
      response: { status: 200 },
    });
    post.mockResolvedValueOnce({
      data: { ...RESPONSE, mode: "server", truncated: true, returned_rows: 1000 },
      response: { status: 200 },
    });
    renderScreen("select * from marts.fct_orders");
    await user.click(screen.getByRole("button", { name: "Run" }));
    const header = await screen.findByRole("button", { name: "amount" });
    const editor = EditorView.findFromDOM(document.querySelector(".cm-editor") as HTMLElement)!;
    act(() => editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: "select 99" } }));

    await user.click(header);

    await waitFor(() => expect(queryCalls()).toHaveLength(2));
    expect(queryCalls()[1]?.[1].body).toMatchObject({
      sql: "select * from marts.fct_orders",
      sort: [{ column: "amount", direction: "asc" }],
      filters: [],
    });
    expect(
      await screen.findByText("Truncated at 1,000 rows: sorting and filtering run on the server over all rows"),
    ).toBeInTheDocument();
  });

  describe("a server re-run", () => {
    const TRUNCATED = { data: { ...RESPONSE, truncated: true, returned_rows: 1000 }, response: { status: 200 } };

    // Unmounting a worksheet with a query in flight cancels it.
    beforeEach(() => post.mockImplementation(() => Promise.resolve({ response: { status: 204 } })));

    async function runTruncated(user: ReturnType<typeof userEvent.setup>, column = "amount") {
      renderScreen("select * from marts.fct_orders");
      await user.click(screen.getByRole("button", { name: "Run" }));
      await screen.findByRole("columnheader", { name: column });
    }

    async function addFilter(user: ReturnType<typeof userEvent.setup>, column: string, value: string) {
      await user.click(screen.getByRole("combobox", { name: "Filter column" }));
      await user.click(await screen.findByRole("option", { name: column }));
      await user.type(screen.getByRole("textbox", { name: "Filter value" }), value);
      await user.click(screen.getByRole("button", { name: "Add filter" }));
    }

    it("keeps the last rows, dimmed, and the filter bar while it runs", async () => {
      const user = userEvent.setup();
      let answer!: (value: unknown) => void;
      post.mockResolvedValueOnce(TRUNCATED);
      post.mockImplementationOnce(() => new Promise((resolve) => (answer = resolve)));
      await runTruncated(user);
      await user.click(screen.getByRole("combobox", { name: "Filter column" }));
      await user.click(await screen.findByRole("option", { name: "name" }));

      await user.click(screen.getByRole("button", { name: "amount" }));

      expect(firstColumn()).toEqual(["a", "b"]);
      expect(screen.getByRole("table").closest("[aria-busy]")).toHaveAttribute("aria-busy", "true");
      expect(screen.getByRole("combobox", { name: "Filter column" })).toHaveValue("name");
      expect(screen.getByRole("columnheader", { name: "amount" })).toHaveAttribute("aria-sort", "ascending");
      const sorted = { ...RESPONSE, mode: "server", truncated: true, returned_rows: 1000, rows: [["b", "9"], ["a", "10"]] };
      await act(async () => answer({ data: sorted, response: { status: 200 } }));
      expect(firstColumn()).toEqual(["b", "a"]);
      expect(screen.getByRole("columnheader", { name: "amount" })).toHaveAttribute("aria-sort", "ascending");
    });

    it("that fails shows its error above the last rows and drops the filter it tried", async () => {
      const user = userEvent.setup();
      const message = "a value could not be converted or computed (InvalidTextRepresentation); check the literal format";
      post.mockResolvedValueOnce(TRUNCATED);
      post.mockResolvedValueOnce({ error: { code: "sql_error", message }, response: { status: 422 } });
      await runTruncated(user);

      await addFilter(user, "name", "zzz");

      expect(await screen.findByText(message)).toBeInTheDocument();
      expect(firstColumn()).toEqual(["a", "b"]);
      expect(screen.queryByRole("button", { name: "Remove filter on name" })).not.toBeInTheDocument();
      expect(queryCalls()[1]?.[1].body.filters).toEqual([{ column: "name", operator: "=", value: "zzz" }]);
    });

    it("is never sent for a column the catalog marks as PII", async () => {
      const user = userEvent.setup();
      const withEmail = { ...TRUNCATED.data, columns: ["name", "EMAIL"], column_types: ["text", "text"] };
      post.mockResolvedValueOnce({ data: withEmail, response: { status: 200 } });
      await runTruncated(user, "EMAIL");

      await user.click(screen.getByRole("columnheader", { name: "EMAIL" }));

      expect(screen.queryByRole("button", { name: "EMAIL" })).not.toBeInTheDocument();
      expect(queryCalls()).toHaveLength(1);
      expect(screen.getByRole("columnheader", { name: "EMAIL" })).toHaveAttribute("aria-sort", "none");
    });

    it("is not a history entry: history records Runs", async () => {
      const user = userEvent.setup();
      post.mockResolvedValueOnce(TRUNCATED);
      post.mockResolvedValueOnce({ data: { ...TRUNCATED.data, mode: "server" }, response: { status: 200 } });
      await runTruncated(user);

      await user.click(screen.getByRole("button", { name: "amount" }));

      await waitFor(() => expect(queryCalls()).toHaveLength(2));
      expect(await screen.findByRole("button", { name: "History (1)" })).toBeInTheDocument();
    });
  });

  describe("a Run after a result", () => {
    const OTHER = { ...RESPONSE, columns: ["city", "code"], rows: [["x", "1"], ["y", "2"]] };

    beforeEach(() =>
      post.mockImplementation((path: string) =>
        path === "/api/queries" ? new Promise(() => {}) : Promise.resolve({ response: { status: 204 } }),
      ),
    );

    async function runTwice(user: ReturnType<typeof userEvent.setup>, second: Promise<unknown> = new Promise(() => {})) {
      post.mockResolvedValueOnce({ data: RESPONSE, response: { status: 200 } });
      post.mockReturnValueOnce(second);
      renderScreen("select 1");
      await user.click(screen.getByRole("button", { name: "Run" }));
      await screen.findByText("2 rows in 5 ms");
      await user.click(screen.getByRole("button", { name: "name" }));
      await user.click(screen.getByRole("button", { name: "Run" }));
    }

    it("keeps the last rows, dimmed, inert and in their order, and says it is running", async () => {
      const user = userEvent.setup();

      await runTwice(user);

      const grid = screen.getByRole("table", { hidden: true }).closest<HTMLElement>("[aria-busy]")!;
      expect(grid).toHaveAttribute("aria-busy", "true");
      expect(grid).toHaveAttribute("inert");
      expect(firstColumn()).toEqual(["a", "b"]);
      expect(screen.getByRole("status")).toHaveTextContent("Running… 0 s");
    });

    it("shows its answer with no sort, in a fresh grid", async () => {
      const user = userEvent.setup();

      await runTwice(user, Promise.resolve({ data: OTHER, response: { status: 200 } }));

      expect(await screen.findByRole("columnheader", { name: "code" })).toHaveAttribute("aria-sort", "none");
      expect(screen.getByRole("table").closest("[aria-busy]")).toHaveAttribute("aria-busy", "false");
      expect(screen.getByRole("table").closest("[inert]")).toBeNull();
    });

    it("that is cancelled shows a neutral notice and clears the rows", async () => {
      const user = userEvent.setup();
      await runTwice(user);

      await user.click(screen.getByRole("button", { name: "Cancel" }));

      expect(screen.getByRole("status")).toHaveTextContent("Cancelled after 0 s");
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
    });
  });

  it("a new Run starts with no sort or filter, which point at columns by position", async () => {
    const user = userEvent.setup();
    const other = { ...RESPONSE, columns: ["city", "code"], rows: [["x", "1"], ["y", "2"]] };
    post.mockResolvedValueOnce({ data: RESPONSE, response: { status: 200 } });
    post.mockResolvedValueOnce({ data: other, response: { status: 200 } });
    renderScreen("select 1");
    await user.click(screen.getByRole("button", { name: "Run" }));
    await user.click(await screen.findByRole("combobox", { name: "Filter column" }));
    await user.click(await screen.findByRole("option", { name: "amount" }));
    await user.type(screen.getByRole("textbox", { name: "Filter value" }), "10");
    await user.click(screen.getByRole("button", { name: "Add filter" }));
    await user.click(screen.getByRole("button", { name: "name" }));
    expect(firstColumn()).toEqual(["a"]);

    await user.click(screen.getByRole("button", { name: "Run" }));

    expect(await screen.findByRole("columnheader", { name: "code" })).toHaveAttribute("aria-sort", "none");
    expect(firstColumn()).toEqual(["x", "y"]);
    expect(screen.queryByRole("button", { name: /^Remove filter/ })).not.toBeInTheDocument();
  });

  it("history records a Run, and picking it puts its SQL into the active worksheet", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue({ data: RESPONSE, response: { status: 200 } });
    renderScreen("select 1");
    await user.click(screen.getByRole("button", { name: "Run" }));
    await screen.findByText("2 rows in 5 ms");
    await user.click(screen.getByRole("button", { name: "New worksheet" }));
    await user.click(screen.getByRole("button", { name: "History (1)" }));

    await user.click(await screen.findByRole("button", { name: /select 1.*2 rows/ }));

    expect(screen.getByRole("tab", { name: "Worksheet 2" })).toHaveAttribute("aria-selected", "true");
    expect(shownEditor().state.doc.toString()).toBe("select 1");
  });

  it("an untruncated result sorts in the browser without a request", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue({ data: RESPONSE, response: { status: 200 } });
    renderScreen("select 1");
    await user.click(screen.getByRole("button", { name: "Run" }));

    await user.click(await screen.findByRole("button", { name: "amount" }));

    expect(queryCalls()).toHaveLength(1);
  });

  it("Preview opens a worksheet and runs", async () => {
    const user = userEvent.setup();
    post.mockImplementation((path: string) =>
      path === "/api/queries"
        ? Promise.resolve({ data: RESPONSE, response: { status: 200 } })
        : Promise.resolve({ response: { status: 204 } }),
    );
    renderScreen("", true);

    await user.click(screen.getByRole("button", { name: "Actions for fct_orders" }));
    await user.click(await screen.findByRole("menuitem", { name: "Preview 100 rows" }));

    expect(await screen.findByRole("tab", { name: "fct_orders" })).toBeInTheDocument();
    await waitFor(() => expect(queryCalls()).toHaveLength(1));
    expect(queryCalls()[0]?.[1].body.sql).toBe("select * from marts.fct_orders limit 100");
    expect(await screen.findByText("2 rows in 5 ms")).toBeInTheDocument();
    // A later render must not run it again.
    await user.click(screen.getByRole("tab", { name: "Worksheet 1" }));
    expect(queryCalls()).toHaveLength(1);
    expect(post.mock.calls.filter(([path]) => path === "/api/queries/{query_id}/cancel")).toHaveLength(0);
  });
});
