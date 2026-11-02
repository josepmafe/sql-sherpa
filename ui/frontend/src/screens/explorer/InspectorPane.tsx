import { Box, Group, ScrollArea, Stack, Title } from "@mantine/core";
import { IconInfoCircle } from "@tabler/icons-react";
import { type RefObject, useEffect } from "react";

import { CollapseToggle, usePaneCollapse } from "../../components/panes";
import { Inspector } from "./Inspector";

export const INSPECTOR_PANE = "inspector";
export const INSPECTOR_TITLE = "Table details";
const OVERLAY_WIDTH = 320;
const OVERLAY_Z_INDEX = 20;
const ICON_SIZE = 18;

type PaneProps = {
  table: string | undefined;
  // Counts the clicks on a table: a folded Inspector opens for each one.
  inspectCount: number;
};

/** The Inspector as the Explorer's right-hand pane (from 1280 px); folded, it is a strip with its icon. */
export function InspectorPane({ table, inspectCount }: PaneProps) {
  const { collapsed, toggle, setOpen, toggleRef } = usePaneCollapse(INSPECTOR_PANE);

  // The click that selects a table also opens the Inspector. Only the count is a dependency: toggling must not rerun it.
  useEffect(() => {
    if (inspectCount > 0 && collapsed) setOpen(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inspectCount]);

  if (collapsed) {
    return (
      <Box component="aside" aria-label={INSPECTOR_TITLE} h="100%">
        <Stack align="center" gap="xs" pt="xs">
          <CollapseToggle
            collapsed
            onToggle={toggle}
            foldLabel="Collapse the Inspector"
            openLabel="Open the Inspector"
            side="right"
            buttonRef={toggleRef}
          />
          <IconInfoCircle size={ICON_SIZE} aria-hidden />
        </Stack>
      </Box>
    );
  }
  return (
    <Box component="aside" aria-label={INSPECTOR_TITLE} h="100%">
      <ScrollArea h="100%" offsetScrollbars="y">
        <Stack gap="sm">
          <Group justify="space-between" wrap="nowrap" gap="xs">
            <Title order={2} size="h6">
              {INSPECTOR_TITLE}
            </Title>
            <CollapseToggle
              collapsed={false}
              onToggle={toggle}
              foldLabel="Collapse the Inspector"
              openLabel="Open the Inspector"
              side="right"
              buttonRef={toggleRef}
            />
          </Group>
          <Inspector table={table} />
        </Stack>
      </ScrollArea>
    </Box>
  );
}

type OverlayProps = {
  table: string | undefined;
  onClose: () => void;
  headingRef: RefObject<HTMLHeadingElement | null>;
};

/** Below 1280 px the Inspector takes no width: it opens over the right edge of the Explorer. */
export function InspectorOverlay({ table, onClose, headingRef }: OverlayProps) {
  return (
    <Box
      component="aside"
      aria-label={INSPECTOR_TITLE}
      bg="var(--mantine-color-body)"
      p="sm"
      style={{
        position: "absolute",
        top: 0,
        right: 0,
        bottom: 0,
        width: `min(${OVERLAY_WIDTH}px, 100%)`,
        zIndex: OVERLAY_Z_INDEX,
        overflowY: "auto",
        borderLeft: "1px solid var(--mantine-color-default-border)",
        boxShadow: "var(--mantine-shadow-md)",
      }}
    >
      <Stack gap="sm">
        <Group justify="space-between" wrap="nowrap" gap="xs">
          <Title order={2} size="h6" ref={headingRef} tabIndex={-1}>
            {INSPECTOR_TITLE}
          </Title>
          <CollapseToggle
            collapsed={false}
            onToggle={onClose}
            foldLabel="Close the table details"
            openLabel="Open the table details"
            side="right"
          />
        </Group>
        <Inspector table={table} />
      </Stack>
    </Box>
  );
}
