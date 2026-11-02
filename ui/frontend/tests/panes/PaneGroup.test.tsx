import { MantineProvider } from "@mantine/core";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CollapseToggle, Pane, PaneGroup, PaneHandle, usePaneCollapse } from "../../src/components/panes";
import { stubPaneLayout } from "../fixtures/panes";

const KEY = "sherpa.v1.layout.test";

function Toggle() {
  const { collapsed, toggle } = usePaneCollapse("side");
  return <CollapseToggle collapsed={collapsed} onToggle={toggle} foldLabel="Fold the side" openLabel="Open the side" />;
}

function Panes() {
  return (
    <MantineProvider env="test">
      <PaneGroup id="test" direction="horizontal">
        <Pane id="side" defaultSize={300} minSize={200} maxSize={400} collapsedSize={48}>
          <Toggle />
        </Pane>
        <PaneHandle label="Resize the side" />
        <Pane id="main" minSize={200} maxSize={2000}>
          <div>main</div>
        </Pane>
      </PaneGroup>
    </MantineProvider>
  );
}

function ExtraToggle() {
  const { collapsed, toggle } = usePaneCollapse("extra");
  return (
    <CollapseToggle collapsed={collapsed} onToggle={toggle} foldLabel="Fold the extra" openLabel="Open the extra" />
  );
}

// A pane that comes and goes, like the Explorer's Inspector across 1280 px.
function PanesWithExtra({ present }: { present: boolean }) {
  return (
    <MantineProvider env="test">
      <PaneGroup id="test" direction="horizontal">
        <Pane id="main" minSize={200} maxSize={2000}>
          <div>main</div>
        </Pane>
        {present && (
          <>
            <PaneHandle label="Resize the extra" />
            <Pane id="extra" defaultSize={300} minSize={200} maxSize={500} collapsedSize={48}>
              <ExtraToggle />
            </Pane>
          </>
        )}
      </PaneGroup>
    </MantineProvider>
  );
}

async function renderPanes(): Promise<void> {
  render(<Panes />);
  await act(async () => {});
}

const valueNow = () => Number(screen.getByRole("separator", { name: "Resize the side" }).getAttribute("aria-valuenow"));
const stored = () => JSON.parse(localStorage.getItem(KEY) ?? "null") as unknown;

beforeEach(() => {
  localStorage.clear();
  stubPaneLayout({ pointerOnHandles: true });
});

