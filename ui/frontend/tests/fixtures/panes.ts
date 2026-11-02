import { vi } from "vitest";

export const PANE_GROUP_WIDTH = 1200;
const HANDLE_RECT = new DOMRect(-10_000, -10_000, 1, 1);

/**
 * jsdom has no layout, so a pane group would measure nothing. This gives every group PANE_GROUP_WIDTH px (and height)
 * and each pane its share of that (its flex weight over the sum), and has every ResizeObserver report what it observes once, in a microtask (`await act(async () => {})` after render). Undone after
 * each test (`unstubGlobals`, `restoreMocks`).
 */
export function stubPaneLayout({ pointerOnHandles = false }: { pointerOnHandles?: boolean } = {}): void {
  const size = (element: HTMLElement): number => {
    if (element.hasAttribute("data-group")) return PANE_GROUP_WIDTH;
    if (element.hasAttribute("data-panel")) return (panelWeight(element) / siblingWeights(element)) * PANE_GROUP_WIDTH;
    return 0;
  };
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockImplementation(function (this: HTMLElement) {
    return size(this);
  });
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function (this: HTMLElement) {
    return size(this);
  });
  // The library orders a group's children by their offsets: DOM order will do.
  const indexOf = (element: HTMLElement): number => Array.from(element.parentElement?.children ?? []).indexOf(element);
  vi.spyOn(HTMLElement.prototype, "offsetLeft", "get").mockImplementation(function (this: HTMLElement) {
    return indexOf(this);
  });
  vi.spyOn(HTMLElement.prototype, "offsetTop", "get").mockImplementation(function (this: HTMLElement) {
    return indexOf(this);
  });
  // Every rectangle is 0 x 0 at (0, 0), where a test's pointer is: the library would take each click for a press on a
  // handle (and cancel it, so the target never gets the focus). The handles sit far away instead, unless the test
  // double-clicks a handle, which the library only sees where the pointer is.
  if (!pointerOnHandles) {
    const rectangle = Element.prototype.getBoundingClientRect;
    vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
      return this.getAttribute("role") === "separator" ? HANDLE_RECT : rectangle.call(this);
    });
  }
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(private readonly callback: ResizeObserverCallback) {}
      observe(target: Element): void {
        const borderBoxSize = [{ inlineSize: 0, blockSize: 0 }] as unknown as ReadonlyArray<ResizeObserverSize>;
        // After the library has registered its group, as a browser does.
        queueMicrotask(() =>
          this.callback([{ target, borderBoxSize, contentRect: new DOMRect() } as ResizeObserverEntry], this as unknown as ResizeObserver),
        );
      }
      unobserve(): void {}
      disconnect(): void {}
    },
  );
}

// A panel's `flex: <weight> 1 0px` is its share of the group, not a percentage.
function panelWeight(panel: Element): number {
  return Number.parseFloat((panel as HTMLElement).style.flexGrow) || 0;
}

function siblingWeights(panel: Element): number {
  const panels = Array.from(panel.parentElement?.children ?? []).filter((child) => child.hasAttribute("data-panel"));
  return panels.reduce((total, sibling) => total + panelWeight(sibling), 0) || 1;
}
