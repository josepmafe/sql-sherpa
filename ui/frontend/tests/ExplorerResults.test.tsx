import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ExplorerScreen } from "../src/screens/explorer/ExplorerScreen";
import { freshState, saveExplorerState, updateWorksheet } from "../src/screens/explorer/worksheetStore";
import { apiFailure, catalog, columnSummary, noContent, ok, queryResponse, table } from "./fixtures/api";
import { firstColumn, renderWithProviders, shownEditor } from "./fixtures/render";

const post = vi.fn();
vi.mock("../src/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api/client")>()),
  api: { POST: (...args: unknown[]) => post(...args) },
}));
const CATALOG = catalog({
  domains: [
    {
      name: "sales",
      tables: [
        table({ name: "fct_orders" }),
        table({ name: "dim_customers", columns: [columnSummary({ name: "email", is_pii: true })] }),
      ],
    },
  ],
});
vi.mock("../src/api/queries", () => ({
  useCatalog: () => ({ data: CATALOG }),
  useTableDetails: () => ({ data: undefined }),
  useHealth: () => ({ data: undefined }),
}));

const RESPONSE = queryResponse({
  columns: ["name", "amount"],
  column_types: ["text", "number"],
  rows: [
    ["a", "10"],
    ["b", "9"],
  ],
});

const queryCalls = () => post.mock.calls.filter(([path]) => path === "/api/queries");

function renderScreen(sql: string, strict = false) {
  const seeded = freshState();
  saveExplorerState(updateWorksheet(seeded, seeded.activeId, { sql }));
  const screenUnderTest = <ExplorerScreen />;
  renderWithProviders(strict ? <StrictMode>{screenUnderTest}</StrictMode> : screenUnderTest);
}

