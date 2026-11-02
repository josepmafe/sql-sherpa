import { ActionIcon, AppShell as MantineAppShell, Group, NavLink, Title } from "@mantine/core";
import { IconPin, IconPinFilled } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { NavLink as RouterLink, Outlet } from "react-router-dom";

import { CollapseToggle, isFreeEscape, readLayout, usePeek, writeLayout } from "../components/panes";
import { HealthBanner } from "./HealthBanner";
import { NAV_ITEMS } from "./navItems";

// A narrow rail: the screens' labels are single words, and the Explorer needs the width. Collapsed it is the icons (UI spec §6.1).
export const NAV_WIDTH = 140;
export const NAV_COLLAPSED_WIDTH = 56;
const HEADER_HEIGHT = 56;
const LAYOUT_ID = "shell";
const NAV_PANE = "nav";
const ICON_SIZE = 20;
const NAV_LIMITS = { [NAV_PANE]: { min: NAV_COLLAPSED_WIDTH, max: NAV_WIDTH } };

export function AppShell() {
  const [collapsed, setCollapsed] = useState(() => readLayout(LAYOUT_ID, NAV_LIMITS)?.collapsed[NAV_PANE] === true);
  const [pinned, setPinned] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const { peeking, peekProps } = usePeek(collapsed);
  const open = collapsed && (pinned || (peeking && !dismissed));
  const showLabels = !collapsed || open;

  // A dismissed peek stays dismissed until the pointer and the focus have left, so the next peek can open.
  const [wasPeeking, setWasPeeking] = useState(peeking);
  if (wasPeeking !== peeking) {
    setWasPeeking(peeking);
    if (!peeking) setDismissed(false);
  }

  useEffect(() => {
    if (!open) return;
    // The outermost layer, but the topmost on screen: it takes the Escape in the capture phase.
    const close = (event: KeyboardEvent) => {
      if (!isFreeEscape(event)) return;
      event.preventDefault();
      setPinned(false);
      setDismissed(true);
    };
    document.addEventListener("keydown", close, true);
    return () => document.removeEventListener("keydown", close, true);
  }, [open]);

  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    setPinned(false);
    setDismissed(false);
    writeLayout(LAYOUT_ID, { sizes: {}, collapsed: { [NAV_PANE]: next } });
  };

  return (
    <MantineAppShell
      header={{ height: HEADER_HEIGHT }}
      navbar={{ width: collapsed ? NAV_COLLAPSED_WIDTH : NAV_WIDTH, breakpoint: 0 }}
      padding="md"
    >
      <MantineAppShell.Header px="md" style={{ display: "flex", alignItems: "center" }}>
        <Title order={4}>SQL Sherpa</Title>
      </MantineAppShell.Header>
      {/* A peek widens this element only, so Main keeps the offset the collapsed rail gave it. */}
      <MantineAppShell.Navbar
        p="xs"
        component="nav"
        aria-label="Main"
        style={{ width: open ? NAV_WIDTH : undefined, display: "flex", flexDirection: "column" }}
        {...peekProps}
      >
        <div style={{ flex: 1 }}>
          {NAV_ITEMS.map(({ path, label, icon: Icon }) => (
            <NavLink
              key={path}
              component={RouterLink}
              to={path}
              aria-label={label}
              onClick={(event) => {
                // A mouse click (detail > 0) ends the peek; a key press (detail 0) keeps the focus-held peek.
                if (event.detail === 0) return;
                event.currentTarget.blur();
                setDismissed(true);
              }}
              label={showLabels ? label : undefined}
              leftSection={<Icon size={ICON_SIZE} />}
            />
          ))}
        </div>
        <Group gap="xs" justify={showLabels ? "space-between" : "center"} wrap="nowrap">
          <CollapseToggle
            collapsed={collapsed}
            onToggle={toggle}
            foldLabel="Collapse the navigation"
            openLabel="Open the navigation"
          />
          {open && (
            <ActionIcon
              variant="subtle"
              color="gray"
              size="sm"
              aria-pressed={pinned}
              aria-label={pinned ? "Unpin the navigation" : "Pin the navigation open"}
              onClick={() => setPinned(!pinned)}
            >
              {pinned ? <IconPinFilled size={16} /> : <IconPin size={16} />}
            </ActionIcon>
          )}
        </Group>
      </MantineAppShell.Navbar>
      {/* The viewport's height, split between the banner and the screen (which takes the rest). */}
      <MantineAppShell.Main style={{ display: "flex", flexDirection: "column", height: "100dvh" }}>
        <HealthBanner />
        <Outlet />
      </MantineAppShell.Main>
    </MantineAppShell>
  );
}
