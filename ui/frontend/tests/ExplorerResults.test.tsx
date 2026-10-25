import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ExplorerScreen } from "../src/screens/explorer/ExplorerScreen";
import { freshState, saveExplorerState, updateWorksheet } from "../src/screens/explorer/worksheetStore";

const post = vi.fn();
vi.mock("../src/api/client", () => ({ api: { POST: (...args: unknown[]) => post(...args) } }));
vi.mock("../src/api/queries", () => ({
  useCatalog: () => ({
    data: {
      database: "sherpa",
      schema_name: "marts",
      domains: [
        {
          name: "sales",
          tables: [
            { name: "fct_orders", domain: "sales", description: "", grain: "", primary_key: [], columns: [] },
          ],
        },
      ],
    },
  }),
  useTableDetails: () => ({ data: undefined }),
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

function renderScreen(sql: string, strict = false) {
  const seeded = freshState();
  saveExplorerState(updateWorksheet(seeded, seeded.activeId, { sql }));
  const screenUnderTest = (
    <MantineProvider env="test">
      <ExplorerScreen />
    </MantineProvider>
  );
  render(strict ? <StrictMode>{screenUnderTest}</StrictMode> : screenUnderTest);
}

describe("Explorer results", () => {
  beforeEach(() => {
    localStorage.clear();
    post.mockReset();
  });

  it("guard rejection shows the reason", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue({
      error: { code: "guard_rejected", message: "Only SELECT statements are allowed." },
      response: { status: 422 },
    });
    renderScreen("delete from marts.fct_orders");

    await user.click(screen.getByRole("button", { name: "Run" }));

    expect(await screen.findByText("Query not allowed")).toBeInTheDocument();
    expect(screen.getByText("Only SELECT statements are allowed.")).toBeInTheDocument();
  });

  it("a timeout shows its hint", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue({
      error: { code: "timeout", message: "Query exceeded 30 s." },
      response: { status: 408 },
    });
    renderScreen("select 1");

    await user.click(screen.getByRole("button", { name: "Run" }));

    expect(await screen.findByText("Query timed out")).toBeInTheDocument();
    expect(screen.getByText(/Narrow it with a WHERE clause or a LIMIT/)).toBeInTheDocument();
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

    await user.click(await screen.findByRole("columnheader", { name: "amount" }));

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

  it("an untruncated result sorts in the browser without a request", async () => {
    const user = userEvent.setup();
    post.mockResolvedValue({ data: RESPONSE, response: { status: 200 } });
    renderScreen("select 1");
    await user.click(screen.getByRole("button", { name: "Run" }));

    await user.click(await screen.findByRole("columnheader", { name: "amount" }));

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
