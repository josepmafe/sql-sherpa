import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HealthBanner } from "../src/app/HealthBanner";

type Health = { database: "ok" | "down"; artifacts: { status: "ok" | "missing"; generated_at: string | null } };

const useHealth = vi.fn<() => { data: Health | undefined; isError?: boolean }>();
vi.mock("../src/api/queries", () => ({ useHealth: () => useHealth() }));

const HEALTHY: Health = { database: "ok", artifacts: { status: "ok", generated_at: "2026-10-24T10:00:00Z" } };

function renderBanner() {
  return render(
    <MantineProvider>
      <HealthBanner />
    </MantineProvider>,
  );
}

describe("HealthBanner", () => {
  beforeEach(() => useHealth.mockReset());

  it("shows one banner per down dependency and none when healthy", () => {
    useHealth.mockReturnValue({ data: HEALTHY });
    const healthy = renderBanner();
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
    healthy.unmount();

    useHealth.mockReturnValue({ data: { ...HEALTHY, database: "down" } });
    const databaseDown = renderBanner();
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.getByText(/database/i)).toBeInTheDocument();
    databaseDown.unmount();

    useHealth.mockReturnValue({ data: { database: "down", artifacts: { status: "missing", generated_at: null } } });
    renderBanner();
    expect(screen.getAllByRole("alert")).toHaveLength(2);
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
