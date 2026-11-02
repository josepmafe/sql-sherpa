import { MantineProvider } from "@mantine/core";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { CollapseToggle } from "../../src/components/panes";

function Harness({ startCollapsed }: { startCollapsed: boolean }) {
  const [collapsed, setCollapsed] = useState(startCollapsed);
  return (
    <MantineProvider env="test">
      <CollapseToggle
        collapsed={collapsed}
        onToggle={() => setCollapsed((current) => !current)}
        foldLabel="Collapse the table tree"
        openLabel="Open the table tree"
      />
    </MantineProvider>
  );
}

describe("CollapseToggle", () => {
  it("is expanded and named by foldLabel while the pane is open", () => {
    render(<Harness startCollapsed={false} />);

    expect(screen.getByRole("button", { name: "Collapse the table tree" })).toHaveAttribute("aria-expanded", "true");
  });

  it("is not expanded and named by openLabel while the pane is folded", () => {
    render(<Harness startCollapsed />);

    expect(screen.getByRole("button", { name: "Open the table tree" })).toHaveAttribute("aria-expanded", "false");
  });

  it.each([["{Enter}"], [" "]])("toggles on the %j key", async (key) => {
    const user = userEvent.setup();
    render(<Harness startCollapsed={false} />);

    screen.getByRole("button").focus();
    await user.keyboard(key);

    expect(screen.getByRole("button", { name: "Open the table tree" })).toHaveAttribute("aria-expanded", "false");
  });
});