describe("Explorer results", () => {
  beforeEach(() => {
    localStorage.clear();
    post.mockReset();
  });

  it("guard rejection shows the reason without the rule's code, and what is allowed", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue(
      apiFailure(
        "guard_rejected",
        "[guard:not_select] the statement must be a SELECT query, not DELETE; write one SELECT",
        422,
      ),
    );
    renderScreen("delete from marts.fct_orders");

    await user.click(screen.getByRole("button", { name: "Run" }));

    const alert = (await screen.findByText("Query not allowed")).closest<HTMLElement>('[role="alert"]')!;
    expect(alert).toHaveTextContent("the statement must be a SELECT query, not DELETE; write one SELECT");
    expect(alert).not.toHaveTextContent("[guard:");
    expect(alert).toHaveTextContent("Only one read-only SELECT (or WITH … SELECT) over the marts.<table> tables runs here.");
  });

  it("a timeout shows its hint", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue(apiFailure("timeout", "Query exceeded 30 s.", 504));
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
    post.mockResolvedValue(apiFailure(code, "the server's reason", status));
    renderScreen("select 1");

    await user.click(screen.getByRole("button", { name: "Run" }));

    expect(await screen.findByText(title)).toBeInTheDocument();
    expect(screen.getByText("the server's reason")).toBeInTheDocument();
  });

  it("truncated results sort on the server", async () => {
    const user = userEvent.setup();
    post.mockResolvedValueOnce(ok(queryResponse({ ...RESPONSE, truncated: true, returned_rows: 1000 })));
    post.mockResolvedValueOnce(ok(queryResponse({ ...RESPONSE, mode: "server", truncated: true, returned_rows: 1000 })));
    renderScreen("select * from marts.fct_orders");
    await user.click(screen.getByRole("button", { name: "Run" }));
    const header = await screen.findByRole("button", { name: "amount" });
    const editor = shownEditor();
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
    const TRUNCATED_DATA = queryResponse({ ...RESPONSE, truncated: true, returned_rows: 1000 });
    const TRUNCATED = ok(TRUNCATED_DATA);

    // Unmounting a worksheet with a query in flight cancels it.
    beforeEach(() => post.mockImplementation(() => Promise.resolve(noContent())));

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
      const sorted = queryResponse({
        ...RESPONSE,
        mode: "server",
        truncated: true,
        returned_rows: 1000,
        rows: [["b", "9"], ["a", "10"]],
      });
      await act(async () => answer(ok(sorted)));
      expect(firstColumn()).toEqual(["b", "a"]);
      expect(screen.getByRole("columnheader", { name: "amount" })).toHaveAttribute("aria-sort", "ascending");
    });

    it("that fails shows its error above the last rows and drops the filter it tried", async () => {
      const user = userEvent.setup();
      const message = "a value could not be converted or computed (InvalidTextRepresentation); check the literal format";
      post.mockResolvedValueOnce(TRUNCATED);
      post.mockResolvedValueOnce(apiFailure("sql_error", message, 422));
      await runTruncated(user);

      await addFilter(user, "name", "zzz");

      expect(await screen.findByText(message)).toBeInTheDocument();
      expect(firstColumn()).toEqual(["a", "b"]);
      expect(screen.queryByRole("button", { name: "Remove filter on name" })).not.toBeInTheDocument();
      expect(queryCalls()[1]?.[1].body.filters).toEqual([{ column: "name", operator: "=", value: "zzz" }]);
    });

    it("is never sent for a column the catalog marks as PII", async () => {
      const user = userEvent.setup();
      const withEmail = queryResponse({ ...TRUNCATED_DATA, columns: ["name", "EMAIL"], column_types: ["text", "text"] });
      post.mockResolvedValueOnce(ok(withEmail));
      await runTruncated(user, "EMAIL");

      await user.click(screen.getByRole("columnheader", { name: "EMAIL" }));

      expect(screen.queryByRole("button", { name: "EMAIL" })).not.toBeInTheDocument();
      expect(queryCalls()).toHaveLength(1);
      expect(screen.getByRole("columnheader", { name: "EMAIL" })).toHaveAttribute("aria-sort", "none");
    });

    it("is not a history entry: history records Runs", async () => {
      const user = userEvent.setup();
      post.mockResolvedValueOnce(TRUNCATED);
      post.mockResolvedValueOnce(ok(queryResponse({ ...TRUNCATED_DATA, mode: "server" })));
      await runTruncated(user);

      await user.click(screen.getByRole("button", { name: "amount" }));

      await waitFor(() => expect(queryCalls()).toHaveLength(2));
      expect(await screen.findByRole("button", { name: "History (1)" })).toBeInTheDocument();
    });
  });

  describe("a Run after a result", () => {
    const OTHER = queryResponse({ ...RESPONSE, columns: ["city", "code"], rows: [["x", "1"], ["y", "2"]] });

    beforeEach(() =>
      post.mockImplementation((path: string) =>
        path === "/api/queries" ? new Promise(() => {}) : Promise.resolve(noContent()),
      ),
    );

    async function runTwice(user: ReturnType<typeof userEvent.setup>, second: Promise<unknown> = new Promise(() => {})) {
      post.mockResolvedValueOnce(ok(RESPONSE));
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

      await runTwice(user, Promise.resolve(ok(OTHER)));

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
    const other = queryResponse({ ...RESPONSE, columns: ["city", "code"], rows: [["x", "1"], ["y", "2"]] });
    post.mockResolvedValueOnce(ok(RESPONSE));
    post.mockResolvedValueOnce(ok(other));
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
    post.mockResolvedValue(ok(RESPONSE));
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
    post.mockResolvedValue(ok(RESPONSE));
    renderScreen("select 1");
    await user.click(screen.getByRole("button", { name: "Run" }));

    await user.click(await screen.findByRole("button", { name: "amount" }));

    expect(queryCalls()).toHaveLength(1);
  });

  it("Preview opens a worksheet and runs", async () => {
    const user = userEvent.setup();
    post.mockImplementation((path: string) =>
      path === "/api/queries" ? Promise.resolve(ok(RESPONSE)) : Promise.resolve(noContent()),
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
