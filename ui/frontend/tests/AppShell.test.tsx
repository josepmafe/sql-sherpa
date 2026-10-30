import { MantineProvider } from "@mantine/core";
import { render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { routes } from "../src/app/routes";

const useCatalog = vi.fn();
vi.mock("../src/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api/queries")>()),
  useHealth: () => ({ data: { database: "ok", artifacts: { status: "ok", generated_at: null } } }),
  useCatalog: () => useCatalog(),
  useTableDetails: () => ({ data: undefined }),
  useLineage: () => ({ error: null, data: { nodes: [], edges: [], layers_available: [], build: { state: "ok" } } }),
}));

vi.mock("../src/screens/lineage/layout", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/screens/lineage/layout")>()),
  layoutGraph: () => Promise.resolve(new Map()),
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
  beforeEach(() => {
    useCatalog.mockReset().mockReturnValue({ data: { database: "sherpa", schema_name: "marts", domains: [] } });
  });

  it("shows only existing screens", () => {
    renderAt("/explorer");

    const nav = screen.getByRole("navigation");
    const links = within(nav).getAllByRole("link");

    expect(links.map((link) => link.textContent)).toEqual(["Explorer", "Lineage"]);
  });

  it("shows Lineage", () => {
    renderAt("/lineage");

    expect(screen.getByRole("link", { name: "Lineage" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("0 nodes")).toBeInTheDocument();
  });

  it("redirects / to the Explorer", () => {
    renderAt("/");

    expect(screen.getByLabelText("Search tables and columns")).toBeInTheDocument();
  });

  it("the Explorer opens when the browser blocks localStorage", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const getter = vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });

    try {
      renderAt("/explorer");

      expect(screen.getByRole("tab", { name: "Worksheet 1" })).toBeInTheDocument();
      expect(screen.queryByText("Something went wrong")).not.toBeInTheDocument();
    } finally {
      getter.mockRestore();
      warn.mockRestore();
    }
  });

  it("a screen that crashes shows the app's error page with the reason", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    useCatalog.mockImplementation(() => {
      throw new Error("boom");
    });

    try {
      renderAt("/explorer");

      expect(screen.getByRole("heading", { name: "Something went wrong" })).toBeInTheDocument();
      expect(screen.getByText("boom")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
    } finally {
      consoleError.mockRestore();
    }
  });
});
