import { screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HealthBanner } from "../src/app/HealthBanner";
import { type Health, health } from "./fixtures/api";
import { renderWithProviders } from "./fixtures/render";

const useHealth = vi.fn<() => { data: Health | undefined; isError?: boolean }>();
vi.mock("../src/api/queries", () => ({ useHealth: () => useHealth() }));

const DOWN_AND_MISSING = health({ database: "down", artifacts: { status: "missing", generated_at: null } });

function renderBanner() {
  return renderWithProviders(<HealthBanner />);
}

describe("HealthBanner", () => {
  beforeEach(() => useHealth.mockReset());

  it("shows one banner per down dependency and none when healthy", () => {
    useHealth.mockReturnValue({ data: health() });
    const healthy = renderBanner();
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
    healthy.unmount();

    useHealth.mockReturnValue({ data: health({ database: "down" }) });
    const databaseDown = renderBanner();
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByText(/database/i)).toBeInTheDocument();
    databaseDown.unmount();

    useHealth.mockReturnValue({ data: DOWN_AND_MISSING });
    renderBanner();
    expect(screen.getAllByRole("alert")).toHaveLength(2);
  });

  it("a down database and missing dbt artifacts each get their own banner", () => {
    useHealth.mockReturnValue({ data: DOWN_AND_MISSING });

    renderBanner();

    const [database, artifacts] = screen.getAllByRole("alert") as [HTMLElement, HTMLElement];
    expect(database).toHaveTextContent(/database/i);
    expect(artifacts).toHaveTextContent(/dbt artifacts missing/);
  });

  it("shows nothing while the health check has not answered", () => {
    useHealth.mockReturnValue({ data: undefined });

    renderBanner();

    expect(screen.queryAllByRole("alert")).toHaveLength(0);
  });

  it("shows a banner when the server itself is unreachable", () => {
    useHealth.mockReturnValue({ data: undefined, isError: true });

    renderBanner();

    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });
});
