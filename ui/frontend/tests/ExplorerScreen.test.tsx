import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ExplorerScreen } from "../src/screens/explorer/ExplorerScreen";
import { freshState, saveExplorerState, updateWorksheet } from "../src/screens/explorer/worksheetStore";

const post = vi.fn();
vi.mock("../src/api/client", () => ({ api: { POST: (...args: unknown[]) => post(...args) } }));
vi.mock("../src/api/queries", () => ({
  useCatalog: () => ({ data: { database: "sherpa", schema_name: "marts", domains: [] } }),
  useTableDetails: () => ({ data: undefined }),
}));

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
    render(
      <MantineProvider env="test">
        <ExplorerScreen />
      </MantineProvider>,
    );
    await user.click(screen.getByRole("button", { name: "Run" }));

    await user.click(screen.getByRole("button", { name: "New worksheet" }));
    await user.click(screen.getByRole("tab", { name: "Worksheet 1" }));

    expect(post.mock.calls.filter(([path]) => path === "/api/queries")).toHaveLength(1);
    expect(post.mock.calls.filter(([path]) => path === "/api/queries/{query_id}/cancel")).toHaveLength(0);
    expect(screen.getAllByRole("button", { name: "Cancel" })[0]).toBeInTheDocument();
  });
});
