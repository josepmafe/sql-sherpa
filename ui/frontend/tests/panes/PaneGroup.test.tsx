import { MantineProvider } from "@mantine/core";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
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

async function renderPanes(): Promise<void> {
  render(<Panes />);
  await act(async () => {});
}

const valueNow = () => Number(screen.getByRole("separator", { name: "Resize the side" }).getAttribute("aria-valuenow"));
const stored = () => JSON.parse(localStorage.getItem(KEY) ?? "null") as unknown;

beforeEach(() => {
  localStorage.clear();
  stubPaneLayout();
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
    expect(stored()).toEqual({
      sizes: { side: 300, main: 900 },
      collapsed: { side: true },
    });
  });
});
