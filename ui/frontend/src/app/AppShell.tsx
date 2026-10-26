import { AppShell as MantineAppShell, NavLink, Title } from "@mantine/core";
import { NavLink as RouterLink, Outlet } from "react-router-dom";

import { HealthBanner } from "./HealthBanner";
import { NAV_ITEMS } from "./navItems";

// A narrow rail: the screens' labels are single words, and the Explorer needs the width.
const NAV_WIDTH = 140;
const HEADER_HEIGHT = 56;

export function AppShell() {
  return (
    <MantineAppShell header={{ height: HEADER_HEIGHT }} navbar={{ width: NAV_WIDTH, breakpoint: 0 }} padding="md">
      <MantineAppShell.Header px="md" style={{ display: "flex", alignItems: "center" }}>
        <Title order={4}>SQL Sherpa</Title>
      </MantineAppShell.Header>
      <MantineAppShell.Navbar p="xs" component="nav">
        {NAV_ITEMS.map((item) => (
          <NavLink key={item.path} component={RouterLink} to={item.path} label={item.label} />
        ))}
      </MantineAppShell.Navbar>
      <MantineAppShell.Main>
        <HealthBanner />
        <Outlet />
      </MantineAppShell.Main>
    </MantineAppShell>
  );
}
