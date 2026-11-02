import { act, fireEvent, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
  orderNodes: () => Promise.resolve(new Map()),
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

const LAYOUT_KEY = "sherpa.v1.layout.shell";
const COLLAPSED_LAYOUT = JSON.stringify({ sizes: {}, collapsed: { nav: true } });
const collapseButton = () => screen.getByRole("button", { name: "Collapse the navigation" });
const openButton = () => screen.getByRole("button", { name: "Open the navigation" });
const storedFlag = () =>
  (JSON.parse(localStorage.getItem(LAYOUT_KEY) ?? "null") as { collapsed: { nav?: boolean } } | null)?.collapsed.nav;

describe("AppShell navigation rail", () => {
  beforeEach(() => {
    localStorage.clear();
    useCatalog.mockReset().mockReturnValue({ data: catalog() });
  });
  afterEach(() => localStorage.clear());

  it("shows an icon and a label for every item and keeps the link names", () => {
    renderAt("/lineage");

    for (const name of ["Explorer", "Lineage"]) {
      const link = screen.getByRole("link", { name });
      expect(link).toHaveTextContent(name);
      expect(link.querySelector("svg")).not.toBeNull();
    }
    expect(screen.getByRole("navigation")).toContainElement(collapseButton());
    expect(collapseButton()).toHaveAttribute("aria-expanded", "true");
  });

  it("collapses to the icons, keeps the link names and remembers the choice", () => {
    renderAt("/lineage");

    fireEvent.click(collapseButton());

    const link = screen.getByRole("link", { name: "Lineage" });
    expect(link).not.toHaveTextContent("Lineage");
    expect(link.querySelector("svg")).not.toBeNull();
    expect(openButton()).toHaveAttribute("aria-expanded", "false");
    expect(storedFlag()).toBe(true);
  });

  it("starts collapsed when the stored layout says so", () => {
    localStorage.setItem(LAYOUT_KEY, COLLAPSED_LAYOUT);

    renderAt("/lineage");

    expect(screen.getByRole("link", { name: "Lineage" })).not.toHaveTextContent("Lineage");
    expect(openButton()).toBeInTheDocument();
  });

  it("starts expanded when the stored layout is corrupt", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    localStorage.setItem(LAYOUT_KEY, "{not json");

    renderAt("/lineage");

    expect(screen.getByRole("link", { name: "Lineage" })).toHaveTextContent("Lineage");
    expect(collapseButton()).toBeInTheDocument();
  });

  it("opens the rail over the screen when a link takes focus and closes it on Escape", () => {
    localStorage.setItem(LAYOUT_KEY, COLLAPSED_LAYOUT);
    renderAt("/lineage");

    act(() => screen.getByRole("link", { name: "Explorer" }).focus());
    expect(screen.getByRole("link", { name: "Explorer" })).toHaveTextContent("Explorer");
    fireEvent.keyDown(screen.getByRole("navigation"), { key: "Escape" });

    expect(screen.getByRole("link", { name: "Explorer" })).not.toHaveTextContent("Explorer");
    expect(storedFlag()).toBe(true);
  });

  it("takes the Escape that closes its peek, so the screen under it does not see it", () => {
    localStorage.setItem(LAYOUT_KEY, COLLAPSED_LAYOUT);
    renderAt("/lineage");
    act(() => screen.getByRole("link", { name: "Explorer" }).focus());

    const notPrevented = fireEvent.keyDown(screen.getByRole("navigation"), { key: "Escape" });

    expect(notPrevented).toBe(false);
  });

  it("keeps a peeking rail open after focus leaves while it is pinned, until the pin is clicked again", () => {
    vi.useFakeTimers();
    try {
      localStorage.setItem(LAYOUT_KEY, COLLAPSED_LAYOUT);
      renderAt("/lineage");
      act(() => screen.getByRole("link", { name: "Explorer" }).focus());

      fireEvent.click(screen.getByRole("button", { name: "Pin the navigation open" }));
      act(() => (document.activeElement as HTMLElement).blur());
      act(() => void vi.advanceTimersByTime(1000));
      expect(screen.getByRole("link", { name: "Explorer" })).toHaveTextContent("Explorer");
      fireEvent.click(screen.getByRole("button", { name: "Unpin the navigation" }));

      expect(screen.getByRole("link", { name: "Explorer" })).not.toHaveTextContent("Explorer");
    } finally {
      vi.useRealTimers();
    }
  });

  it("offers no pin while the rail is expanded", () => {
    renderAt("/lineage");

    act(() => screen.getByRole("link", { name: "Explorer" }).focus());

    expect(screen.queryByRole("button", { name: "Pin the navigation open" })).not.toBeInTheDocument();
  });
});

describe("AppShell navigation rail peek and clicks", () => {
  beforeEach(() => {
    localStorage.clear();
    useCatalog.mockReset().mockReturnValue({ data: catalog() });
  });
  afterEach(() => localStorage.clear());

  it("closes again after a link is clicked during a hover peek and the pointer leaves", () => {
    vi.useFakeTimers();
    try {
      localStorage.setItem(LAYOUT_KEY, COLLAPSED_LAYOUT);
      renderAt("/lineage");
      const rail = screen.getByRole("navigation");
      fireEvent.pointerEnter(rail);
      act(() => void vi.advanceTimersByTime(200));
      const link = screen.getByRole("link", { name: "Explorer" });
      expect(link).toHaveTextContent("Explorer");

      act(() => link.focus()); // a mouse press focuses the link
      fireEvent.click(link, { detail: 1 });
      fireEvent.pointerLeave(rail);
      act(() => void vi.advanceTimersByTime(400));

      expect(screen.getByRole("link", { name: "Explorer" })).not.toHaveTextContent("Explorer");
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps the rail open after Enter on a focused link and closes it on Escape", () => {
    localStorage.setItem(LAYOUT_KEY, COLLAPSED_LAYOUT);
    renderAt("/lineage");
    const link = screen.getByRole("link", { name: "Explorer" });
    act(() => link.focus());

    fireEvent.click(link, { detail: 0 }); // Enter on a link is a click without a pointer
    expect(screen.getByRole("link", { name: "Explorer" })).toHaveTextContent("Explorer");
    fireEvent.keyDown(document.body, { key: "Escape" });

    expect(screen.getByRole("link", { name: "Explorer" })).not.toHaveTextContent("Explorer");
  });

  it("remembers the rail as expanded after it is opened again", () => {
    localStorage.setItem(LAYOUT_KEY, COLLAPSED_LAYOUT);
    renderAt("/lineage");

    fireEvent.click(openButton());

    expect(storedFlag()).toBe(false);
    expect(screen.getByRole("link", { name: "Lineage" })).toHaveTextContent("Lineage");
  });
});
