import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { routes } from "../src/app/routes";
import { catalog, health, lineageGraph } from "./fixtures/api";
import { renderRoutes } from "./fixtures/render";

const useCatalog = vi.fn();
vi.mock("../src/api/queries", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/api/queries")>()),
  useHealth: () => ({ data: health() }),
  useCatalog: () => useCatalog(),
  useTableDetails: () => ({ data: undefined }),
  useLineage: () => ({ error: null, data: lineageGraph() }),
}));

vi.mock("../src/screens/lineage/layout", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../src/screens/lineage/layout")>()),
  layoutGraph: () => Promise.resolve(new Map()),
}));

function renderAt(path: string) {
  return renderRoutes(routes, { path });
}

describe("AppShell", () => {
  beforeEach(() => {
    useCatalog.mockReset().mockReturnValue({ data: catalog() });
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
