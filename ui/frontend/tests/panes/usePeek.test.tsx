import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { usePeek } from "../../src/components/panes/usePeek";

function Strip({ collapsed }: { collapsed: boolean }) {
  const { peeking, peekProps } = usePeek(collapsed);
  return (
    <div data-testid="strip" {...peekProps}>
      <button>inside</button>
      <button>other</button>
      <span>{peeking ? "open" : "closed"}</span>
    </div>
  );
}

const isOpen = () => screen.queryByText("open") !== null;
const advance = (ms: number) => act(() => void vi.advanceTimersByTime(ms));

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("usePeek", () => {
  it("opens after the pointer has been on the strip for 200 ms, not before", () => {
    render(<Strip collapsed />);

    fireEvent.pointerEnter(screen.getByTestId("strip"));
    advance(199);
    expect(isOpen()).toBe(false);
    advance(1);

    expect(isOpen()).toBe(true);
  });

  it("opens at once on focus", () => {
    render(<Strip collapsed />);

    fireEvent.focus(screen.getByText("inside"));

    expect(isOpen()).toBe(true);
  });

  it("closes 300 ms after the pointer leaves, not before", () => {
    render(<Strip collapsed />);
    fireEvent.pointerEnter(screen.getByTestId("strip"));
    advance(200);

    fireEvent.pointerLeave(screen.getByTestId("strip"));
    advance(299);
    expect(isOpen()).toBe(true);
    advance(1);

    expect(isOpen()).toBe(false);
  });

  it("stays open when the pointer comes back inside the 300 ms", () => {
    render(<Strip collapsed />);
    const strip = screen.getByTestId("strip");
    fireEvent.pointerEnter(strip);
    advance(200);

    fireEvent.pointerLeave(strip);
    advance(250);
    fireEvent.pointerEnter(strip);
    advance(1000);

    expect(isOpen()).toBe(true);
  });

  it("stays open while focus is inside after the pointer left, and closes 300 ms after focus leaves", () => {
    render(<Strip collapsed />);
    const strip = screen.getByTestId("strip");
    fireEvent.focus(screen.getByText("inside"));
    fireEvent.pointerEnter(strip);
    fireEvent.pointerLeave(strip);
    advance(1000);
    expect(isOpen()).toBe(true);

    fireEvent.blur(screen.getByText("inside"), {
      relatedTarget: document.body,
    });
    advance(299);
    expect(isOpen()).toBe(true);
    advance(1);

    expect(isOpen()).toBe(false);
  });

  it("does not close when focus moves between two elements inside the strip", () => {
    render(<Strip collapsed />);
    fireEvent.focus(screen.getByText("inside"));

    fireEvent.blur(screen.getByText("inside"), {
      relatedTarget: screen.getByText("other"),
    });
    advance(1000);

    expect(isOpen()).toBe(true);
  });

  it("never opens when not collapsed", () => {
    render(<Strip collapsed={false} />);

    fireEvent.pointerEnter(screen.getByTestId("strip"));
    fireEvent.focus(screen.getByText("inside"));
    advance(1000);

    expect(isOpen()).toBe(false);
  });

  it("closes when the pane is expanded while peeking", () => {
    const { rerender } = render(<Strip collapsed />);
    fireEvent.focus(screen.getByText("inside"));

    rerender(<Strip collapsed={false} />);

    expect(isOpen()).toBe(false);
  });

  it("leaves no timer behind when unmounted", () => {
    const { unmount } = render(<Strip collapsed />);
    fireEvent.pointerEnter(screen.getByTestId("strip"));

    unmount();

    expect(vi.getTimerCount()).toBe(0);
  });
});
