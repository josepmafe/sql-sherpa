import { MantineProvider } from "@mantine/core";
import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { Pane, PaneGroup, PaneHandle } from "../../src/components/panes";
import { stubPaneLayout } from "../fixtures/panes";

function Panes() {
  return (
    <MantineProvider env="test">
      <PaneGroup id="test" direction="horizontal">
        <Pane id="left" defaultSize={300} minSize={200} maxSize={400}>
          <div>left</div>
        </Pane>
        <PaneHandle label="Resize the left pane" />
        <Pane id="right" minSize={200} maxSize={2000}>
          <div>right</div>
        </Pane>
      </PaneGroup>
    </MantineProvider>
  );
}

async function renderPanes(): Promise<void> {
  render(<Panes />);
  await act(async () => {});
}

const handle = () => screen.getByRole("separator", { name: "Resize the left pane" });
const now = () => Number(handle().getAttribute("aria-valuenow"));

beforeEach(() => {
  localStorage.clear();
  stubPaneLayout({ pointerOnHandles: true });
});

describe("PaneHandle", () => {
  it("is a separator named by its label", async () => {
    await renderPanes();

    expect(handle()).toBeInTheDocument();
  });

  it("moves by one step with the arrow keys and stays inside the limits", async () => {
    const user = userEvent.setup();
    await renderPanes();
    const start = now();

    handle().focus();
    await user.keyboard("{ArrowRight}");
    const right = now();
    await user.keyboard("{ArrowLeft}{ArrowLeft}");
    const left = now();

    expect(right).toBeGreaterThan(start);
    expect(left).toBeLessThan(start);
    expect(right - start).toBeCloseTo(start - left, 0);
    expect(right).toBeLessThanOrEqual(100);
    expect(left).toBeGreaterThan(0);
  });

  it("stops at the maximum and at the minimum", async () => {
    const user = userEvent.setup();
    await renderPanes();
    handle().focus();

    await user.keyboard("{End}");
    const atMax = now();
    await user.keyboard("{ArrowRight}");
    const pastMax = now();
    await user.keyboard("{Home}");
    const atMin = now();
    await user.keyboard("{ArrowLeft}");
    const pastMin = now();

    expect(pastMax).toBe(atMax);
    expect(pastMin).toBe(atMin);
    expect(atMax).toBeCloseTo((400 / 1200) * 100, 0);
    expect(atMin).toBeCloseTo((200 / 1200) * 100, 0);
  });

  it("restores the default sizes on a double-click", async () => {
    const user = userEvent.setup();
    await renderPanes();
    const start = now();
    handle().focus();
    await user.keyboard("{ArrowRight}{ArrowRight}");
    expect(now()).not.toBe(start);

    fireEvent.doubleClick(handle());

    expect(now()).toBeCloseTo(start, 0);
  });
});
