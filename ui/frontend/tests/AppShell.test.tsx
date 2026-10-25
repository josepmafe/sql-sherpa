import { MantineProvider } from "@mantine/core";
import { render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { routes } from "../src/app/routes";

vi.mock("../src/api/queries", () => ({
  useHealth: () => ({ data: { database: "ok", artifacts: { status: "ok", generated_at: null } } }),
  useCatalog: () => ({ data: { database: "sherpa", schema_name: "marts", domains: [] } }),
  useTableDetails: () => ({ data: undefined }),
}));

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return render(
    <MantineProvider>
      <RouterProvider router={router} />
    </MantineProvider>,
  );
}

describe("AppShell", () => {
  it("shows only existing screens", () => {
    renderAt("/explorer");

    const nav = screen.getByRole("navigation");
    const links = within(nav).getAllByRole("link");

    expect(links.map((link) => link.textContent)).toEqual(["Explorer"]);
  });

  it("redirects / to the Explorer", () => {
    renderAt("/");

    expect(screen.getByLabelText("Search tables and columns")).toBeInTheDocument();
  });
});