describe("PaneGroup", () => {
  it("starts at the default size when nothing is stored", async () => {
    await renderPanes();

    expect(valueNow()).toBeCloseTo((300 / 1200) * 100, 0);
  });

  it("starts at the stored size, clamped to the pane's maximum", async () => {
    localStorage.setItem(KEY, JSON.stringify({ sizes: { side: 5000 }, collapsed: {} }));

    await renderPanes();

    expect(valueNow()).toBeCloseTo((400 / 1200) * 100, 0);
  });

  it("starts at the default size when the stored value is not a layout", async () => {
    localStorage.setItem(KEY, '{"x":');

    await renderPanes();

    expect(valueNow()).toBeCloseTo((300 / 1200) * 100, 0);
  });

  it("stores the size after a key press, and only sizes and flags", async () => {
    const user = userEvent.setup();
    await renderPanes();

    screen.getByRole("separator", { name: "Resize the side" }).focus();
    await user.keyboard("{ArrowRight}");

    const layout = stored() as {
      sizes: Record<string, number>;
      collapsed: Record<string, boolean>;
    };
    expect(Object.keys(layout).sort()).toEqual(["collapsed", "sizes"]);
    expect(layout.sizes.side).toBeGreaterThan(300);
    expect(layout.collapsed).toEqual({ side: false });
  });

  it("starts folded when the stored layout says so", async () => {
    localStorage.setItem(KEY, JSON.stringify({ sizes: { side: 300 }, collapsed: { side: true } }));

    await renderPanes();

    expect(screen.getByRole("button", { name: "Open the side" })).toHaveAttribute("aria-expanded", "false");
  });

  it("folds on the toggle and remembers it with the size it will open back to", async () => {
    const user = userEvent.setup();
    await renderPanes();

    await user.click(screen.getByRole("button", { name: "Fold the side" }));
    expect(screen.getByRole("button", { name: "Open the side" })).toHaveAttribute("aria-expanded", "false");

    const layout = stored() as { sizes: { side: number }; collapsed: { side: boolean } };
    expect(layout.sizes.side).toBe(300);
    expect(layout.collapsed.side).toBe(true);
  });

  it("opens back to the size it had and remembers that size", async () => {
    const user = userEvent.setup();
    await renderPanes();
    await user.click(screen.getByRole("button", { name: "Fold the side" }));

    await user.click(screen.getByRole("button", { name: "Open the side" }));

    expect(screen.getByRole("button", { name: "Fold the side" })).toHaveAttribute("aria-expanded", "true");
    expect(valueNow()).toBeCloseTo((300 / 1200) * 100, 0);
    const layout = stored() as { sizes: { side: number }; collapsed: { side: boolean } };
    expect(layout.sizes.side).toBe(300);
    expect(layout.collapsed.side).toBe(false);
  });

  it("remembers the default sizes after a double-click on the handle", async () => {
    const user = userEvent.setup();
    await renderPanes();
    const handle = screen.getByRole("separator", { name: "Resize the side" });
    handle.focus();
    await user.keyboard("{ArrowRight}");
    expect((stored() as { sizes: { side: number } }).sizes.side).toBeGreaterThan(300);

    await user.dblClick(handle);

    expect((stored() as { sizes: { side: number } }).sizes.side).toBeCloseTo(300, 0);
  });

  it("stores sizes only for the panes that declared a default size", async () => {
    const user = userEvent.setup();
    await renderPanes();

    screen.getByRole("separator", { name: "Resize the side" }).focus();
    await user.keyboard("{ArrowRight}");

    expect(Object.keys((stored() as { sizes: object }).sizes)).toEqual(["side"]);
  });

  it("ignores a stored size for a pane that declared no default size", async () => {
    localStorage.setItem(KEY, JSON.stringify({ sizes: { side: 300, main: 1900 }, collapsed: {} }));

    await renderPanes();

    expect(valueNow()).toBeCloseTo((300 / 1200) * 100, 0);
  });

  it("warns once for a stored value that is not a layout, however many panes the group has", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    localStorage.setItem(KEY, '{"x":');

    await renderPanes();

    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("keeps the stored size and flag of a pane that is not in the group now", async () => {
    const user = userEvent.setup();
    localStorage.setItem(KEY, JSON.stringify({ sizes: { side: 300, extra: 250 }, collapsed: { extra: true } }));
    await renderPanes();

    screen.getByRole("separator", { name: "Resize the side" }).focus();
    await user.keyboard("{ArrowRight}");

    const layout = stored() as { sizes: Record<string, number>; collapsed: Record<string, boolean> };
    expect(layout.sizes.side).toBeGreaterThan(300);
    expect(layout.sizes.extra).toBe(250);
    expect(layout.collapsed.extra).toBe(true);
  });

  it("brings back a pane that left the group folded, with the size the user gave it", async () => {
    const user = userEvent.setup();
    const view = render(<PanesWithExtra present />);
    await act(async () => {});
    screen.getByRole("separator", { name: "Resize the extra" }).focus();
    await user.keyboard("{ArrowLeft}");
    const dragged = (stored() as { sizes: { extra: number } }).sizes.extra;
    await user.click(screen.getByRole("button", { name: "Fold the extra" }));

    view.rerender(<PanesWithExtra present={false} />);
    await act(async () => {});
    view.rerender(<PanesWithExtra present />);
    await act(async () => {});

    expect(dragged).toBeGreaterThan(300);
    expect((stored() as { collapsed: { extra: boolean } }).collapsed.extra).toBe(true);
    await user.click(screen.getByRole("button", { name: "Open the extra" }));
    expect((stored() as { sizes: { extra: number } }).sizes.extra).toBe(dragged);
  });

  it("gives a group and its panes the DOM ids it is told, and stores under the layout ids", async () => {
    const user = userEvent.setup();
    render(
      <MantineProvider env="test">
        <PaneGroup id="test" domId="test-one" direction="horizontal">
          <Pane id="side" domId="side-one" defaultSize={300} minSize={200} maxSize={400}>
            <div>side</div>
          </Pane>
          <PaneHandle label="Resize the side" />
          <Pane id="main" domId="main-one" minSize={200} maxSize={2000}>
            <div>main</div>
          </Pane>
        </PaneGroup>
      </MantineProvider>,
    );
    await act(async () => {});

    expect(document.querySelector("[data-group]")).toHaveAttribute("id", "test-one");
    expect(Array.from(document.querySelectorAll("[data-panel]"), (panel) => panel.id)).toEqual(["side-one", "main-one"]);
    screen.getByRole("separator", { name: "Resize the side" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(Object.keys((stored() as { sizes: object }).sizes)).toEqual(["side"]);
  });
});
