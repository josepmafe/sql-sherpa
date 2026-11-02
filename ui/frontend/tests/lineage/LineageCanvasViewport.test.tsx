import { MantineProvider } from "@mantine/core";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { orderNodes } from "../../src/screens/lineage/layout";
import { forgetView, keepView } from "../../src/screens/lineage/viewport";
import { columnView, type LineageEdge } from "../fixtures/api";
import { stubLayout } from "../fixtures/render";
import { canvasProps, node, rankInInputOrder } from "./canvasHarness";
import { LineageCanvas } from "../../src/screens/lineage/LineageCanvas";

vi.mock("../../src/screens/lineage/layout", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../src/screens/lineage/layout")>()),
  orderNodes: vi.fn(),
}));

// The user's zoom and pan, as the kept view holds them: 0.6x, panned a little.
const KEPT = { x: -37, y: 12, zoom: 0.6 };
const KEPT_TRANSFORM = "translate(-37px,12px) scale(0.6)";
const SUBJECT = "+b+";
const A = node("a", { columns: [columnView({ name: "id" })] });
const B = node("b", { layer: "marts" });
const C = node("c", { layer: "marts" });
const EDGES: LineageEdge[] = [{ source: A.unique_id, target: B.unique_id }];

const transform = () => (document.querySelector(".react-flow__viewport") as HTMLElement).style.transform;
const buttons = () => screen.findAllByRole("button", { name: /^[abc]\b/ });

function canvas(overrides: Parameters<typeof canvasProps>[0] = {}) {
  return (
    <MantineProvider env="test">
      <LineageCanvas {...canvasProps({ nodes: [A, B], edges: EDGES, subject: SUBJECT, ...overrides })} />
    </MantineProvider>
  );
}

describe("the Lineage viewport across redraws", () => {
  beforeEach(() => {
    rankInInputOrder();
    forgetView();
    stubLayout();
  });

  it("the first layout of a subject fits the graph", async () => {
    render(canvas());
    await buttons();

    await waitFor(() => expect(transform()).not.toMatch(/translate\(0px,0px\)/));
    expect(transform()).not.toBe(KEPT_TRANSFORM);
  });

  it("keeps the zoom and pan when a layer is switched on (more nodes)", async () => {
    keepView(SUBJECT, KEPT);
    const view = render(canvas());
    await waitFor(() => expect(transform()).toBe(KEPT_TRANSFORM));

    view.rerender(canvas({ nodes: [A, B, C] }));
    await waitFor(() => expect(screen.getByRole("button", { name: /^c\b/ })).toBeVisible());

    expect(transform()).toBe(KEPT_TRANSFORM);
  });

  it("keeps the zoom and pan when a layer is switched off or Failing only drops nodes", async () => {
    keepView(SUBJECT, KEPT);
    const view = render(canvas({ nodes: [A, B, C] }));
    await waitFor(() => expect(transform()).toBe(KEPT_TRANSFORM));

    view.rerender(canvas({ nodes: [A, B], failingOnly: true }));
    await waitFor(() => expect(screen.queryByRole("button", { name: /^c\b/ })).not.toBeInTheDocument());

    expect(transform()).toBe(KEPT_TRANSFORM);
  });

  it("keeps the zoom and pan when a node is expanded", async () => {
    keepView(SUBJECT, KEPT);
    const view = render(canvas());
    await waitFor(() => expect(transform()).toBe(KEPT_TRANSFORM));

    view.rerender(canvas({ expanded: new Set([A.unique_id]) }));
    await screen.findByRole("button", { name: /^id\b/ });

    expect(transform()).toBe(KEPT_TRANSFORM);
  });

  it("restores the kept view when Lineage is opened again on the same subject", async () => {
    keepView(SUBJECT, KEPT);

    render(canvas());

    await waitFor(() => expect(transform()).toBe(KEPT_TRANSFORM));
  });

  it("fits again for a different subject (another mode, domain or table path)", async () => {
    keepView(SUBJECT, KEPT);
    const view = render(canvas());
    await waitFor(() => expect(transform()).toBe(KEPT_TRANSFORM));

    view.rerender(canvas({ nodes: [A, B, C], subject: "+c+" }));
    await screen.findByRole("button", { name: /^c\b/ });

    await waitFor(() => expect(transform()).not.toBe(KEPT_TRANSFORM));
    expect(transform()).not.toMatch(/translate\(0px,0px\)/);
  });

  it("fits again when the kept view shows no node", async () => {
    keepView(SUBJECT, { x: -50_000, y: -50_000, zoom: 1 });

    render(canvas());
    await buttons();

    await waitFor(() => expect(transform()).not.toMatch(/-50000/));
    expect(transform()).not.toMatch(/translate\(0px,0px\)/);
  });

  it("does not use localStorage", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");

    render(canvas());
    await buttons();

    expect(setItem).not.toHaveBeenCalled();
    expect(orderNodes).toHaveBeenCalled();
  });
});
